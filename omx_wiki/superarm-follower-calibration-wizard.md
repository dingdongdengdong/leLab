---
title: "SuperArm follower calibration wizard"
tags: ["superarm", "dm4340p", "calibration", "lerobot", "safety", "amazinghand", "openarm"]
created: 2026-07-21T15:21:46.832Z
updated: 2026-07-21T16:04:00.000Z
sources: []
links: []
category: pattern
confidence: medium
schemaVersion: 1
---

# SuperArm follower calibration wizard

## Location and device semantics
SuperArm calibration is integrated into LeLab's existing `/calibration` page. Select `SuperArm (DM4340P Follower)` in the same Device Type selector used for SO-101 leader/follower calibration. There is no separate SuperArm calibration route or query parameter.

The page uses a device-type metadata list and a reusable checklist renderer. In SuperArm mode it explicitly shows:

- `Leader: SO-101 teleoperator (optional)`
- `Follower: SuperArm 5-DOF DM4340P + AmazingHand grasp`

This makes the control contract clear: SO-101 is an optional leader source, while the calibrated follower is the five-joint SuperArm. AmazingHand uses one logical fixed-grasp command rather than exposing its eight internal joints as arm calibration axes.

## OpenArm / Damiao integration
The SuperArm live path uses LeRobot `OpenArmFollowerConfig` with the five custom DM4340P CAN ID pairs. It deliberately does **not** call `OpenArmFollower.connect()`, because that path enables torque. Instead `SuperArmCalibrationSession` opens the LeRobot Damiao bus, immediately disables torque, and then reads `Present_Position` while the operator moves joints manually.

## Guided sequence
1. On `/calibration`, select SuperArm and enter five custom CAN send/receive ID pairs.
2. Press the existing Start Calibration button; the torque-disabled session begins.
3. Put the arm at the reference pose and use Capture reference zero from the existing status panel.
4. Move every joint manually through its safe range; the existing status panel shows live min/current/max degree readings.
5. Finish/disconnect (torque remains disabled), then complete direction/offset/limits/KP/KD values and validate/download the local YAML.

## Safety contract
The live session always reports `torque_enabled=false`. It does not command targets or enable torque. Stop/unmount disconnects the CAN bus with torque disable. The configuration preview endpoint remains validation/download-only and never connects hardware.
