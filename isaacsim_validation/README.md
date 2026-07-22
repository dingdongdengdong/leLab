# SuperArm + AmazingHand USD validation in Isaac Sim 6.0

This directory builds and validates one SuperArm + AmazingHand robot asset for
Isaac Sim. The recommended learning profile uses the supplied Isaac Sim USD
archive for the detailed hand appearance while retaining the existing combined
SuperArm URDF as the single owner of robot physics.

## Authoritative hand source

```text
/home/dong/july/superarm_ws/isaacsim_test/artifacts/distributions/
  amazinghand_isaac_sim_usd_distribution_20260722.zip
SHA256: 3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377
entry: usd/amazinghand_graspable/amazinghand_graspable.usda
```

Preparation verifies the checksum, rejects unsafe archive paths, preserves the
eight hand-joint contract, and repairs the source package's invalid static
visual-shell rigid-body binding.

## Robot and control contract

The final `zip_learning` robot has:

- one articulation root;
- 13 physical movable joints: five SuperArm joints and eight AmazingHand joints;
- 28 rigid bodies and 13 collision prims in the verified import;
- one logical six-value LeRobot action: five arm values plus one grasp value;
- the grasp value expanded to fixed open/half-close/close targets for all eight
  hand joints.

The supplied hand USD's articulation is **not** nested into the SuperArm. Only
its checked visual payloads are copied and referenced beneath the existing
wrist, proximal, and distal links. This avoids two competing articulations.

## Profiles

| Profile | Purpose | Hand appearance |
| --- | --- | --- |
| `zip_learning` | Recommended Isaac/VLA/RL asset | Supplied Isaac USD wrist/palm/servo frame plus 88 shell-free structural linkage followers driven from the combined 13-DOF physics tree |
| `learning` | Diagnostic serial-URDF learning asset | Selected local URDF shell meshes |
| `aligned` | LeLab transforms with original URDF visuals | All generated URDF visuals retained |
| `served` | Browser/showroom-compatible tree | Hand visuals removed for the website overlay |
| `raw` | Unmodified source diagnostic | Original source transforms and visuals |

## Reproduce the verified run

The tested runtime is `nvcr.io/nvidia/isaac-sim:6.0.0`.

```bash
export SOURCE_WS=/home/dong/july/superarm_ws
export SOURCE_URDF="$SOURCE_WS/isaacsim_test/outputs/robot_arm_hand_from_zip_local_drive/superarm_amazinghand.urdf"
export HAND_ZIP="$SOURCE_WS/isaacsim_test/artifacts/distributions/amazinghand_isaac_sim_usd_distribution_20260722.zip"
export HAND_SHA=3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-combined-zip-usd"
export RUN_ROOT="artifacts/isaacsim_superarm/$RUN_ID"

.venv/bin/python -m isaacsim_validation.prepare_superarm_urdf \
  --source-urdf "$SOURCE_URDF" \
  --source-root "$SOURCE_WS" \
  --output-dir "$RUN_ROOT/zip_learning_input" \
  --profile zip_learning

.venv/bin/python -m isaacsim_validation.prepare_amazinghand_usd \
  --source-zip "$HAND_ZIP" \
  --expected-sha256 "$HAND_SHA" \
  --output-dir "$RUN_ROOT/zip_hand_source"

isaacsim_validation/run_isaacsim60_validation.sh \
  zip_learning "$RUN_ID" "$RUN_ROOT/zip_hand_source"

isaacsim_validation/run_asset_validator.sh \
  "$RUN_ROOT/zip_learning_isaac/superarm_amazinghand/superarm_amazinghand.usda" \
  "$RUN_ROOT/zip_learning_isaac/asset-validator.json"
```

## Export the validated distribution

The exporter accepts only a runtime `PASS`, a strict validator pass with zero
blocking issues, one articulation root, 13 revolute joints, successful arm and
hand motion, clean-package restoration, and the checked 88-part passive visual
contract. It writes a deterministic single-root ZIP with relative USD
composition arcs, both license texts, a manifest, per-file checksums, the
shell-free passive-linkage helper, and bounded validation evidence. Measured
70 MB snapshot stages and runtime world state are deliberately excluded.

