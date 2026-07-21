---
title: "SuperArm follower calibration wizard"
tags: ["superarm", "dm4340p", "calibration", "lerobot", "safety", "amazinghand", "openarm"]
created: 2026-07-21T15:21:46.832Z
updated: 2026-07-21T15:35:00.960Z
sources: []
links: []
category: pattern
confidence: medium
schemaVersion: 1
---

# SuperArm follower calibration wizard

## Purpose
The website now has `/superarm-follower-calibration`, a non-torque configuration wizard for the real `SuperArm DM4340P + AmazingHand` follower. It is not LeRobot SO-101 follower calibration.

## Safety contract
The browser sends measured values to `POST /api/superarm/hardware-config/preview`. The endpoint validates only: it never opens CAN or serial, never enables torque, and explicitly reports `connects_hardware=false` and `motion_authorized=false`.

## Required measurements
The wizard requires exactly five records: `joint_rev_1` through `joint_rev_5`. Every record must contain unique DM4340P CAN send/receive IDs, direction (+1 or -1), zero offset in radians, lower/upper degree limits, `position_kp`, and `position_kd`. The operator must explicitly confirm values were measured. It also captures arm CAN port, AmazingHand serial port, and safe hand speed.

## Result
After validation, the browser downloads `superarm_dm4340p_amazinghand.yaml`; it does not write configs to the repository or hardware. The downloaded config is used later with the separate LeRobot hardware adapter and isolated safety tests.

---

## Update (2026-07-21T15:35:00.960Z)

## Purpose
`/superarm-follower-calibration` now follows the SO-101 calibration UX for the real five-joint `SuperArm DM4340P + AmazingHand` follower. It is not SO-101 follower calibration.

## OpenArm / Damiao integration
The live path uses LeRobot `OpenArmFollowerConfig` with the five custom DM4340P CAN ID pairs. It deliberately does **not** call `OpenArmFollower.connect()`, because that path enables torque. Instead `SuperArmCalibrationSession` opens the LeRobot Damiao bus, immediately disables torque, and then reads `Present_Position` while the operator moves joints manually.

## Guided sequence
1. Enter five custom CAN send/receive ID pairs and start the torque-disabled session.
2. Put the arm at the chosen reference pose and explicitly press Capture reference zero. This invokes Damiao's persistent zero command while torque remains disabled.
3. Move every joint manually through its safe range; the page shows live min/current/max degree readings like SO-101 calibration.
4. Finish/disconnect (torque remains disabled), complete direction/offset/limits/KP/KD values, and validate/download the local YAML.

## Safety contract
The live session always reports `torque_enabled=false`. It does not command targets or enable torque. Stop/unmount disconnects the CAN bus with torque disable. The configuration preview endpoint remains validation/download-only and never connects hardware.
