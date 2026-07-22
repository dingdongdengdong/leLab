# SuperArm URDF validation in Isaac Sim 6.0

This directory validates the same custom SuperArm + AmazingHand source used by
LeLab. It is intentionally separate from the MuJoCo website runtime: Isaac Sim
is a conversion and physics-verification target, not another LeLab backend.

## What is tested

- every referenced mesh is copied into a self-contained package and recorded
  with size and SHA-256 metadata;
- Isaac's 6.0 URDF importer produces a fixed-base USD articulation;
- the imported articulation contains 13 movable joints: five arm joints and
  eight AmazingHand motor joints;
- each arm joint moves independently;
- the LeRobot-facing six-value action contract is preserved: five arm values
  plus one fixed grasp value expanded to all eight hand targets;
- open, half-close, and close targets move monotonically in simulation;
- screenshots are rejected when the frame contains no visible detail.

## Three asset profiles

| Profile | Purpose | Detailed hand visuals |
| --- | --- | --- |
| `served` | Exact URDF tree returned by the current LeLab showroom endpoint | Removed because the browser overlays the official AmazingHand MJCF bodies |
| `aligned` | Recommended Isaac profile: applies LeLab's existing joint-5 and wrist attachment transforms but keeps the generated URDF hand visuals | Present and attached |
| `raw` | Diagnostic copy of the generated source without LeLab transforms | Present, but the current source mount places the hand about 0.6 m above the wrist |

The `aligned` profile is not a new robot definition. It reuses the two
non-destructive transforms already used by `lelab.superarm.showroom`, while
omitting only `remove_amazinghand_visuals()` so Isaac can render the hand.

## Run

The tested runtime is `nvcr.io/nvidia/isaac-sim:6.0.0` with NVIDIA Container
Toolkit GPU access.

```bash
export SOURCE_WS=/home/dong/july/superarm_ws
export SOURCE_URDF="$SOURCE_WS/isaacsim_test/outputs/robot_arm_hand_from_zip_local_drive/superarm_amazinghand.urdf"
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"

for profile in served aligned raw; do
  uv run --extra dev python -m isaacsim_validation.prepare_superarm_urdf \
    --source-urdf "$SOURCE_URDF" \
    --source-root "$SOURCE_WS" \
    --output-dir "artifacts/isaacsim_superarm/$RUN_ID/${profile}_input" \
    --profile "$profile"
done

isaacsim_validation/run_isaacsim60_validation.sh aligned "$RUN_ID"
isaacsim_validation/run_isaacsim60_validation.sh served "$RUN_ID"
```

Each run writes `isaac-report.json`, the imported USD package, `isaac.log`, and
reviewable PNG evidence below
`artifacts/isaacsim_superarm/<run-id>/<profile>_isaac/`.

## Interpreting the result

A runtime `PASS` proves URDF-to-USD import, articulation schema, collision
presence, and commanded joint motion in Isaac Sim. It does not prove the real
DM4340P CAN transport, AmazingHand serial transport, contact-quality tuning, or
a trained ACT/VLA rollout. The generated hand shell is also only a serial URDF
approximation of AmazingHand's closed-loop mechanism; the LeLab browser keeps
using the official MJCF visual overlay for that reason.
