---
title: "SuperArm leader to follower progression guide"
tags: ["so101", "lerobot", "superarm", "mujoco", "isaac-sim", "amazinghand", "recording", "hardware"]
created: 2026-07-21T14:48:57.876Z
updated: 2026-07-23T00:00:00.000Z
sources: []
links: ["superarm-follower-calibration-wizard.md", "superarm-website-real-hardware-readiness-page.md", "lelab-controlled-superarm-in-isaac-sim-6-0.md"]
category: pattern
confidence: high
schemaVersion: 1
---

# SuperArm leader to follower progression guide

## Current website truth

The intended progression has three stages, but the website currently completes
only the first two:

| Stage | Leader | Follower | Website status |
| --- | --- | --- | --- |
| 1 | Manual Web Leader sliders | MuJoCo or Isaac Sim software follower | Available |
| 2 | Physical SO-101 leader | MuJoCo first, then Isaac Sim software follower | Available |
| 3 | Physical SO-101 leader | Physical DM4340P SuperArm + AmazingHand | Preparation only |

The third row must not be described as working end-to-end. The protocol-safe
hardware robot adapter, five-joint calibration flow, configuration preview, and
readiness page exist. However, LeLab's website robot records and recording
factory currently register `superarm_mujoco` and `superarm_isaac`, not
`superarm_dm4340p_amazinghand`. Therefore the real follower cannot yet be
selected for website teleoperation or recording.

All three stages preserve the same policy-facing contract:

```text
[joint_rev_1, joint_rev_2, joint_rev_3, joint_rev_4, joint_rev_5,
 amazinghand_motion]
```

The final value selects a complete fixed hand motion—open, half-close, or
close. It does not expose eight independent AmazingHand joints to the leader or
policy.

## Stage 1 — manual slider leader to software follower

### Purpose

Prove the follower model, joint signs, limits, hand presets, emergency stop,
and six-value action contract without opening any physical motor bus.

### Direct-control steps

1. Start LeLab and select `SuperArm + AmazingHand` for MuJoCo or
   `SuperArm + AmazingHand (Isaac Sim)` for Isaac Sim.
2. Choose **Manual Web Leader** from the robot card.
3. Press **Start follower** before moving a slider.
4. Move one arm slider at a time and verify the matching `joint_rev_1` through
   `joint_rev_5` readback and showroom movement.
5. Test the complete hand poses in order: open, half-close, close, then open.
6. Use emergency stop once and confirm the follower holds its measured pose.
7. Stop the session before changing follower backends.

### Recording steps

1. Select the same simulation robot on the dashboard.
2. Choose **Record**.
3. Select **Manual Web Leader** as the input mode.
4. Record a short episode with isolated arm movements and all three hand poses.
5. Inspect the dataset: action and observation widths must remain six.

### Acceptance gate

- Five arm values move the matching five follower joints.
- One grasp value chooses a fixed AmazingHand motion.
- The simulation reports finite measured positions.
- No CAN, CAN-FD, or AmazingHand serial device is opened.

## Stage 2 — physical SO-101 leader to software follower

### Purpose

Replace the browser slider source with the real SO-101 leader while retaining
a safe software follower. MuJoCo is the first validation target because it is
lighter and easier to inspect; Isaac Sim follows after the MuJoCo dry run.

### Leader preparation

1. Connect only the SO-101 leader. Keep the physical SuperArm unpowered.
2. Open `/calibration`.
3. Select **Leader (Teleoperator)** and the SO-101 device type.
4. Complete the existing LeRobot SO-101 calibration and save its calibration
   ID.
5. Record the leader serial port, normally `/dev/ttyACM*` or `/dev/ttyUSB*`.

### MuJoCo dry run

1. Select `SuperArm + AmazingHand` on the dashboard.
2. Choose **Record**, then **SO101 Leader**.
3. Enter the SO-101 serial port and calibration ID.
4. Move each leader joint separately at low speed.
5. Confirm the fixed mapping:
   - `shoulder_pan.pos` → `joint_rev_1.pos`
   - `shoulder_lift.pos` → `joint_rev_2.pos`
   - `elbow_flex.pos` → `joint_rev_3.pos`
   - `wrist_flex.pos` → `joint_rev_4.pos`
   - `wrist_roll.pos` → `joint_rev_5.pos`
6. Move the SO-101 gripper through its range and confirm it selects open,
   half-close, and close rather than moving eight hand joints independently.
7. Record one short episode and inspect all six logical action values.

### Isaac Sim follow-up

1. Select `SuperArm + AmazingHand (Isaac Sim)` only after the MuJoCo episode
   passes.
2. Use the same **SO101 Leader** input, serial port, calibration ID, and mapping.
3. Confirm the validated Isaac distribution is loaded and the 6-to-13 action
   expansion reports five arm plus eight hand targets.
4. Treat the URDF showroom as measured-joint visualization. A WebRTC transport
   connection alone is not proof that the controlled robot is visible.

### Acceptance gate

- Existing SO-101 calibration loads without an unexpected recalibration.
- Every mapped joint has the intended sign, offset, and range.
- Gripper quantization is stable near open/half/close boundaries.
- The recorded action and observation shapes remain six-wide.
- The real follower remains disconnected and unpowered.

## Stage 3 — physical SO-101 leader to physical SuperArm follower

### Current status

**Preparation only. Do not expect a real-follower choice in the recording
modal yet.** The website exposes `/hardware-setup` and the SuperArm follower
calibration inside `/calibration`, but it does not yet create or run a physical
SuperArm robot record.

### Required preparation

1. Copy
   `lelab/superarm/data/superarm_dm4340p_amazinghand.example.yaml` outside the
   repository.
2. Replace every invalid placeholder with discovered values:
   - five unique DM4340P send CAN IDs;
   - five unique receive CAN IDs;
   - direction and `zero_offset_rad` for all five joints;
   - measured lower and upper limits;
   - five measured `position_kp` and `position_kd` values;
   - actual CAN/CAN-FD interface and bitrates.
3. Validate the arm alone with torque disabled, then with one torque-limited
   joint pulse at a time.
4. Validate the AmazingHand alone on its SCS0009 serial bus at 1,000,000 baud:
   IDs 1 through 8, readback, and fixed open/half/close motions.
5. Confirm emergency stop and the stale-command watchdog hold both work before
   a leader is allowed to command the follower.

### Missing website integration

Before stage 3 can be called available, the project still needs:

1. `superarm_dm4340p_amazinghand` added as a recognized website robot backend.
2. A clean built-in or user-created hardware robot record containing the
   measured configuration path.
3. Recording and teleoperation factories that instantiate
   `SuperArmDm4340PAmazingHandRobot` instead of falling through to SO-101
   follower assumptions.
4. UI selection that labels the follower as real hardware and requires an
   explicit motor-authorization step.
5. Hardware-in-loop tests for connect, calibrated readback, low-speed command,
   emergency stop, watchdog hold, partial arm/hand failure cleanup, disconnect,
   and reconnect.

Only after those five items pass should the website expose:

```text
SO-101 Leader -> SuperArm DM4340P + AmazingHand (Real)
```

## Safety and proof boundary

- Simulation PASS does not authorize real torque.
- A valid YAML preview does not prove the discovered CAN IDs or gains are safe.
- SO-101 calibration and SuperArm follower calibration are separate.
- DM4340P CAN/CAN-FD and AmazingHand SCS0009 serial remain separate protocols.
- The real hardware path requires readback and bounded failure cleanup; a sent
  command alone is not acceptance evidence.