```bash
export ACCEPTED_RUN=artifacts/isaacsim_superarm/20260722T163556Z-motor2-flexion-fix/zip_learning_isaac
export DIST=superarm_amazinghand_isaac_sim_usd_distribution_20260722_v2

.venv/bin/python -m isaacsim_validation.export_superarm_usd_distribution \
  --source-asset-dir "$ACCEPTED_RUN/superarm_amazinghand" \
  --runtime-report "$ACCEPTED_RUN/isaac-report.json" \
  --validator-report "$ACCEPTED_RUN/asset-validator.json" \
  --preview-image "$ACCEPTED_RUN/passive_linkage_contact_sheet.png" \
  --whole-image omx_wiki/assets/superarm-isaac60-passive-linkage-whole.png \
  --open-image omx_wiki/assets/superarm-isaac60-passive-linkage-open.png \
  --half-close-image omx_wiki/assets/superarm-isaac60-passive-linkage-half-close.png \
  --close-image omx_wiki/assets/superarm-isaac60-passive-linkage-close.png \
  --hand-license-file artifacts/amazinghand_distribution_audit/20260722/amazinghand_isaac_sim_usd_distribution_20260722/LICENSE-AmazingHandControl \
  --output-zip "artifacts/distributions/$DIST.zip" \
  --distribution-name "$DIST" \
  --validation-run-id 20260722T163556Z-motor2-flexion-fix
```

Accepted archive:

```text
artifacts/distributions/superarm_amazinghand_isaac_sim_usd_distribution_20260722_v2.zip
SHA256: 3bd316090d17f9903562139983a6c66731717f7246045ebdaf90610bf3e596d3
entry: usd/superarm_amazinghand/superarm_amazinghand.usda
```

After extraction, run `sha256sum -c SHA256SUMS` from the distribution
directory. The manifest and checksum inventory also bind the exact whole,
open, half-close, and close PNG bytes used by the E2E evidence runner. The
accepted clean-extraction validator result is retained under
`artifacts/distribution_validation/superarm_amazinghand_isaac_sim_usd_distribution_20260722_v2/`.

## Managed LeLab control bridge

`run_isaacsim60_control_bridge.sh` starts one long-lived Isaac Sim 6.0
container for a previously validated/extracted distribution. It mounts the
asset and Python package read-only, mounts only the session run directory
read-write, binds the authenticated JSONL bridge to localhost, and removes only
its uniquely named container.

```bash
install -d -m 0700 /tmp/superarm-isaac-session /tmp/superarm-isaac-secret
python3 - <<'PY' > /tmp/superarm-isaac-secret/token
import secrets
print(secrets.token_hex(32))
PY
chmod 0600 /tmp/superarm-isaac-secret/token

isaacsim_validation/run_isaacsim60_control_bridge.sh \
  --asset-root /path/to/extracted/distribution-root \
  --entrypoint /path/to/extracted/distribution-root/usd/superarm_amazinghand/superarm_amazinghand.usda \
  --run-dir /tmp/superarm-isaac-session \
  --host 127.0.0.1 --port 8765 \
  --token-file /tmp/superarm-isaac-secret/token
```

The bridge owns Isaac APIs on its main thread and supports `hello`, atomic
13-joint `command`, `observe`, `hold`, and `shutdown`. Commands are named by the
exact five-arm/eight-hand joint contract; articulation array order is never
trusted. The protocol retains a fail-closed `capture` operation for version
compatibility, but the Isaac Sim 6.0 runtime advertises
`supports_capture=false` and rejects it immediately. Live headless capture was
disabled after the available Replicator, legacy camera, experimental RTX
camera, viewport, and isolated child-Kit paths either returned no usable frame
or failed to terminate within their deadlines while the long-lived control
stage was active. It deliberately provides no continuous video loop, ROS 2
transport, external AmazingHand runtime, or real-hardware control.

## LeLab-controlled Isaac acceptance

Start LeLab with the validated distribution, then run the API-driven acceptance
tool. It exercises the same six-value service route used by manual/SO-101
teleoperation and future ACT/VLA policy code; the Isaac bridge expands those
values to the exact 13 named joints.

