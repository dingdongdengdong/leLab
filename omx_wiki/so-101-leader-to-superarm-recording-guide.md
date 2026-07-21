---
title: "SO-101 leader to SuperArm recording guide"
tags: ["so101", "lerobot", "superarm", "mujoco", "amazinghand", "recording"]
created: 2026-07-21T14:48:57.876Z
updated: 2026-07-21T14:48:57.876Z
sources: []
links: []
category: pattern
confidence: medium
schemaVersion: 1
---

# SO-101 leader to SuperArm recording guide

## Contract
Use LeLab's existing physical `SO101Leader` recording path with the SuperArm MuJoCo follower first. The Manual Web Leader page is browser sliders only, not a physical SO-101.

## Step-by-step
1. Connect and calibrate the SO-101 leader through the existing calibration page.
2. Keep the follower in MuJoCo for the first episode; do not enable DM4340P torque.
3. Dashboard -> SuperArm + AmazingHand -> Record -> SO101 Leader.
4. Enter the leader serial port and calibration ID.
5. Record one short dry-run and inspect all six logical actions before using the real-follower checklist.

## Mapping
The leader arm maps `shoulder_pan`, `shoulder_lift`, `elbow_flex`, `wrist_flex`, and `wrist_roll` to `joint_rev_1` through `joint_rev_5`. `gripper.pos` selects a whole AmazingHand motion (`open`, `half_close`, or `close`) as `amazinghand_motion.pos`; it does not command the eight hand motors independently.

## Boundary
The website guide documents leader-to-MuJoCo recording. The protocol-safe DM4340P + AmazingHand hardware adapter remains a separate later real-follower integration and is not controlled by browser UI.
