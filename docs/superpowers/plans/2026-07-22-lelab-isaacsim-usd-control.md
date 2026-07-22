# LeLab-Controlled SuperArm Isaac Sim USD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the LeLab website, manual/SO-101 teleoperation, recording, and future ACT/VLA policies control the validated SuperArm + AmazingHand Isaac Sim 6.0 USD with the existing six logical controls while Isaac owns the 13 physical joints.

**Architecture:** Keep Isaac Sim out of the FastAPI process. A managed Isaac Sim 6.0 container opens the verified USD and exposes a versioned, token-authenticated JSON Lines control bridge on localhost; a host-side LeLab runtime adapter owns or attaches to that bridge. LeLab preserves its canonical five-arm-plus-one-grasp action, maps all commands by joint name rather than array order, and reports 13-joint measured/target telemetry back to the current website and LeRobot interfaces.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, LeRobot 0.6, Isaac Sim 6.0 Docker, OpenUSD/Isaac `Articulation`, stdlib sockets/selectors/subprocess/zipfile, React/TypeScript/Vite/Vitest, pytest, Ruff, Pillow.

---

## 1. Requirements Summary

### Target result

1. `runtime="isaac_sim"` is a first-class LeLab SuperArm session next to the existing `mujoco` and `hybrid_serial` modes (`lelab/superarm/api.py:38-43`, `lelab/superarm/service.py:266-306`).
2. LeRobot and VLA-facing code continues to expose exactly six features: five `joint_rev_N.pos` values and `amazinghand_motion.pos` (`lelab/superarm/actions.py:10-12`, `lelab/superarm/robot.py:41-50`).
3. Isaac receives and reports exactly 13 named joints: five arm and eight hand joints (`isaacsim_validation/contracts.py:8-12`, `isaacsim_validation/contracts.py:38-47`).
4. The supplied distribution is verified before extraction and the runtime opens its declared entrypoint, `usd/superarm_amazinghand/superarm_amazinghand.usda`; no path is hard-coded to one developer checkout (`isaacsim_validation/export_superarm_usd_distribution.py:15-23`).
5. Website arm sliders, individual hand controls, open/half-close/close motions, poses, sequences, emergency stop, and live-command timeout work through the Isaac adapter without changing MuJoCo behavior (`lelab/superarm/service.py:332-402`, `frontend/src/pages/SuperArm.tsx:416-548`).
6. Add a LeRobot `superarm_isaac` robot config so manual input, SO-101 input, data recording, and future ACT/VLA inference all see a six-wide action/observation vector (`lelab/teleoperate.py:194-267`, `lelab/record.py:223-269`).
7. Produce fresh numeric, screenshot, and GIF evidence from the managed LeLab-to-Isaac path. The evidence must be reviewed by a verifier subagent before the branch can be merged.
8. Append each completed repair/feature slice to `isaacsim_usd_engineering_log.md` and the durable `omx_wiki` page, and commit every verified slice separately.

### Explicit non-goals

- Do not import or integrate `AmazingHandControl`, `rustypot`, DM4340P CAN, CAN-FD, or any other physical-hardware transport into the Isaac backend.
- Do not add ROS 2. LeLab talks directly to the local Isaac bridge; ROS can remain a separate future integration.
- Do not replace, remove, or silently change `superarm_mujoco`; its tests remain regression gates.
- Do not claim that the 88 passive linkage followers are live PhysX bodies. The reusable USD has 13 physical joints; the detailed 88-part follower set remains snapshot/evidence-only.
- Do not promise continuous Isaac viewport streaming in this slice. The website gets telemetry plus an explicit on-demand capture; `/api/superarm/video` remains MuJoCo-only.
- Do not claim trained-policy, contact, grasp-retention, or real-hardware success.

### Stop condition

Stop only after the feature branch has: unit/integration tests passing, the real Isaac Sim 6.0 E2E report meeting the numeric thresholds below, nonblank open/half/close images and a reviewed GIF, no MuJoCo regressions, updated engineering records, one commit per verified slice, independent verifier approval, and a successful push. Merge to the fork's `main` only after those gates pass.

## 2. Verified Baseline and Missing Work

| Area | Already verified | Missing implementation |
| --- | --- | --- |
| Logical action | Six-value validation and grasp quantization exist in `lelab/superarm/actions.py:10-46`. | Route the same logical action atomically to named Isaac targets. |
| Isaac contract | `expand_logical_action()` maps six values to 13 named targets in `isaacsim_validation/contracts.py:38-47`. | Make that contract available to installed LeLab code and validate exact-name target payloads. |
| USD | The merged exporter requires one articulation, 13 physical joints, six logical actions, and passing arm/hand motion in `isaacsim_validation/export_superarm_usd_distribution.py:43-74`. | Verify/extract the distribution at session startup and open the clean entrypoint in a long-lived runtime. |
| Isaac control | Validation code creates `World`, timeline, and `Articulation`, then commands named joints in `isaacsim_validation/run_validation.py:174-223`. | Convert the one-shot validator into a separate, long-lived bridge without placing Isaac imports in FastAPI. |
| Service/API | Session safety, action, timeout, emergency stop, and telemetry are reusable in `lelab/superarm/service.py:266-402`. | Add a runtime factory/config and Isaac-specific capability/capture behavior. |
| LeRobot | `SuperArmMujocoRobot` already exposes the correct six policy features in `lelab/superarm/robot.py:19-50`. | Register `superarm_isaac` and return positive URDF/Isaac hand positions without the MuJoCo sign projection at `lelab/superarm/robot.py:123-138`. |
| Website | `/superarm` already contains arm, hand, pose, sequence, and telemetry controls in `frontend/src/pages/SuperArm.tsx:416-570`. | Add Isaac selection/configuration and replace the hard-coded MuJoCo video panel at `frontend/src/pages/SuperArm.tsx:446-467` when Isaac is selected. |
| Recording | SuperArm recording keeps a six-wide LeRobot contract in `lelab/record.py:223-269`. | Select the Isaac robot config and propagate distribution/bridge settings. |

Current baseline test evidence from discovery:

```text
51 passed, 5 warnings
```

```bash
uv run --extra test pytest -q \
  tests/test_superarm_mujoco_focus.py \
  tests/test_superarm_isaac_contract.py \
  tests/test_superarm_hardware_protocol.py \
  tests/test_server.py -q
```

## 3. Architecture Decision

### Selected process boundary

```text
Browser / LeRobot policy / SO-101 leader
                 |
                 | six logical values or website arm/hand controls
                 v
        LeLab FastAPI + SuperArmService
                 |
                 | localhost JSONL, exact named target map
                 v
      Managed Isaac Sim 6.0 bridge container
                 |
                 | Articulation set_dof_position_targets by name
                 v
   superarm_amazinghand.usda: 5 arm + 8 hand DOFs
```

The host LeLab process does not import `isaacsim`, `omni`, or `pxr`. The managed bridge imports `SimulationApp` before every Isaac/Omniverse module, matching the proven ordering at `isaacsim_validation/run_validation.py:21-49`.

### Bridge protocol

Every newline-delimited request is at most 64 KiB and has:

```json
{
  "schema": "lelab.superarm.isaac_bridge/v1",
  "request_id": "uuid",
  "token": "session-secret",
  "op": "hello"
}
```

Supported operations:

| Operation | Request data | Success response |
| --- | --- | --- |
| `hello` | none | Isaac version, prim path, 13 DOF names, logical width 6, capture support, sequence |
| `command` | `targets`: exactly 13 finite named radians | accepted target map and incremented command sequence |
| `observe` | none | measured/target/error for all 13 joints, timestamp, physics step, command sequence |
| `hold` | none | current measured positions copied into all targets |
| `capture` | `view`: `whole` or `hand`, `name`: safe filename stem | PNG metadata and a run-directory-relative artifact path; the host resolves and allowlists it beneath its own mounted run directory |
| `shutdown` | none | acknowledgement followed by clean timeline/app shutdown |

