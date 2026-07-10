# SuperArm + AmazingHand dashboard

LeLab exposes `/superarm`, a unified controller for the five-joint source arm
and the official closed-loop AmazingHand MJCF model.

## Runtime boundaries

- **MuJoCo** is the default complete v1 runtime.
- **Hybrid serial** keeps the arm simulated and controls an eight-servo physical
  AmazingHand through `rustypot` while mirroring commands into MuJoCo.
- **Isaac Sim — later** is deliberately disabled. Existing Isaac/ROS routes and
  the preserved SimReady path are unchanged.
- A real source-arm transport is not implemented in v1; the `ArmTransport`
  interface is the replacement point.

The AmazingHand is not converted to the simplified URDF hand. The source arm's
URDF is converted to MJCF, then the official `r_wrist_interface` body, eight
position actuators, and 20 equality constraints are copied from
`hand_mjcf/robot.xml` into the combined model.

## Install and run

```bash
uv sync --extra superarm
MUJOCO_GL=egl lelab
```

Open `http://localhost:8000/superarm`. The model generator locates the SuperArm
workspace through `robot_arm_hand_package.zip`; pass `workspace_root` to the
session API if it is not discoverable.

Generated portable model inputs are cached under
`~/.cache/huggingface/lerobot/amazinghand/model/`. Programs are atomically
persisted at `~/.cache/huggingface/lerobot/amazinghand/programs.yaml`.

On first use, bundled AmazingHandControl defaults from inspected revision
`2a59fd8` are translated from Ring/Middle/Pointer/Thumb arrays into named hand
fields.

## API

- `GET /api/superarm/capabilities`
- `POST|DELETE /api/superarm/session`
- `PUT /api/superarm/action`
- `POST /api/superarm/emergency-stop`
- CRUD under `/api/superarm/poses` and `/api/superarm/sequences`
- sequence play, pause, and stop endpoints
- `GET /api/superarm/video` (640×480 MJPEG at 15 FPS)
- `WS /ws/superarm` (10 Hz state, telemetry, runtime, and program events)

## Safety

Commands are rejected while disconnected or emergency-stopped. Live commands
are capped at 20 Hz and stop after ten seconds without input. Serial mode scans
`/dev/ttyACM*` and `/dev/ttyUSB*`, defaults to `/dev/ttyACM0` at 1,000,000 baud,
requires all servo IDs 1–8, preserves even-servo inversion, and disables torque
on stop, emergency stop, disconnect, or stale telemetry.

Physical serial hardware was not attached during this implementation. Do not
claim hardware validation until the opt-in eight-servo discovery, motion,
telemetry, timeout, and torque-disable checks pass on the actual hand.


## Official MJCF motor direction

UI degrees remain positive and hardware-compatible. In the official MJCF,
`motor2` uses the opposite hinge direction for flexion: open maps to `-0.02 rad`
and 110 degrees maps to `-1.10 rad`. Applying both motors as positive radians
mostly spreads the linkage sideways and is covered by the fingertip-motion
regression test.
