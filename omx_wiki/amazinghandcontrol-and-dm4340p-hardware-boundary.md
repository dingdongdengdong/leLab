---
title: "AmazingHandControl and DM4340P hardware boundary"
tags: ["superarm", "amazinghand", "lerobot", "dm4340p", "openarm", "hardware"]
created: 2026-07-20T02:34:35.834Z
updated: 2026-07-20T02:34:35.834Z
sources: []
links: []
category: decision
confidence: medium
schemaVersion: 1
---

# AmazingHandControl and DM4340P hardware boundary

## Verified protocol split

This branch now treats the physical arm and AmazingHand as different buses and does not substitute one protocol for the other.

### AmazingHand

The hand follows [AmazingHandControl](https://github.com/Betatester777/AmazingHandControl): Feetech SCS0009 on a serial bus, Rustypot `Scs0009PyController`, default `/dev/ttyACM0`, 1,000,000 baud, eight IDs, and speed scale 1..6. The authoritative pair/order split is:

- UI/name map: Pointer `(1,2)`, Middle `(3,4)`, Ring `(5,6)`, Thumb `(7,8)`.
- Upstream pose-array order: Ring `(5,6)`, Middle `(3,4)`, Pointer `(1,2)`, Thumb `(7,8)`.
- Even servo IDs invert sign for command and feedback conversions.
- Logical VLA/LeRobot action remains five arm controls plus one fixed grasp code. The grasp expands to eight centered SCS0009 targets for open/half/close and is not an eight-dimensional policy action.

### Arm

The physical arm path is a separate LeRobot OpenArm/Damiao adapter. DM4340P is represented by LeRobot motor type `dm4340` and is carried on CAN/CAN-FD (OpenArm convention: 1 Mbps nominal, 5 Mbps data for CAN-FD), not the hand serial bus.

`SuperArmDm4340PAmazingHandRobot` composes LeRobot `OpenArmFollower` with `SerialAmazingHandTransport`. It requires, before torque enable:

1. five explicit custom send/receive CAN-ID pairs; no OpenArm sample IDs are inherited; send and receive sets must be disjoint,
2. all five joint direction and zero-offset-radian calibrations,
3. all five measured degree limits and MIT Kp/Kd gains,
4. the separate AmazingHand port, baud rate, and speed.

The checked-in `superarm_dm4340p_amazinghand.example.yaml` deliberately uses invalid zero CAN IDs, so it cannot drive hardware until the actual arm has been discovered and calibrated. The `superarm` extra now includes `python-can`; base LeLab installs do not acquire the CAN dependency.

### Website boundary

The website is intentionally unchanged: it supports MuJoCo, and `hybrid_serial` remains MuJoCo arm plus real SCS0009 hand. The new real-hardware LeRobot robot is a dedicated adapter for later calibrated hardware/recording integration; it is not exposed as a browser runtime yet. Therefore no website operation is claimed to control the physical DM4340P arm.

### Offline evidence

Protocol tests cover servo order/inversion, CAN-ID validation, calibrated radians/degrees round-tripping, six-action routing to five CAN arm targets plus one eight-servo hand pose, and arm teardown when the hand connection fails. Hardware discovery, calibration, and motion tests remain required on the connected robot.