All failures return the same schema with `ok=false`, an error `code`, and a bounded `message`. Missing/extra joints, non-finite values, wrong tokens, stale schema versions, multiple active clients, invalid articulation state, and oversized frames fail closed.

### Runtime ownership

- **Managed mode (default):** LeLab validates/extracts the ZIP, creates a `0600` token file, launches the wrapper with `subprocess.Popen([...], shell=False, start_new_session=True)`, redirects Isaac output to session log files, waits up to `ISAAC_SIM_STARTUP_TIMEOUT_S` (default 180 s) for `hello`, owns shutdown, and removes only its own container/run state. The wrapper mounts the packaged/source `isaacsim_validation` module read-only, sets `PYTHONPATH`, and runs `/isaac-sim/python.sh -m isaacsim_validation.control_bridge`.
- **External mode:** LeLab connects to `SUPERARM_ISAAC_HOST`, `SUPERARM_ISAAC_PORT`, and a server-side `SUPERARM_ISAAC_BRIDGE_TOKEN`; disconnect never kills that externally owned process.
- The bridge binds `127.0.0.1` by default. A non-loopback bind is rejected by the managed launcher in this scope.

### Coordinate contract

- Commands are indexed by name, never by the array order reported by Isaac or the ZIP manifest.
- Arm targets remain radians in `joint_rev_1` through `joint_rev_5`.
- Website hand degrees use the existing mechanical mapping and then the existing MuJoCo-to-URDF sign correction: `mujoco_hand_to_urdf(named_hand_to_mujoco(hand_deg))` (`lelab/superarm/mapping.py:48-61`, `lelab/superarm/mapping.py:89-114`).
- LeRobot grasp `0.0/0.5/1.0` uses the positive Isaac targets in `grasp_to_urdf_targets()` (`isaacsim_validation/contracts.py:26-35`).
- `superarm_isaac` rejects a 13-wide policy action. The 13 values are an internal runtime target/state shape, not a policy interface.

## 4. File Responsibility Map

### New files

- `lelab/superarm/isaac_distribution.py` — safe distribution validation, checksum verification, cache extraction, and entrypoint resolution; no Isaac imports.
- `isaacsim_validation/bridge_protocol.py` — shared pure-stdlib JSONL schema/codec/server validation; no LeLab, LeRobot, Isaac, or Omniverse imports.
- `lelab/superarm/isaac_protocol.py` — bounded host socket client and error types importing the shared codec; no Isaac imports.
- `lelab/superarm/isaac_runtime.py` — host-side managed/external bridge adapter implementing the current SuperArm runtime surface.
- `lelab/superarm/isaac_robot.py` — LeRobot `superarm_isaac` config and robot class with six policy features.
- `lelab/superarm/data/superarm_isaac.yaml` — website/manual/SO-101 logical mapping and Isaac runtime defaults.
- `isaacsim_validation/control_bridge.py` — Isaac-only main loop, articulation validation/control, observation, hold, and capture.
- `isaacsim_validation/run_isaacsim60_control_bridge.sh` — deterministic Docker/NGC launcher with mounts, cache volumes, host networking, and cleanup trap.
- `isaacsim_validation/run_lelab_isaac_e2e.py` — full API-driven acceptance runner and report/GIF producer.
- `tests/test_superarm_isaac_distribution.py` — malicious archive, manifest, checksum, and extraction-cache tests.
- `tests/test_superarm_isaac_protocol.py` — protocol framing/schema/client tests.
- `tests/test_superarm_isaac_runtime.py` — fake-bridge lifecycle, mapping, timeout, hold, ownership, and capture tests.
- `tests/test_superarm_isaac_lerobot_backend.py` — six-wide LeRobot/recording contract tests.
- `frontend/src/lib/superarmRuntime.ts` — shared backend/runtime predicates and UI capability helpers.
- `frontend/src/lib/superarmRuntime.test.ts` — Vitest coverage for runtime-specific behavior.
- `omx_wiki/lelab-isaac-sim-superarm-control.md` — durable architecture, commands, evidence, and proof boundary.

### Modified files

- `pyproject.toml:25-42,45-49` — ship the lightweight `isaacsim_validation` package/scripts/data without adding a runtime dependency.
- `isaacsim_validation/contracts.py:8-47` — exact physical-target validation and public constants used by host and bridge.
- `isaacsim_validation/README.md` — managed control commands and proof boundaries.
- `lelab/superarm/actions.py:10-46` — add an Isaac logical expansion wrapper while retaining the MuJoCo arm/hand command function.
- `lelab/superarm/transports.py:44-82` — define the concrete runtime protocol/atomic partial-command surface used by service, MuJoCo, and Isaac without leaving either adapter abstract.
- `lelab/superarm/service.py:26-70,266-402` — runtime factory, Isaac capabilities/session fields, atomic logical action, capture, and generic runtime typing.
- `lelab/superarm/api.py:38-43,287-320,419-434` — request schema, capture routes, and explicit MuJoCo-only continuous video error.
- `lelab/teleoperate.py:79-92,194-267,304-326,511-531` — factory-based SuperArm backend selection and accurate telemetry labels.
- `lelab/record.py:79-100,158-180,223-269` — construct `SuperArmIsaacRobotConfig` without changing six-wide dataset features.
- `lelab/manual_leader.py:88-193` — backend-aware positive Isaac hand targets and start request.
- `lelab/utils/config.py:332-399,515-540` — Isaac record fields, optional built-in Isaac record, and cleanliness checks.
- `lelab/server.py:1170-1190,1227-1291,1353-1398` — accept both SuperArm backends while leaving MJCF visual routes MuJoCo-only.
- `frontend/src/hooks/useRobots.ts:7-22` — type the Isaac record fields.
- `frontend/src/pages/SuperArm.tsx:40-105,150-190,315-470` — runtime selector/config, capability state, and on-demand capture panel.
- `frontend/src/pages/Landing.tsx:161-260` — treat both SuperArm backends as six-control recording targets and forward Isaac settings.
- `frontend/src/components/landing/RobotTile.tsx:45-49` — allow Manual Web Leader for both SuperArm backends.
- `frontend/src/components/landing/RecordingModal.tsx:87-99,148-180` — show the existing manual/SO-101 inputs for Isaac too.
- `frontend/src/pages/Recording.tsx:110-140` — recognize both SuperArm backends for manual actions.
- `frontend/src/pages/ManualLeader.tsx:145-170` — remove MuJoCo-only wording from generic SuperArm connection errors.
- `tests/test_superarm_dashboard.py:100-170,300-430` — Isaac API/service lifecycle and MuJoCo regression tests.
- `tests/test_teleoperate.py:172-285` — Isaac start/action/readback and backend dispatch tests.
- `tests/test_record.py:120-300` — Isaac config and six-wide dataset feature tests.
- `isaacsim_usd_engineering_log.md` — append-only implementation/verification entries.

## 5. Implementation Tasks

### Task 1: Lock the distribution and coordinate contracts

**Files:**
- Create: `lelab/superarm/isaac_distribution.py`
- Create: `tests/test_superarm_isaac_distribution.py`
- Modify: `isaacsim_validation/contracts.py:8-47`
- Modify: `lelab/superarm/actions.py:8-46`
- Modify: `pyproject.toml:45-49`
- Test: `tests/test_superarm_isaac_contract.py`

