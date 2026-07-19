---
title: "SuperArm URDF AmazingHand showroom motion fix"
tags: ["superarm", "amazinghand", "mujoco", "urdf", "showroom", "lerobot"]
created: 2026-07-19T14:58:31.224Z
updated: 2026-07-19T14:58:31.224Z
sources: []
links: []
category: debugging
confidence: medium
schemaVersion: 1
---

# SuperArm URDF AmazingHand showroom motion fix

# SuperArm URDF AmazingHand showroom motion fix

Date: 2026-07-19
Branch: `feature/mujoco-web-lerobot`

## Diagnosis

MuJoCo hand motion was correct, but the normal LeLab URDF showroom looked stuck or wrong because the two-link showroom URDF uses positive distal finger joint limits while the official AmazingHand MuJoCo model flexes each `finger*_motor2` in the negative direction. Sending raw MuJoCo qpos into `urdf-loader` caused motor-2 joints to clamp at zero.

The cached generated URDF also had passive closed-loop hardware meshes on simplified moving proximal/distal links. That made rods/pins/linkage appear to tear away from the hand when the simplified serial tree moved.

## Fix

- Added a visualization-only MuJoCo-to-URDF hand projection: motor-1 remains positive and limited to the showroom range; motor-2 is negated and limited to `[0.0, 1.2]` only for viewer joint values.
- Preserved raw MuJoCo telemetry and the canonical 6D LeRobot action contract (`5 arm joints + amazinghand_motion`).
- Stabilized served URDF visuals: only `proximal(.stl)`, `proximal_shell(.stl)`, `distal(.stl)`, and `distal_shell(.stl)` remain on moving finger links; passive linkage/rod/pin visuals are reparented to `r_wrist_interface` at their zero-pose transforms.
- Applied the visual policy to both `/robots/{name}/urdf` and `/api/superarm/urdf` paths.

## Verification

- Targeted ruff on changed Python/tests: passed.
- Full Python pytest: `230 passed, 5 warnings`.
- Frontend Vitest: `2 files / 6 tests passed`.
- Frontend build: passed.
- Known unrelated gaps: full repo ruff still reports `UP017` in `lelab/datasets.py`; frontend ESLint still reports pre-existing issues in UI/components/tailwind files.
- Real cached URDF check: stabilizer moved 120 passive visuals; each moving finger proximal/distal link now serves exactly 2 segment meshes.
- Live `/robots/SuperArm%20%2B%20AmazingHand/urdf`: HTTP 200 and every moving finger link serves exactly the two segment meshes.
- Live `/ws/joint-data` settled close proof: 13 joints broadcast; all `finger*_motor2` values positive near `1.10`, all `finger*_motor1` near `0.95`.
- Browser visual proof: Xvfb + Firefox captured normal LeLab Teleoperation showroom with `13/13 physical joints live` across open, half, close.

## Artifacts

- Full GIF: `omx_wiki/assets/superarm-urdf-hand-open-half-close.gif`
- Close-up GIF: `omx_wiki/assets/superarm-urdf-hand-open-half-close-closeup.gif`
- Screenshots: `omx_wiki/assets/superarm-urdf-hand-open.png`, `omx_wiki/assets/superarm-urdf-hand-half.png`, `omx_wiki/assets/superarm-urdf-hand-close.png`
- Numeric reports: `omx_wiki/assets/superarm-urdf-hand-ws-settled-close-report.json`, `omx_wiki/assets/superarm-urdf-hand-capture-report.json`

## Boundary

This is a stable showroom approximation, not a full browser recreation of AmazingHand closed-loop passive kinematics. The MuJoCo panel remains the physical closed-loop reference.

