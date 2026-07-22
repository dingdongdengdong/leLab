# AmazingHand Passive-Linkage Visuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add shell-free, closed-loop-informed AmazingHand linkage visuals to Isaac physics snapshots while preserving the verified 13-DOF combined articulation and six-value LeRobot/VLA action.

**Architecture:** A host-side generator uses the checksum-locked original AmazingHand MJCF only to solve visual keyframes and writes a committed JSON manifest. A pure Python follower maps measured Isaac finger angles to those transforms. A thin USD layer adds visual-only references to exported snapshot stages and flattens them; the reusable robot root never receives follower opinions or additional physics schemas.

**Tech Stack:** Python 3.14, MuJoCo 3.10.0 for offline keyframe generation, OpenUSD/Isaac Sim 6.0 for snapshot composition, pytest, Ruff.

---

## File structure

- Create `isaacsim_validation/generate_passive_linkage_keyframes.py`: deterministic host generator from checked MJCF and checked ZIP USD geometry.
- Create `isaacsim_validation/data/amazinghand_passive_linkage_keyframes.json`: generated transform/provenance manifest.
- Create `isaacsim_validation/passive_linkage.py`: pure manifest validation, per-finger closure calculation, interpolation, and pose contract.
- Create `isaacsim_validation/passive_linkage_usd.py`: thin `pxr` snapshot-authoring and validation boundary.
- Modify `isaacsim_validation/run_validation.py`: add followers only to exported snapshots and record their contract.
- Modify `isaacsim_validation/render_physics_snapshots.py`: enforce follower count, shell exclusion, visual-only schemas, and proof text.
- Create `tests/test_passive_linkage.py` and `tests/test_passive_linkage_usd.py`.
- Modify `tests/test_superarm_isaac_visuals.py`.
- Update `isaacsim_usd_engineering_log.md`, `isaacsim_validation/README.md`, and `omx_wiki/superarm-urdf-validation-in-isaac-sim-6-0.md`.

### Task 1: Generate a checksum-locked shell-free linkage manifest

**Files:**
- Create: `isaacsim_validation/generate_passive_linkage_keyframes.py`
- Create: `isaacsim_validation/data/amazinghand_passive_linkage_keyframes.json`
- Create: `tests/test_passive_linkage.py`

- [ ] **Step 1: Write failing provenance and allowlist tests**

```python
def test_manifest_uses_checked_original_mjcf_and_zip_geometry():
    manifest = load_manifest(MANIFEST)
    assert manifest["source_mjcf_sha256"] == "d21366e7c9a1f5debe04b8abb5ea1ade7fade42e493e09d003f5db196548b098"
    assert manifest["source_hand_zip_sha256"] == "3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377"
    assert manifest["finger_count"] == 4
    assert manifest["passive_visual_part_count"] == 120


def test_manifest_excludes_shells_and_existing_frame_cores():
    manifest = load_manifest(MANIFEST)
    names = [part["source_prim"] for part in manifest["parts"]]
    assert all("proximal_shell" not in name for name in names)
    assert all("distal_shell" not in name for name in names)
    assert set(manifest["excluded_shell_indices"]) == {45, 51, 78, 85, 114, 115, 144, 152}
    assert set(manifest["existing_core_indices"]) == {44, 52, 76, 84, 113, 117, 147, 153}
```

- [ ] **Step 2: Run `.venv/bin/python -m pytest -q tests/test_passive_linkage.py` and confirm failure for the missing module/manifest.**

- [ ] **Step 3: Implement the deterministic generator with these locked ranges.**

```python
FINGER_BLOCKS = {1: range(26, 60), 2: range(60, 94), 3: range(94, 128), 4: range(128, 162)}
EXCLUDED_SHELL_INDICES = frozenset({45, 51, 78, 85, 114, 115, 144, 152})
EXISTING_CORE_INDICES = frozenset({44, 52, 76, 84, 113, 117, 147, 153})
KEYFRAMES = {
    "open": {"motor1": 0.05, "motor2": 0.02},
    "half_close": {"motor1": 0.50, "motor2": 0.56},
    "close": {"motor1": 0.95, "motor2": 1.10},
}
```