- [ ] **Step 1: Write failing archive and target-contract tests**

Cover a valid single-root ZIP, `../` traversal, absolute paths, symlinks, duplicate members, checksum mismatch, wrong schema, wrong `robot_contract`, absent entrypoint, extra/missing target names, NaN, and exact open/half/close values. The core assertions are:

```python
resolved = validate_and_extract_distribution(source_zip, cache_root=tmp_path / "cache")
assert resolved.entrypoint.name == "superarm_amazinghand.usda"
assert resolved.robot_contract == {
    "arm_dof_count": 5,
    "hand_dof_count": 8,
    "physical_dof_count": 13,
    "logical_action_width": 6,
    "articulation_root_count": 1,
}
assert validate_physical_targets(expand_logical_action([0, 0, 0, 0, 0, 1]))[
    "finger1_motor2"
] == pytest.approx(1.10)
with pytest.raises(ValueError, match="exactly 13"):
    validate_physical_targets({"joint_rev_1": 0.0})
```

- [ ] **Step 2: Run the focused tests and confirm they fail for missing APIs**

```bash
uv run --extra test pytest -q \
  tests/test_superarm_isaac_distribution.py \
  tests/test_superarm_isaac_contract.py
```

Expected: failure naming `validate_and_extract_distribution` and `validate_physical_targets`.

- [ ] **Step 3: Implement safe, deterministic extraction**

Use `zipfile.ZipFile`, `PurePosixPath`, `hashlib.sha256`, and a temporary directory followed by `os.replace`. The public surface is fixed as:

```python
@dataclass(frozen=True)
class IsaacDistribution:
    archive_sha256: str
    root: Path
    entrypoint: Path
    manifest: dict[str, Any]
    robot_contract: dict[str, int]

def validate_and_extract_distribution(
    source_zip: str | Path,
    *,
    cache_root: str | Path | None = None,
) -> IsaacDistribution: ...
```

Reject entries unless every member is beneath one archive root, is not a link, is unique after POSIX normalization, and matches `SHA256SUMS`. Require schema `superarm.isaac_sim.usd_distribution/v1`, the five/eight/13/six/one contract, and an entrypoint that remains inside the extracted root.

- [ ] **Step 4: Add exact target validation and a LeLab wrapper**

`isaacsim_validation/contracts.py` exposes:

```python
PHYSICAL_JOINTS = (*ARM_JOINTS, *HAND_JOINTS)

def validate_physical_targets(targets: Mapping[str, float]) -> dict[str, float]:
    if set(targets) != set(PHYSICAL_JOINTS):
        raise ValueError("physical targets must contain exactly 13 expected joints")
    values = {name: float(targets[name]) for name in PHYSICAL_JOINTS}
    if not all(math.isfinite(value) for value in values.values()):
        raise ValueError("physical targets must be finite")
    return values
```

`lelab/superarm/actions.py` imports and wraps `expand_logical_action()` as `action_to_isaac_targets(values)` after `normalize_superarm_action(values)`. Keep `action_to_runtime_commands()` unchanged for MuJoCo.

- [ ] **Step 5: Ship the lightweight validation package**

Extend setuptools discovery to include `isaacsim_validation*` and package the shell scripts plus `data/*.json`. Do not add Isaac or Omniverse to `project.dependencies`.

- [ ] **Step 6: Run tests and static checks**

```bash
uv run --extra test pytest -q \
  tests/test_superarm_isaac_distribution.py \
  tests/test_superarm_isaac_contract.py \
  tests/test_export_superarm_usd_distribution.py
uv run ruff check lelab/superarm/isaac_distribution.py \
  lelab/superarm/actions.py isaacsim_validation/contracts.py \
  tests/test_superarm_isaac_distribution.py tests/test_superarm_isaac_contract.py
```

Expected: all pass and Ruff reports no errors.

- [ ] **Step 6a: Prove installed-wheel package contents**

Build a wheel into a temporary directory, install it into an isolated temporary virtual environment, import `isaacsim_validation`, and assert `Path(isaacsim_validation.__file__).parent` contains `run_isaacsim60_validation.sh` plus `data/amazinghand_passive_linkage_keyframes.json`. This proves the future launcher can derive and mount the same package root from source or an installed wheel.

- [ ] **Step 7: Append the engineering-log entry and commit**

Record the duplicate-mapping risk, malicious-archive tests, chosen single source, test command/result, and proof boundary in `isaacsim_usd_engineering_log.md`.

```bash
git add pyproject.toml lelab/superarm/actions.py lelab/superarm/isaac_distribution.py \
  isaacsim_validation/contracts.py tests/test_superarm_isaac_contract.py \
  tests/test_superarm_isaac_distribution.py isaacsim_usd_engineering_log.md
git commit -m "feat(isaac): validate USD distribution and 13-joint targets"
```

### Task 2: Define and test the local bridge protocol

**Files:**
- Create: `isaacsim_validation/bridge_protocol.py`
- Create: `lelab/superarm/isaac_protocol.py`
- Create: `tests/test_superarm_isaac_protocol.py`

- [ ] **Step 1: Write failing framing, schema, timeout, and error tests**

Use `socket.socketpair()` for success/error round trips and a tiny TCP fake for timeout/reconnect cases. Assert request IDs match, payloads over 64 KiB are rejected before send, unknown operations fail, tokens are never printed in exceptions, and a partial line cannot be parsed as a complete message.

```python
client = IsaacBridgeClient("127.0.0.1", server.port, token="secret", timeout_s=0.2)
hello = client.request("hello")
assert hello["physical_dof_count"] == 13
with pytest.raises(IsaacBridgeError, match="schema_mismatch"):
    client.request("unknown")
assert "secret" not in repr(client)
```

- [ ] **Step 2: Confirm the focused test fails**

```bash
uv run --extra test pytest -q tests/test_superarm_isaac_protocol.py
```

Expected: import failure for `isaacsim_validation.bridge_protocol` and `lelab.superarm.isaac_protocol`.

- [ ] **Step 3: Implement the protocol without third-party dependencies**

Expose the schema/codec in `isaacsim_validation.bridge_protocol` and the client in `lelab.superarm.isaac_protocol`:

```python
SCHEMA = "lelab.superarm.isaac_bridge/v1"
MAX_MESSAGE_BYTES = 65_536

def encode_request(op: str, *, request_id: str, token: str, **payload: Any) -> bytes: ...
def decode_message(raw: bytes) -> dict[str, Any]: ...

class IsaacBridgeError(RuntimeError):
    code: str

class IsaacBridgeClient:
    def connect(self) -> dict[str, Any]: ...
    def command(self, targets: Mapping[str, float]) -> dict[str, Any]: ...
    def observe(self) -> dict[str, Any]: ...
    def hold(self) -> dict[str, Any]: ...
    def capture(self, view: Literal["whole", "hand"], name: str) -> dict[str, Any]: ...
    def shutdown(self) -> dict[str, Any]: ...
    def close(self) -> None: ...
```

Use one lock around request/write/read so concurrent telemetry and action calls cannot interleave frames. Never automatically retry `command`, `hold`, or `shutdown`; a caller must reconnect and reconcile observed state first.

- [ ] **Step 4: Run tests and commit**

```bash
uv run --extra test pytest -q tests/test_superarm_isaac_protocol.py
uv run ruff check isaacsim_validation/bridge_protocol.py lelab/superarm/isaac_protocol.py tests/test_superarm_isaac_protocol.py
git add isaacsim_validation/bridge_protocol.py lelab/superarm/isaac_protocol.py tests/test_superarm_isaac_protocol.py \
  isaacsim_usd_engineering_log.md
git commit -m "feat(isaac): add versioned localhost control protocol"
```