```bash
export SUPERARM_ISAAC_DISTRIBUTION_ZIP="$PWD/artifacts/distributions/superarm_amazinghand_isaac_sim_usd_distribution_20260722_v2.zip"
export ISAAC_SIM_STARTUP_TIMEOUT_S=240
uv run lelab --no-open

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-lelab-isaac"
uv run python -m isaacsim_validation.run_lelab_isaac_e2e \
  --base-url http://127.0.0.1:8000 \
  --distribution-zip "$SUPERARM_ISAAC_DISTRIBUTION_ZIP" \
  --run-dir "isaacsim_test/artifacts/lelab_isaac_e2e_${RUN_ID}" \
  --http-timeout-s 400
```

The runner accepts a case only after both the bridge command sequence advances
and every reported target matches the newly requested 13-joint vector. This
prevents stale telemetry from producing a false settled result. It verifies
open, half-close, and close targets; numeric arm/hand error limits; emergency
hold; the ten-second live-command timeout hold; managed-container cleanup; and
disconnect/reconnect.

Visual evidence is a separate proof category. The runner copies the already
validated static Isaac USD whole/open/half/close frames from `omx_wiki/assets`,
records `proof_category=prevalidated_static_isaac_visuals` and
`is_live_session_capture=false`, measures nonblank/difference gates, and builds
a GIF. These images verify the asset's visible poses; they are not presented as
frames from the live LeLab control session. In the website, the measured five
arm plus eight hand positions drive the URDF showroom while live Isaac viewport
capture remains unavailable.

## Evidence and proof boundaries

The accepted passive-linkage run is
`artifacts/isaacsim_superarm/20260722T163556Z-motor2-flexion-fix/`:

- `zip_learning_isaac/isaac-report.json`: runtime `PASS`, 13 DOFs, one
  articulation, six-value logical action, arm motion PASS, hand motion PASS,
  88 passive visual followers with 22 per finger, zero outer shells, and zero
  physics schemas on the visual followers;
- `zip_learning_isaac/asset-validator.json`: strict Isaac Sim 6.0 validator
  PASS with zero blocking issues;
- `zip_learning_isaac/whole_robot.png`: reviewed full robot with attached hand;
- `zip_learning_isaac/hand_open.png`, `hand_half_close.png`, and
  `hand_close.png`: reviewed, nonblank direct frames from measured physics
  snapshots, with adjacent RMS differences `25.1546` and `23.0963`;
- `zip_learning_isaac/hand_finger1_close.png` through
  `hand_finger4_close.png`: reviewed independent-finger evidence after each
  case resets to the measured open baseline;
- every reopened snapshot validates all 88 local transforms and source
  identities against its generated follower contract within `1e-6`.

This proves source integrity, USD composition, Isaac articulation response, and
visible open/close motion with source-informed passive-linkage geometry. It
does not prove real DM4340P CAN transport, AmazingHand serial transport,
closed-loop PhysX constraints, contact/grasp quality, or a trained policy
rollout. The runtime physics remains the existing eight-joint open-chain hand;
the supplied linkage members are visual-only followers solved from checked
offline source keyframes. Rounded `proximal_shell` and `distal_shell` parts
remain excluded and are intentionally deferred to a later cosmetic pass.

The successful live LeLab control report is retained at
`isaacsim_test/artifacts/lelab_isaac_e2e_20260722T112742Z/lelab-isaac-e2e-report.json`.
It reports Isaac Sim `6.0.0`, one articulation, 13 expected joint names, a
six-value logical action, maximum settled arm error `0.009771 rad`, maximum
settled hand error `0.000989 rad`, emergency/live-timeout hold stability beyond
120 physics steps, managed cleanup in `6.446 s`, and a passing reconnect. Its
PNG/GIF entries are explicitly the separate prevalidated static visual set.

## Required engineering record

After each Isaac USD problem is repaired and freshly verified, append the
symptom, evidence path, cause, repair, regression check, result/commit,
remaining boundary, and reusable rule to
[`isaacsim_usd_engineering_log.md`](../isaacsim_usd_engineering_log.md). A fix
is not considered documented until that cumulative entry exists; earlier
entries are preserved rather than rewritten.