The generator must verify both source ZIP checksums, extract the historical MJCF/assets, parse each `base.usda` source prim and exact `/Instances/...` target, load MuJoCo, reset the `zero` keyframe, set all eight named actuators, and step until equality residual is below `1e-6`. It must compose solved body `xpos/xquat` with raw XML geom-local `pos/quat`; do not use `geom_xpos/xmat`, because MuJoCo mesh recentering does not match the ZIP prim-frame convention. Emit the four finger blocks minus eight shells and eight already-bound cores: exactly 120 parts, each with finger, role, source prim, instance prim, source index, and open/half-close/close wrist-local transforms.

Run:

```bash
.venv/bin/python -m isaacsim_validation.generate_passive_linkage_keyframes \
  --source-package-zip /home/dong/july/superarm_ws/robot_arm_hand_package.zip \
  --hand-distribution-zip /home/dong/july/superarm_ws/isaacsim_test/artifacts/distributions/amazinghand_isaac_sim_usd_distribution_20260722.zip \
  --output isaacsim_validation/data/amazinghand_passive_linkage_keyframes.json
```

- [ ] **Step 4: Generate twice and use `cmp` to require byte-identical JSON.**
- [ ] **Step 5: Run the tests and commit `feat(isaac): generate shell-free linkage keyframes`.**

### Task 2: Implement the pure measured-angle follower solver

**Files:**
- Create: `isaacsim_validation/passive_linkage.py`
- Modify: `tests/test_passive_linkage.py`

- [ ] **Step 1: Add failing finite, normalized-quaternion, and per-finger isolation tests.**

```python
def test_solver_returns_normalized_finite_pose_for_every_part():
    poses = solve_passive_linkage(HALF_CLOSE_MEASURED)
    assert len(poses) == 120
    assert {pose.finger for pose in poses} == {1, 2, 3, 4}
    assert all(all(math.isfinite(v) for v in pose.translate) for pose in poses)
    assert all(math.isclose(sum(v * v for v in pose.orient), 1.0, abs_tol=1e-6) for pose in poses)


def test_each_finger_uses_only_its_measured_motor_pair():
    baseline = solve_passive_linkage(OPEN_MEASURED)
    moved = solve_passive_linkage({**OPEN_MEASURED, "finger3_motor1": 0.95, "finger3_motor2": 1.10})
    assert changed_fingers(baseline, moved) == {3}
```

- [ ] **Step 2: Run the test and confirm it fails because `solve_passive_linkage` is absent.**
- [ ] **Step 3: Implement immutable `PassiveVisualPose`, strict manifest validation, closedness calculation, linear translation interpolation, and shortest-arc normalized quaternion slerp.**

```python
def finger_closedness(motor1: float, motor2: float) -> float:
    first = (motor1 - 0.05) / 0.90
    second = (motor2 - 0.02) / 1.08
    return min(1.0, max(0.0, (first + second) / 2.0))


def solve_passive_linkage(measured: Mapping[str, float]) -> tuple[PassiveVisualPose, ...]:
    """Interpolate checked closed-loop visual keyframes from measured Isaac angles."""
```

Interpolate open-to-half over `[0, 0.5]` and half-to-close over `(0.5, 1]`. Reject missing/non-finite joint values, duplicate source prims, non-normalized source quaternions, bad part counts, or source equality residual above `1e-6`.

- [ ] **Step 4: Run targeted pytest and changed-file Ruff.**
- [ ] **Step 5: Commit `feat(isaac): solve passive linkage follower poses`.**

### Task 3: Author followers into snapshots without physics schemas

**Files:**
- Create: `isaacsim_validation/passive_linkage_usd.py`
- Create: `tests/test_passive_linkage_usd.py`
- Modify: `isaacsim_validation/run_validation.py`
- Modify: `tests/test_superarm_isaac_visuals.py`

- [ ] **Step 1: Add failing tests requiring `stage.Export(snapshot)` before snapshot reopen/author/validate, and requiring `pristine_package` restoration from `finally`.**
- [ ] **Step 2: Run targeted tests and confirm the new contract fails.**
- [ ] **Step 3: Implement the thin USD boundary.**