### Task 3: Build the long-lived Isaac Sim 6.0 bridge

**Files:**
- Create: `isaacsim_validation/control_bridge.py`
- Create: `isaacsim_validation/run_isaacsim60_control_bridge.sh`
- Modify: `isaacsim_validation/README.md`
- Test: `tests/test_superarm_isaac_protocol.py`

- [ ] **Step 1: Add source-level guard tests before the Isaac implementation**

The tests read the script and assert `SimulationApp` construction precedes `omni`, `pxr`, and `isaacsim.core` imports; the launcher must use `--network host`, a read-only asset mount, `ACCEPT_EULA=Y`, the fixed image default `nvcr.io/nvidia/isaac-sim:6.0.0`, a read-only module mount derived by the host from `Path(isaacsim_validation.__file__).parent`, `PYTHONPATH`, `/isaac-sim/python.sh -m isaacsim_validation.control_bridge`, and a cleanup trap. These tests protect the runtime boundary even on hosts without Isaac.

- [ ] **Step 2: Implement startup and articulation validation**

Parse arguments before Isaac imports, then create `SimulationApp`, open the extracted USD, create `World`, play the timeline, discover the stage default prim or the unique prim carrying `ArticulationRootAPI`, and create `Articulation(discovered_path)` using the proven sequence at `isaacsim_validation/run_validation.py:154-200`. Never hard-code `/superarm_amazinghand`. Require:

```python
expected = set(PHYSICAL_JOINTS)
actual = set(art.dof_names)
if art.num_dofs != 13 or actual != expected:
    raise RuntimeError(
        f"Isaac articulation contract mismatch: count={art.num_dofs}, "
        f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
    )
```

The name-to-index map is built once from `art.dof_names`; array ordering is never treated as the contract.

- [ ] **Step 3: Implement the main-thread physics/network loop**

Use `selectors.DefaultSelector` with nonblocking sockets. The single main loop performs one `world.step(render=False)`, services complete JSONL messages, and publishes no Isaac API object to a worker thread. `command` calls `set_dof_position_targets`; `observe` reads `get_dof_positions`; `hold` reads positions and writes the same values as targets.

State has this fixed shape:

```python
{
    "runtime": "isaac_sim",
    "connected": True,
    "timestamp": time.time(),
    "physics_step": step_count,
    "command_sequence": command_sequence,
    "arm": {name: {"position": p, "target": t, "error": t - p, "moving": abs(t-p) > 0.01}},
    "hand": {name: {"position": p, "target": t, "error": t - p, "moving": abs(t-p) > 0.01}},
}
```

- [ ] **Step 4: Add on-demand capture only**

Adapt the proven camera framing and Replicator writer pattern from `isaacsim_validation/render_physics_snapshots.py:170-230`. `capture` renders either the full root or the unique `r_wrist_interface` subtree into the mounted run directory, validates that the PNG is nonblank with `image_has_detail`, and returns only its relative path/size/camera metadata. Warm rendering explicitly and detach/destroy the writer, render product, camera graph, and temporary layer after every capture. Add a repeated-capture followed by command test to catch resource accumulation or timeline corruption. It does not start a video loop.

- [ ] **Step 5: Implement deterministic launcher cleanup**

The wrapper accepts `--asset-root`, `--entrypoint`, `--run-dir`, `--host`, `--port`, and `--token-file`; validates all resolved paths; mounts the asset root and packaged/source `isaacsim_validation` package read-only; mounts the run directory read-write; sets `PYTHONPATH`; launches the bridge with `python.sh -m`; uses the existing Isaac cache volumes; generates a unique container name; writes startup/container metadata and log files; and removes only that container in `trap cleanup EXIT INT TERM`.

- [ ] **Step 6: Run host-safe tests and shell validation**

```bash
uv run --extra test pytest -q tests/test_superarm_isaac_protocol.py
bash -n isaacsim_validation/run_isaacsim60_control_bridge.sh
uv run ruff check isaacsim_validation/control_bridge.py
```

Expected: all pass without starting Isaac.

- [ ] **Step 7: Prove the completed bridge ships from an installed wheel**

Build a fresh wheel, install it into an isolated temporary virtual environment,
and import both `isaacsim_validation.bridge_protocol` and
`isaacsim_validation.control_bridge` without importing Isaac-only modules at
module-import time. From the installed package root, assert the wheel contains
`bridge_protocol.py`, `control_bridge.py`, executable/source-readable
`run_isaacsim60_control_bridge.sh`, and the required passive-linkage JSON data.
This proof occurs here, after every bridge artifact exists; the earlier Task 1
wheel smoke remains only the distribution-loader packaging proof.

- [ ] **Step 8: Append the bridge boundary to docs and commit**

```bash
git add isaacsim_validation/control_bridge.py \
  isaacsim_validation/run_isaacsim60_control_bridge.sh \
  isaacsim_validation/README.md tests/test_superarm_isaac_protocol.py \
  isaacsim_usd_engineering_log.md
git commit -m "feat(isaac): add managed SuperArm articulation bridge"
```

### Task 4: Add the host-side Isaac runtime and service/API session

**Files:**
- Create: `lelab/superarm/isaac_runtime.py`
- Create: `tests/test_superarm_isaac_runtime.py`
- Modify: `lelab/superarm/transports.py:44-82`
- Modify: `lelab/superarm/service.py:26-70,266-402`
- Modify: `lelab/superarm/api.py:38-43,287-320,419-434`
- Modify: `tests/test_superarm_dashboard.py:300-430`

- [ ] **Step 1: Write failing fake-bridge runtime tests**

Use a fake JSONL bridge process/server, not Isaac, to assert:

- managed startup validates/extracts the ZIP and passes a token by file, never by logs;
- `connect()` rejects a `hello` response unless the joint set is exact and count is 13;
- arm and hand calls update a complete cached 13-target map;
- a 110-degree motor2 command becomes positive `1.10` for Isaac;
- `command_logical([0, 0, 0, 0, 0, 1])` sends one atomic 13-target request;
- telemetry polling emits 13 measured joints at no more than 20 Hz;
- `stop()` sends `hold`;
- owned close sends `shutdown` and reaps the child, while external close does not;
- connect timeout includes the current startup phase and last bounded log lines;
- `capture()` accepts only a safe relative path returned by the bridge, resolves it beneath the host run directory, rejects traversal/symlinks, and survives repeated captures followed by another command.

- [ ] **Step 2: Write failing API/service tests**

Inject a fake runtime factory and assert:

```python
response = client.post("/api/superarm/session", json={
    "runtime": "isaac_sim",
    "isaac_distribution_zip": str(distribution_zip),
    "isaac_bridge_mode": "managed",
})
assert response.status_code == 200
assert response.json()["runtime"] == "isaac_sim"

telemetry = client.get("/api/superarm/telemetry").json()["state"]
assert len(telemetry["arm"]) == 5
assert len(telemetry["hand"]) == 8
assert client.get("/api/superarm/video").status_code == 409
```

- [ ] **Step 3: Confirm focused tests fail**

```bash
uv run --extra test pytest -q \
  tests/test_superarm_isaac_runtime.py \
  tests/test_superarm_dashboard.py
```

Expected: failure for missing `IsaacSimRuntime` and unsupported `isaac_sim`.

- [ ] **Step 4: Define a concrete common runtime surface and implement `IsaacSimRuntime`**

The constructor is dependency-injectable for unit tests:

