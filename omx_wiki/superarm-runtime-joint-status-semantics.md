---
title: "SuperArm Runtime Joint Status Semantics"
tags: ["lelab", "superarm", "amazinghand", "websocket", "urdf", "mujoco", "debugging"]
created: 2026-07-20T01:44:41.995Z
updated: 2026-07-20T01:44:41.995Z
sources: []
links: []
category: debugging
confidence: medium
schemaVersion: 1
---

# SuperArm Runtime Joint Status Semantics

## Finding
The earlier `0/13 physical joints live` capture was not an actual name mismatch. The normal LeLab teleoperation worker was inactive, so `/ws/joint-data` had an open socket but had not emitted a `joint_update`. When teleoperation is active, the backend emits exactly the configured 13 names: five `joint_rev_*` arm joints and eight `finger*_motor*` AmazingHand joints.

## Fix
`useRealTimeJoints` now distinguishes WebSocket connectivity from receipt of a real joint frame. The URDF viewer reports three truthful states:
- disconnected: socket is closed;
- awaiting: socket is open but no runtime joint sample has arrived;
- live/mismatch: at least one real sample arrived, so coverage can be evaluated.

A genuine `Runtime/URDF joint mismatch` is shown only after a received sample is missing configured names. This does not change the LeRobot policy contract or MuJoCo runtime: action space remains 5 arm DOF plus one fixed grasp scalar.

## Evidence
- Active teleoperation WebSocket sample: all 13 configured physical joint keys plus 33 exact visual-pose bodies.
- Live browser: `13/13 physical joints live`, exact hand visible — `omx_wiki/assets/superarm-joint-status-live.png`.
- Inactive browser: `Waiting for Robot Data` / `Waiting for runtime joint sample`, no false mismatch — `omx_wiki/assets/superarm-joint-status-awaiting.png`.
- Regression helper tests distinguish awaiting from a real 12/13 mismatch.
- Full validation: 20 frontend tests, TypeScript, changed-file ESLint, production build, and 235 Python tests.