```python
def author_passive_linkage_snapshot(
    snapshot_stage,
    robot_root: str,
    poses: Sequence[PassiveVisualPose],
    instances_usda: Path,
+) -> dict:
    """Add visual-only follower refs, flatten the snapshot, and return its contract."""
```

For each pose, define only `UsdGeom.Xform` prims under `r_wrist_interface/passive_linkage_visuals/fingerN/part_NNN`, author translate/orient, and add an instanceable child reference to the exact manifest `/Instances/...` prim. Add no `UsdPhysics` API, joint, rigid-body, mass, or collision schema. Flatten and atomically replace the snapshot so absolute source paths are not published.

Return a contract with mode `frame_plus_passive_linkage_no_shells`, 120 parts, 30 parts per finger, zero shell visuals, and zero added rigid bodies/colliders/joints.

- [ ] **Step 4: After every measured snapshot export, reopen it, solve from `measured`, author/flatten followers, validate, and store the contract in the snapshot report.**
- [ ] **Step 5: Move reusable-root byte restoration into a guarded `finally` path.**
- [ ] **Step 6: Run targeted pytest/Ruff and commit `feat(isaac): author linkage followers in snapshots`.**

### Task 4: Require direct visual evidence for the reconstructed linkage

**Files:**
- Modify: `isaacsim_validation/render_physics_snapshots.py`
- Modify: `isaacsim_validation/visuals.py`
- Modify: `tests/test_superarm_isaac_visuals.py`

- [ ] **Step 1: Add failing tests that reject wrong part counts, any `_shell` source, follower physics schemas, unchanged finger followers, blank frames, or static three-state sequences.**
- [ ] **Step 2: Before capture, traverse the follower group and require 120 total parts, 30 per finger, no shell names, and zero physics/collision/joint APIs. Record the result under `report["passive_linkage_visuals"]`.**
- [ ] **Step 3: Keep the fixed-camera open/half-close/close sequence and add four independent per-finger close-up snapshot renders.**
- [ ] **Step 4: Update proof text: frame cores and passive shell-free visuals move; outer shells remain disabled; no closed-loop PhysX claim.**
- [ ] **Step 5: Run targeted pytest/Ruff and commit `test(isaac): require shell-free linkage visual proof`.**

### Task 5: Run Isaac Sim 6.0 and publish evidence

**Files:**
- Modify: `isaacsim_validation/README.md`
- Modify: `isaacsim_usd_engineering_log.md`
- Modify: `omx_wiki/superarm-urdf-validation-in-isaac-sim-6-0.md`
- Create: `omx_wiki/assets/superarm-isaac60-passive-linkage-*`

- [ ] **Step 1: Prepare a fresh run ID ending in `combined-zip-passive-linkage` with the documented source URDF, authoritative hand ZIP, and `zip_learning` profile.**
- [ ] **Step 2: Run `isaacsim_validation/run_isaacsim60_validation.sh zip_learning "$RUN_ID" "$RUN_ROOT/zip_hand_source"`. Require PASS, 13 DOFs, one articulation root, action width 6, 120 follower visuals, zero shells, and visibly changed frames.**
- [ ] **Step 3: Run strict Asset Validator on the clean reusable root. Require zero blocking issues. Validate the close snapshot separately with the follower structural validator.**
- [ ] **Step 4: Review whole/open/half/close and four per-finger images with `view_image`; reject detached parts, wrong pivots, severe intersections, missing fingers, static followers, blank output, or visible shells.**
- [ ] **Step 5: Append the engineering log, copy reviewed artifacts into `omx_wiki/assets`, update wiki/README, and commit `docs(isaac): record passive linkage validation`.**

### Task 6: Ralph completion gates

- [ ] **Step 1: Run full pytest, changed-file Ruff, and `git diff --check`; record unrelated repo-wide lint separately.**
- [ ] **Step 2: Run architect verification over provenance, solver, root/snapshot boundary, report, validator, and every reviewed image.**
- [ ] **Step 3: Run standard ai-slop-cleaner only on files changed since baseline `4e95d73`, then repeat regression checks.**
- [ ] **Step 4: Push `feature/isaacsim-superarm-urdf-validation`, verify remote HEAD equals local HEAD, verify a clean worktree, write Ralph completion audit, and cancel Ralph state.**