```python
class SuperArmRuntime(Protocol):
    connected: bool
    failure: str | None
    supports_video: bool
    def connect(self) -> None: ...
    def command_partial(self, *, arm_rad=None, hand_deg=None, hand_speed=None) -> None: ...
    def command_logical(self, values: list[float]) -> None: ...
    def observe(self) -> dict[str, Any]: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...

class IsaacSimRuntime:
    def __init__(
        self,
        distribution_zip: str | Path,
        *,
        bridge_mode: Literal["managed", "external"] = "managed",
        host: str = "127.0.0.1",
        port: int = 8765,
        startup_timeout_s: float = 180.0,
        state_callback: Callable[[dict[str, Any]], None] | None = None,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        client_factory: Callable[..., IsaacBridgeClient] = IsaacBridgeClient,
    ) -> None: ...
```

`command_partial(arm_rad=None, hand_deg=None)` merges every provided subsystem into `_targets` and sends the entire validated map exactly once. `command_logical(values)` sends `action_to_isaac_targets(values)` once. Give `MuJoCoRuntime` compatible `command_partial` and `command_logical` wrappers while preserving its existing `command()` behavior: `command_logical` normalizes the six-value action, converts its five arm values and fixed grasp code, updates both subsystems in one cached target snapshot, and applies that snapshot atomically through the existing MuJoCo command path. Both concrete adapters therefore satisfy `SuperArmRuntime`. `IsaacSimRuntime.frame()` returns `(0, None)` and `supports_video=False`; `capture()` is explicit. Managed launch redirects stdout/stderr to files and owns a process group; close performs bridge shutdown, timed wait, process-group terminate, then kill fallback. External close is socket-only.

- [ ] **Step 5: Generalize the service through a runtime factory**

Change `self.runtime` from `MuJoCoRuntime | None` to the common transport/runtime protocol. `start_session()` keeps existing arguments and adds keyword-only Isaac settings. Construct MuJoCo exactly as before for its two modes; construct `IsaacSimRuntime` only for `isaac_sim`. Add one safety-gated dispatcher used by manual, pose, sequence, logical, and teleoperation actions; it checks connection, emergency stop, live-rate state, and then composes one atomic runtime command:

```python
def logical_action(self, values: list[float]) -> dict[str, Any]:
    normalized = normalize_superarm_action(values)
    return self._dispatch(logical=normalized, source="staged")
```

Capabilities report Docker CLI availability, configured image, validated distribution path/contract, bridge mode defaults, and errors without launching Isaac. A service-owned monotonic watchdog enforces the 10-second live timeout even when no browser/websocket/telemetry poll is active. Its fake-runtime test sends one live command, performs zero telemetry/websocket calls, crosses the deadline, and observes exactly one hold.

- [ ] **Step 6: Extend the API and explicit video boundary**

`SessionRequest.runtime` becomes `Literal["mujoco", "hybrid_serial", "isaac_sim"]` and gains server-local Isaac fields. Add `PUT /api/superarm/logical-action` accepting exactly six mutually exclusive logical values and routing through the common service safety gate. Add `POST /api/superarm/capture` and `GET /api/superarm/capture/latest`; both reject non-Isaac runtimes. `/api/superarm/video` checks `supports_video` and returns `409` with “Continuous video is only available for MuJoCo; use the Isaac capture endpoint.”

- [ ] **Step 7: Run runtime/service/API regressions**

```bash
uv run --extra test pytest -q \
  tests/test_superarm_isaac_runtime.py \
  tests/test_superarm_dashboard.py \
  tests/test_superarm_mujoco_focus.py
uv run ruff check lelab/superarm/isaac_runtime.py lelab/superarm/service.py \
  lelab/superarm/api.py tests/test_superarm_isaac_runtime.py tests/test_superarm_dashboard.py
```

- [ ] **Step 8: Record and commit the runtime slice**

```bash
git add lelab/superarm/isaac_runtime.py lelab/superarm/transports.py \
  lelab/superarm/service.py lelab/superarm/api.py \
  tests/test_superarm_isaac_runtime.py tests/test_superarm_dashboard.py \
  isaacsim_usd_engineering_log.md
git commit -m "feat(superarm): control Isaac Sim through LeLab sessions"
```

### Task 5: Register the six-control LeRobot Isaac backend

**Files:**
- Create: `lelab/superarm/isaac_robot.py`
- Create: `lelab/superarm/data/superarm_isaac.yaml`
- Create: `tests/test_superarm_isaac_lerobot_backend.py`
- Modify: `lelab/superarm/robot.py:41-153` only if a small shared base removes duplication without changing behavior

- [ ] **Step 1: Write failing LeRobot contract tests**

Use a fake service/runtime and assert:

```python
robot = SuperArmIsaacRobot(config, runtime_service=fake_service)
assert list(robot.action_features) == CANONICAL_FEATURES
assert list(robot.observation_features) == CANONICAL_FEATURES
assert robot.send_action([0.1, -0.1, 0.2, -0.2, 0.05, 1.0]).shape == (6,)
assert len(fake_service.runtime.last_targets) == 13
assert fake_service.runtime.last_targets["finger1_motor2"] == pytest.approx(1.10)
with pytest.raises(ValueError, match="exactly 6"):
    robot.send_action([0.0] * 13)
```

Also assert `get_visualization_joints()` returns measured positive Isaac hand radians unchanged and `connect()` rejects an active non-Isaac session.

- [ ] **Step 2: Confirm the test fails**

```bash
uv run --extra test pytest -q tests/test_superarm_isaac_lerobot_backend.py
```

Expected: missing `SuperArmIsaacRobotConfig`.

- [ ] **Step 3: Implement the registered robot**

```python
@RobotConfig.register_subclass("superarm_isaac")
@dataclass(kw_only=True)
class SuperArmIsaacRobotConfig(RobotConfig):
    distribution_zip: str
    bridge_mode: Literal["managed", "external"] = "managed"
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8765
    cameras: dict = field(default_factory=dict)
```

`SuperArmIsaacRobot` exposes the same six features as `SuperArmMujocoRobot`, calls `runtime_service.logical_action()` from `send_action`, reads five measured arm positions plus the last commanded discrete grasp code for observations, returns 13 measured positions for visualization, and owns/disconnects only a session it started. Document that the sixth observation is commanded-state semantics until a separately validated measured-grasp classifier exists.

- [ ] **Step 4: Add the YAML config**

Copy the proven logical joint names, arm limits, SO-101 mapping, grasp motions, and 13 physical names from `lelab/superarm/data/superarm_mujoco.yaml`, but set `_type: superarm_isaac` and add:

```yaml
isaac:
  bridge_mode: managed
  host: 127.0.0.1
  port: 8765
  image: nvcr.io/nvidia/isaac-sim:6.0.0
  distribution_env: SUPERARM_ISAAC_DISTRIBUTION_ZIP
```

- [ ] **Step 5: Run both robot backends and commit**

```bash
uv run --extra test pytest -q \
  tests/test_superarm_isaac_lerobot_backend.py \
  tests/test_superarm_mujoco_focus.py \
  tests/test_superarm_isaac_contract.py
uv run ruff check lelab/superarm/isaac_robot.py tests/test_superarm_isaac_lerobot_backend.py
git add lelab/superarm/isaac_robot.py lelab/superarm/data/superarm_isaac.yaml \
  tests/test_superarm_isaac_lerobot_backend.py isaacsim_usd_engineering_log.md
git commit -m "feat(lerobot): register six-control SuperArm Isaac backend"
```

### Task 6: Reuse the backend in teleoperation, manual leader, and recording

