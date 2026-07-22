# SuperArm + AmazingHand on LeLab, MuJoCo, and LeRobot

This is an extension of the official
[`huggingface/leLab`](https://github.com/huggingface/leLab) application. The
normal LeLab workflow remains primary; `/superarm` is an additional MuJoCo
diagnostic dashboard.

## Website split

1. **Normal LeLab workflow (primary)**
   - the landing page selects `SuperArm + AmazingHand` when the built-in record
     is available;
   - Teleoperation uses LeLab's original Three.js URDF showroom;
   - Teleoperation keeps the manual/API control surface, while the normal
     recording modal selects either **Manual Web Leader** or **SO101 Leader**;
   - recording, training, and inference use LeRobot interfaces;
   - leaving Teleoperation stops the active runtime so re-entry can connect
     again.
2. **MuJoCo diagnostics (additional)**
   - `/superarm` renders the complete MuJoCo assembly and telemetry;
   - it retains pose, sequence, emergency-stop, and optional hand-serial tools;
   - it is not a replacement website or a second LeRobot action contract.

## Canonical LeRobot action

Policy, dataset, manual input, and SO101 adaptation all meet at exactly six
features, in this order:

```text
joint_rev_1.pos
joint_rev_2.pos
joint_rev_3.pos
joint_rev_4.pos
joint_rev_5.pos
amazinghand_motion.pos
```

`amazinghand_motion.pos` is quantized to fixed motions: `0.0` open, `0.5`
half-close, or `1.0` close. The robot expands that one value into the eight
physical AmazingHand actuator targets. SO101's five arm features map to the
five SuperArm joints, and `gripper.pos` maps to the same fixed motion value.

The action is therefore **6D**, not the 13 physical joints and not eight
independent hand commands.

## Focused runtime inputs

This branch runs without any alternate simulator backend. Supply the custom
assets explicitly:

```bash
export SUPERARM_ASSET_ROOT="$HOME/.cache/huggingface/lerobot/superarm/showroom"
export SUPERARM_URDF_PATH="$SUPERARM_ASSET_ROOT/superarm_amazinghand.urdf"
export SUPERARM_MUJOCO_MODEL_PATH="$HOME/.cache/huggingface/lerobot/amazinghand/model/superarm_amazinghand.xml"
MUJOCO_GL=egl lelab
```

The URDF and every referenced mesh must stay under `SUPERARM_ASSET_ROOT`; the
showroom endpoint allowlists only those files. LeLab preserves mesh file
extensions for Three.js and corrects the known
`wrist_adapter_to_amazinghand` display transform to the transform used by the
attached MuJoCo assembly. The input asset itself is not modified.

### Joint 5 motor-cover alignment

The generated asset originally placed `joint_rev_5` between `motor_5` and
`arm_link3b`, retaining a 25 mm fixed shell offset as the rotation pivot. That
caused the cover to move away from the motor. LeLab corrects both served URDF
and runtime MJCF non-destructively: `joint_rev_5` rotates
`arm_link2b -> motor_5` at `0.02 0 0.05` around `0 0 -1`, while
`motor_5 -> arm_link3b` remains fixed. The configured source files are not
rewritten.

## Recording input check

The normal LeLab recording modal exposes both supported control sources.
**Manual Web Leader** needs no serial device. **SO101 Leader** requires the
leader serial port and LeRobot calibration ID; its five joints and gripper are
adapted to the same canonical 6D action before the dataset writer. Physical
SO101 transport remains unverified until the device is connected.

Select **Manual Web Leader**, start a local recording with the
`superarm_mujoco` backend, and send six-value actions through
`POST /recording-action`. A valid episode has `action` and
`observation.state` arrays of width six; the sixth value is one of
`0.0`, `0.5`, or `1.0`.

The same schema is camera-ready for ACT/VLA policies. A state-only recording
proves the control and dataset boundary, not camera acquisition or a trained
policy rollout.

## MuJoCo dashboard API

- `GET /api/superarm/capabilities`
- `POST|DELETE /api/superarm/session`
- `PUT /api/superarm/action`
- `POST /api/superarm/emergency-stop`
- CRUD under `/api/superarm/poses` and `/api/superarm/sequences`
- `GET /api/superarm/video`
- `WS /ws/superarm`

The official closed-loop AmazingHand model retains eight position actuators
and 20 equality constraints. Its `motor2` hinge direction is negative for
flexion: open maps to `-0.02 rad` and 110 degrees maps to `-1.10 rad`.

## Validation boundary

MuJoCo, browser control, URDF rendering, and state-only LeRobot recording can
be validated without hardware. Physical source-arm motion, the eight-servo
hand transport, real cameras, and a trained ACT/VLA rollout remain unverified
until the corresponding devices and policy are present.

## Isaac Sim validation branch

Isaac validation is isolated from the MuJoCo website runtime. See
[`isaacsim_validation/README.md`](isaacsim_validation/README.md) for the
self-contained URDF packager, the Isaac Sim 6.0 container runner, and the
served/aligned/raw evidence boundary. The recommended `aligned` profile reuses
LeLab's existing joint-5 and AmazingHand attachment transforms while retaining
the detailed hand visuals that the browser-served URDF intentionally removes.
