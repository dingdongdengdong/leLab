---
title: "SuperArm real-hardware motor protocol boundary"
tags: ["superarm", "amazinghand", "lerobot", "hardware", "motor-protocol"]
created: 2026-07-20T02:25:26.374Z
updated: 2026-07-20T02:25:26.374Z
sources: []
links: []
category: decision
confidence: medium
schemaVersion: 1
---

# SuperArm real-hardware motor protocol boundary

## Verdict

The current focused LeLab branch is **MuJoCo-ready and AmazingHand-hand-serial-aware**, but it is **not yet a complete real SuperArm + AmazingHand LeRobot hardware driver**. Do not use `hybrid_serial` as evidence that the physical arm is controlled.

## Current contract

- Policy/recording action space is deliberately six controls: five `joint_rev_*` arm values and one `amazinghand_motion` code.
- The final code resolves only to fixed `open` (0 degrees), `half_close` (55 degrees), or `close` (110 degrees) poses. It expands to the eight physical AmazingHand servos; it is not an eight-dimensional VLA action.
- The simulator is responsible for all five arm joints and the visual/physics hand.

## AmazingHand hardware path

`SerialAmazingHandTransport` is separate from the simulated arm runtime:

- Uses Rustypot's `Scs0009PyController` on a distinct serial port, default `/dev/ttyACM0`, at 1,000,000 baud.
- Discovers and commands servo IDs 1 through 8, uses per-servo speed writes plus a synchronized goal-position write, and reads telemetry.
- Preserves the necessary direction convention: even-numbered servos are sign-inverted before hardware position writes and after reads.
- Pings every servo and torque-disables all eight on connect failure, telemetry loss, stop, or emergency stop.

## What `hybrid_serial` means today

`service.start_session("hybrid_serial")` always starts `MuJoCoRuntime`; it optionally adds `SerialAmazingHandTransport` for the physical hand. The action path sends arm targets to MuJoCo and sends hand poses to both MuJoCo and the physical hand. There is currently no physical-arm transport, arm motor IDs, arm bus port, arm motor-model/protocol configuration, or arm calibration mapping for `joint_rev_1` through `joint_rev_5`.

The stock LeLab SO-101 integration is separate and has its own STS3215/Feetech assumptions. Do not reuse that bus/protocol configuration for AmazingHand's SCS0009 bus.

## Required gate before real combined control

1. Add an explicit physical-arm transport/configuration: distinct port, motor IDs, motor model/protocol, baud rate, calibrated zero/direction/limits, and torque safety.
2. Compose that arm transport with `SerialAmazingHandTransport` in a real-hardware `Robot` implementation while preserving the six-control LeRobot action contract.
3. Keep hand state/telemetry separate from the logical VLA action; map the final scalar only to validated named hand poses.
4. Validate on connected hardware in this order: non-torque discovery, individual arm calibration, individual 8-servo hand open/half/close command plus readback, then a torque-limited combined pulse. Record the per-device calibration artifacts.

## Evidence

Targeted protocol/mapping/API tests passed locally: 27 passed. These tests prove simulator mapping, six-control validation, hand servo direction conversion, and safe simulated session behavior. They do not prove a real motor bus, calibration, or physical arm command.