**Files:**
- Modify: `lelab/teleoperate.py:79-92,194-267,304-326,511-531`
- Modify: `lelab/record.py:79-100,158-180,223-269`
- Modify: `lelab/manual_leader.py:88-193`
- Modify: `lelab/utils/config.py:332-399,515-540`
- Modify: `lelab/server.py:1170-1190,1227-1291`
- Modify: `tests/test_teleoperate.py:172-285`
- Modify: `tests/test_record.py:120-300`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Write failing backend-dispatch tests**

Assert `/move-arm` constructs `SuperArmIsaacRobot`, websocket messages use `robot_backend="superarm_isaac"`, `/send-joint-action` returns six logical values and 13 physical values, recording builds `SuperArmIsaacRobotConfig`, ACT dataset features remain shape `(6,)`, and manual leader open/half/close targets use positive Isaac motor2 values.

- [ ] **Step 2: Add common backend predicates without merging runtime semantics**

Use constants/functions rather than scattered equality checks:

```python
SUPERARM_BACKENDS = frozenset({"superarm_mujoco", "superarm_isaac"})

def is_superarm_backend(value: str | None) -> bool:
    return value in SUPERARM_BACKENDS
```

MuJoCo-only MJCF visual routes remain guarded by exact equality with `superarm_mujoco`.

- [ ] **Step 3: Generalize teleoperation factories and messages**

`TeleoperateRequest` gains `isaac_distribution_zip`, `isaac_bridge_mode`, `isaac_host`, and `isaac_port`. `_create_superarm_robot()` selects the config/class. `_handle_start_superarm_teleoperation()` uses `request.robot_backend` in logs, thread names, websocket events, and responses. The SO-101 calibration path remains bypassed for both custom backends.

- [ ] **Step 4: Generalize recording without changing policy width**

`RecordingRequest` receives the same Isaac fields. `_create_superarm_record_config()` selects MuJoCo or Isaac config, then constructs the same `SuperArmTeleoperatorConfig` and dataset configuration. Tests inspect `hw_to_dataset_features(...)["action"]["shape"] == (6,)`.

- [ ] **Step 5: Make manual leader targets backend-aware**

`build_manual_leader_config()` keeps canonical six-value buttons for both backends. For display/URDF targets, MuJoCo uses `degrees_to_mujoco`; Isaac uses `grasp_to_urdf_targets`. The `start_request` and returned `robot_backend` copy the record backend instead of forcing `superarm_mujoco` (`lelab/manual_leader.py:170-193`).

- [ ] **Step 6: Add an optional built-in Isaac robot record**

Preserve the existing `SuperArm + AmazingHand` MuJoCo record. Add `SuperArm + AmazingHand (Isaac Sim)` only when `SUPERARM_ISAAC_DISTRIBUTION_ZIP` resolves to a valid distribution. It is marked `purpose: diagnostic` so existing default selection is unchanged. Cleanliness requires readable YAML, valid distribution, loopback host for managed mode, and port `1..65535`; it does not require SO-101 calibration or a MuJoCo model.

- [ ] **Step 7: Run backend and server regressions**

```bash
uv run --extra test pytest -q \
  tests/test_teleoperate.py \
  tests/test_record.py \
  tests/test_server.py \
  tests/test_superarm_amazinghand_manual_config.py \
  tests/test_superarm_mujoco_focus.py
uv run ruff check lelab/teleoperate.py lelab/record.py lelab/manual_leader.py \
  lelab/utils/config.py lelab/server.py tests/test_teleoperate.py tests/test_record.py
```

- [ ] **Step 8: Append evidence and commit**

```bash
git add lelab/teleoperate.py lelab/record.py lelab/manual_leader.py \
  lelab/utils/config.py lelab/server.py tests/test_teleoperate.py tests/test_record.py \
  tests/test_server.py tests/test_superarm_amazinghand_manual_config.py \
  isaacsim_usd_engineering_log.md
git commit -m "feat(lelab): use Isaac backend for teleop and recording"
```

### Task 7: Add the Isaac runtime to the website without faking video

**Files:**
- Create: `frontend/src/lib/superarmRuntime.ts`
- Create: `frontend/src/lib/superarmRuntime.test.ts`
- Modify: `frontend/src/pages/SuperArm.tsx:40-105,150-190,315-470`
- Modify: `frontend/src/hooks/useRobots.ts:7-22`
- Modify: `frontend/src/pages/Landing.tsx:161-260`
- Modify: `frontend/src/components/landing/RobotTile.tsx:45-49`
- Modify: `frontend/src/components/landing/RecordingModal.tsx:87-99,148-180`
- Modify: `frontend/src/pages/Recording.tsx:110-140`
- Modify: `frontend/src/pages/ManualLeader.tsx:145-170`

- [ ] **Step 1: Write failing frontend helper tests**

```typescript
expect(isSuperArmBackend("superarm_mujoco")).toBe(true);
expect(isSuperArmBackend("superarm_isaac")).toBe(true);
expect(runtimeSupportsContinuousVideo("isaac_sim")).toBe(false);
expect(runtimeSupportsCapture("isaac_sim")).toBe(true);
expect(buildIsaacSessionPayload(settings)).toEqual({
  runtime: "isaac_sim",
  isaac_distribution_zip: settings.distributionZip,
  isaac_bridge_mode: "managed",
  isaac_host: "127.0.0.1",
  isaac_port: 8765,
});
```

- [ ] **Step 2: Run Vitest and confirm failure**

```bash
cd frontend && npm test -- --run src/lib/superarmRuntime.test.ts
```

Expected: missing module/functions.

- [ ] **Step 3: Add shared runtime helpers and record types**

Define:

```typescript
export type SuperArmRuntime = "mujoco" | "hybrid_serial" | "isaac_sim";
export const SUPERARM_BACKENDS = ["superarm_mujoco", "superarm_isaac"] as const;
export const isSuperArmBackend = (value?: string) =>
  value === "superarm_mujoco" || value === "superarm_isaac";
```

Add optional `isaac_distribution_zip`, `isaac_bridge_mode`, `isaac_host`, and `isaac_port` fields to `RobotRecord`.

- [ ] **Step 4: Extend `/superarm` connection and status UI**

Add `Isaac Sim 6.0 (USD)` to the runtime selector. When selected, show server-local distribution path, managed/external mode, host/port, capability validation status, and the text “6 logical controls → 13 physical Isaac joints.” Submit only the Isaac fields for `isaac_sim`; preserve the current serial field behavior for `hybrid_serial` (`frontend/src/pages/SuperArm.tsx:315-337`).

- [ ] **Step 5: Replace only the second visualization panel for Isaac**

Keep `SuperArmUrdfViewer` and telemetry. For MuJoCo, preserve the `<img src="/api/superarm/video">`. For Isaac, show connection phase, physics step, 13-joint coverage, last capture metadata, a `Capture whole robot` button, a `Capture hand` button, and the latest PNG returned by the capture endpoint. Do not label it “live video.”

- [ ] **Step 6: Enable both SuperArm record backends in landing/recording/manual pages**

Replace direct `=== "superarm_mujoco"` checks with `isSuperArmBackend()` only where the behavior is genuinely shared. Continue exact MuJoCo checks for MJCF-only overlays. Forward Isaac fields into teleoperate and recording requests. Change user-facing errors from “MuJoCo follower” to the selected backend name.

- [ ] **Step 7: Run frontend tests, lint, and build**

```bash
cd frontend
npm test
npm run lint
npm run build
```

Expected: all tests pass, ESLint exits 0, and Vite production build exits 0.

- [ ] **Step 8: Commit the website slice**

```bash
git add frontend/src/lib/superarmRuntime.ts frontend/src/lib/superarmRuntime.test.ts \
  frontend/src/pages/SuperArm.tsx frontend/src/hooks/useRobots.ts \
  frontend/src/pages/Landing.tsx frontend/src/components/landing/RobotTile.tsx \
  frontend/src/components/landing/RecordingModal.tsx frontend/src/pages/Recording.tsx \
  frontend/src/pages/ManualLeader.tsx isaacsim_usd_engineering_log.md
git commit -m "feat(web): add truthful Isaac Sim SuperArm controls"
```

### Task 8: Run the real LeLab-to-Isaac acceptance sequence and create evidence

**Files:**
- Create: `isaacsim_validation/run_lelab_isaac_e2e.py`
- Modify: `isaacsim_validation/README.md`
- Create under ignored runtime output: `artifacts/lelab_isaac_control/<run-id>/...`

- [ ] **Step 1: Implement an API-driven acceptance runner**

The runner takes `--base-url`, `--distribution-zip`, and `--run-dir`; starts an `isaac_sim` session through `POST /api/superarm/session`; waits for connected telemetry; then sends these canonical actions through `PUT /api/superarm/logical-action`, the same safety-gated service dispatch used by the LeRobot backend:

```python
CASES = [
    ("neutral_open", [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    ("arm_probe_half", [0.12, -0.12, 0.10, -0.10, 0.08, 0.5]),
    ("neutral_close", [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]),
]
```

For each case, poll until settled or 10 s, capture whole/hand PNGs, and write the exact logical action, expanded targets, measured positions, absolute errors, timing, bridge metadata, and artifact hashes into `lelab-isaac-e2e-report.json`. Assemble the three hand frames into `lelab-isaac-open-half-close.gif` with Pillow.

- [ ] **Step 2: Start the built frontend/backend server**

```bash
export SUPERARM_ISAAC_DISTRIBUTION_ZIP=/absolute/path/to/superarm_amazinghand_isaac_sim_usd_distribution_20260722.zip
export ISAAC_SIM_STARTUP_TIMEOUT_S=180
uv run lelab
```

Use the project’s normal server port and record the actual URL in the report. Do not expose the bridge port through Tailscale or bind it beyond loopback.

- [ ] **Step 3: Run the E2E acceptance**

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-lelab-isaac-control"
uv run python -m isaacsim_validation.run_lelab_isaac_e2e \
  --base-url http://127.0.0.1:8000 \
  --distribution-zip "$SUPERARM_ISAAC_DISTRIBUTION_ZIP" \
  --run-dir "artifacts/lelab_isaac_control/$RUN_ID"
```

Expected report gates:

- `status == "PASS"`;
- `runtime == "isaac_sim"` and Isaac version starts with `6.0`;
- one articulation and exactly 13 unique DOF names;
- logical action width exactly 6;
- maximum settled arm error `<= 0.02 rad`;
- maximum settled hand error `<= 0.01 rad`;
- open/half-close/close hand images are nonblank;
- adjacent hand frames have mean absolute pixel difference `> 0.5`;
- emergency stop and the 10 s live timeout both invoke hold;
- after hold, the reported target vector remains unchanged for at least 120 physics steps;
- managed disconnect exits the bridge/container within 10 s and leaves no matching container running.

- [ ] **Step 4: Exercise the real website path**

Open `/superarm`, select `Isaac Sim 6.0 (USD)`, connect, send one arm probe and open/half/close hand motions, verify the URDF viewer and 13-joint coverage update, trigger both capture buttons, disconnect, then reconnect once. Save a full-page screenshot and a short browser action log under the same run directory. A reconnect failure is a release blocker.

- [ ] **Step 5: Run all automated quality gates after the live test**

```bash
uv run --extra test pytest -q
uv run ruff check .
uv run --with mypy mypy lelab
cd frontend && npm test && npm run lint && npm run build
```

Expected: every command exits 0. If project-wide MyPy exposes pre-existing ignored modules, record the exact output and still require zero new errors in changed modules with `uv run --with mypy mypy` over those files.

- [ ] **Step 6: Commit the runner and documentation, not ignored runtime output**

```bash
git add isaacsim_validation/run_lelab_isaac_e2e.py isaacsim_validation/README.md \
  isaacsim_usd_engineering_log.md
git commit -m "test(isaac): add LeLab controlled-motion acceptance run"
```

### Task 9: Independent verification, durable wiki, merge, and push

**Files:**
- Create: `omx_wiki/lelab-isaac-sim-superarm-control.md`
- Modify: `omx_wiki/index.md`
- Modify: `omx_wiki/log.md`
- Modify: `isaacsim_usd_engineering_log.md`

- [ ] **Step 1: Delegate numeric and lifecycle review to a verifier subagent**

Give the verifier the branch commit range, `lelab-isaac-e2e-report.json`, bridge/server logs, all test outputs, and container cleanup evidence. Require an explicit `APPROVE` or `REJECT` against every acceptance criterion; narrative-only review is insufficient.

- [ ] **Step 2: Delegate close-up visual review to a separate vision/verifier subagent**

Give it the whole-robot screenshot, open/half/close PNGs, and GIF. Require confirmation that the arm and hand are visible, attached, nonblank, and visibly different across states. The reviewer must also state that the images do not prove contact or the 88 passive followers are live physics.

- [ ] **Step 3: Repair any rejected gate with another TDD slice and commit**

For each rejection, first add/strengthen the regression test, reproduce the failure, make the smallest repair, rerun targeted and affected suites, append the engineering-log entry, and create a separate `fix(isaac): ...` commit. Repeat the independent review after the repair.

- [ ] **Step 4: Write the durable wiki record**

The wiki page records architecture, exact ZIP/hash used, launch/config variables, six-to-13 mapping, protocol operations, website workflow, teleoperate/record workflow, numeric results, evidence paths, commit hashes, cleanup behavior, troubleshooting, and explicit proof boundaries. Link it from `omx_wiki/index.md` and append a dated entry to `omx_wiki/log.md`.

- [ ] **Step 5: Commit the final documentation**

```bash
git add omx_wiki/lelab-isaac-sim-superarm-control.md omx_wiki/index.md \
  omx_wiki/log.md isaacsim_usd_engineering_log.md
git commit -m "docs(isaac): record LeLab SuperArm control evidence"
```

- [ ] **Step 6: Verify the final tree and commit history**

```bash
git status --short
git log --oneline --decorate fork/main..HEAD
uv run --extra test pytest -q
uv run ruff check .
cd frontend && npm test && npm run lint && npm run build
```

Expected: clean tracked worktree, all feature-slice commits present, and every command exits 0.

- [ ] **Step 7: Push the feature branch, then merge only after approval**

```bash
git push -u fork feature/lelab-isaacsim-control
git switch main
git merge --ff-only feature/lelab-isaacsim-control
git push fork main
git rev-parse main
git rev-parse fork/main
```

Expected: local `main` and `fork/main` resolve to the same final commit. If the implementation uses a different isolated worktree/branch name, substitute that exact verified branch; never merge from the dirty historical root checkout.

## 6. Acceptance Criteria

1. **Distribution safety:** every archive member and checksum is validated before extraction; malicious path/symlink/duplicate/checksum tests pass.
2. **Runtime isolation:** importing `lelab.server` on the host never imports `isaacsim`, `omni`, or `pxr`.
3. **Session API:** `POST /api/superarm/session` accepts `isaac_sim`; reconnecting the same active mode is idempotent; starting a different mode while connected returns conflict; disconnect/reconnect works.
4. **Policy shape:** `superarm_isaac.action_features` and numeric action tensors have width 6; a 13-value policy action is rejected.
5. **Physical shape:** bridge `hello`, command, and observation prove exactly 13 unique expected joint names and one articulation.
6. **Coordinate correctness:** closed grasp produces positive `fingerN_motor2 ~= 1.10` in Isaac; no MuJoCo-negative motor2 value crosses the bridge.
7. **Atomic logical command:** one six-value LeRobot action produces one complete 13-target bridge command.
8. **Manual precision:** website individual finger commands still map each selected finger’s two degree inputs to the correct positive Isaac joints.
9. **Measured control:** the real Isaac E2E settles within `0.02 rad` arm and `0.01 rad` hand maximum absolute error for all three cases.
10. **Safety:** emergency stop and live timeout invoke hold, commands are blocked during emergency stop, and held targets do not drift for 120 physics steps.
11. **Telemetry:** website/websocket/API report five arm plus eight hand measured/target/error channels at no more than 20 Hz.
12. **Truthful visualization:** MuJoCo retains continuous video; Isaac shows telemetry plus explicit captures and never labels a static capture as live video.
13. **Visual evidence:** reviewed whole-robot and hand frames are nonblank; open/half/close are visibly distinct; the GIF covers all three states.
14. **Recording:** manual and SO-101 recording can select `superarm_isaac`; the recorded action/observation schema remains six-wide.
15. **Ownership cleanup:** managed disconnect removes only its owned container within 10 s; external mode does not terminate its bridge.
16. **Regression:** the full pytest suite, Ruff, frontend tests/lint/build, and targeted MuJoCo tests pass.
17. **Documentation:** engineering log and `omx_wiki` contain exact evidence paths, commits, commands, and limits.
18. **Git delivery:** each feature/fix slice is committed, verifier approval is recorded, the verified branch is pushed, and merge uses fast-forward into the fork main.

## 7. Risks and Mitigations

| Risk | Mitigation and verification |
| --- | --- |
| Isaac startup exceeds ordinary web timeouts | Managed startup uses a configurable 180 s timeout, phase file, bounded log tail, and asynchronous UI status; unit-test timeout/error text and live-test cold start. |
| Isaac array order differs from manifest or local constants | Build indices from `art.dof_names` and compare sets/counts; all wire targets are mappings; add shuffled-order tests. |
| Hand motor2 sign leaks from MuJoCo | Centralize positive Isaac conversion and assert `+1.10` for close at unit, fake-bridge, and real E2E levels. |
| FastAPI crashes from Isaac imports | Place every Isaac import in the bridge process after `SimulationApp`; add import-isolation test. |
| Port collision or unintended exposure | Default/bound managed host is `127.0.0.1`; fail with an actionable port-collision error; reject non-loopback managed binds. |
| Orphaned container after server failure | Unique container ID, wrapper trap, owned-process PID/container metadata, shutdown then terminate/kill fallback, and live cleanup assertion. |
| Killing an externally owned bridge | Track `owns_process`; external close closes only its socket. Unit-test both modes. |
| Token leaks in logs or API responses | Generate a 0600 file for managed mode, redact `token` from repr/errors/status, and never send it to the browser. |
| ZIP extraction races or partial cache | Extract under a temporary directory, verify all files, atomically rename by archive SHA, and lock per digest. |
| Website implies real-time Isaac video | Runtime capability drives conditional UI; return 409 from continuous video and expose only explicit capture buttons. |
| Detailed passive linkage is mistaken for live physics | Wiki/UI/evidence state 13 physical DOFs and snapshot-only 88 followers; visual verifier checks wording. |
| Refactor breaks MuJoCo, hardware, or SO-101 paths | Keep exact-mode checks for mode-specific code and run current MuJoCo/hardware/server suites after every affected slice. |
| Live capture stalls control | Capture is explicit and serialized; UI disables command buttons while capture runs; telemetry resumes and E2E asserts post-capture command success. |
| Ignored binary evidence is lost | Keep artifacts ignored but record absolute/relative paths and SHA256 in tracked engineering log/wiki; publish a distribution/evidence ZIP only when explicitly requested. |

## 8. Verification Matrix

| Layer | Command/evidence | Pass condition |
| --- | --- | --- |
| Unit: contract/archive | `pytest tests/test_superarm_isaac_contract.py tests/test_superarm_isaac_distribution.py` | All safety, exact-name, six-to-13, and sign cases pass. |
| Unit: protocol/runtime | `pytest tests/test_superarm_isaac_protocol.py tests/test_superarm_isaac_runtime.py` | Framing, timeout, lifecycle, hold, ownership, redaction, and capture allowlist pass. |
| Integration: service/API | `pytest tests/test_superarm_dashboard.py tests/test_server.py` | Isaac lifecycle/actions/capture work and video boundary is explicit. |
| Integration: LeRobot | `pytest tests/test_superarm_isaac_lerobot_backend.py tests/test_teleoperate.py tests/test_record.py` | Six-wide policy contract and 13-wide internal target/state contract pass. |
| Regression: MuJoCo | `pytest tests/test_superarm_mujoco_focus.py tests/test_superarm_dashboard.py` | Existing MuJoCo behavior remains green. |
| Frontend | `npm test && npm run lint && npm run build` | UI logic, lint, and production build pass. |
| Static | `ruff check .` and MyPy changed-module check | No new static errors. |
| Real Isaac numeric | `run_lelab_isaac_e2e.py` report | One articulation, 13 names, error thresholds, hold, cleanup, reconnect all pass. |
| Real Isaac visual | whole/hand PNGs and GIF | Nonblank, attached, visible motion; independent reviewer approves. |
| Delivery | `git status`, log, `rev-parse main fork/main` | Clean, sliced commits, verified/pushed, refs match after merge. |

## 9. Implementation Staffing Guidance

This plan is parallelizable after Task 2 fixes the contract/protocol:

- **Executor (medium reasoning):** Tasks 1-2, distribution and protocol foundations.
- **Isaac executor/debugger (high reasoning):** Task 3 and live bridge failures; must use the Isaac Sim 6.0 runtime/robot-asset/python-scripting skills.
- **Backend executor (medium reasoning):** Tasks 4-6 after the protocol lands.
- **Frontend executor/designer (medium/high reasoning):** Task 7 after API shapes are fixed.
- **Test engineer (medium reasoning):** Task 8 acceptance runner and hostile lifecycle scenarios.
- **Verifier (high reasoning):** Task 9 numeric/lifecycle verdict.
- **Vision/verifier (low/high reasoning):** Task 9 image/GIF review, separate from the implementation author.

The leader owns integration, commit sequencing, final full-suite runs, wiki updates, and the merge/push decision. Workers must report shared-file conflicts before editing `service.py`, `teleoperate.py`, `record.py`, or the engineering log.

## 10. Execution Handoff

Recommended execution is `$team` plus `$ultragoal`: use the plan as the durable leader-owned ledger, then assign the foundation, backend, frontend, and test lanes after their dependencies are ready. Team verification must return targeted test output, live Isaac report paths, visual artifact paths, commit hashes, and explicit verifier verdicts before shutdown. `$ralph` is a valid fallback only if a persistent single-owner sequential repair/verification loop is explicitly chosen.

Suggested launch context:

```text
$ultragoal docs/superpowers/plans/2026-07-22-lelab-isaacsim-usd-control.md
$team implement docs/superpowers/plans/2026-07-22-lelab-isaacsim-usd-control.md with executor, debugger, frontend executor, test-engineer, verifier
```

Goal-mode selection:

- Use `$ultragoal` for durable implementation and evidence checkpoints.
- Add `$team` because the backend/frontend/test lanes can proceed in parallel after the protocol contract is fixed.
- Do not use `$autoresearch-goal`; this is implementation and validation, not a research deliverable.
- Do not use `$performance-goal`; performance optimization is not the requested objective.
