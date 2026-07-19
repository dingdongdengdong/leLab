---
title: "SuperArm URDF AmazingHand showroom motion fix"
tags: ["superarm", "amazinghand", "mujoco", "urdf", "showroom", "lerobot"]
created: 2026-07-19T14:58:31.224Z
updated: 2026-07-19T15:20:00.000Z
sources: []
links: []
category: debugging
confidence: high
schemaVersion: 1
---

# SuperArm URDF AmazingHand showroom motion fix

Date: 2026-07-19
Branch: `feature/mujoco-web-lerobot`

> Historical stage: this approximate visual policy was superseded by
> [[exact-amazinghand-mjcf-visuals-in-the-lelab-urdf-showroom]], which renders
> the exact official MJCF/STL visual instances and streamed body transforms.

## Diagnosis

MuJoCo hand motion was correct, but the normal LeLab URDF showroom looked stuck or wrong because the two-link showroom URDF uses positive distal finger joint limits while the official AmazingHand MuJoCo model flexes each `finger*_motor2` in the negative direction. Sending raw MuJoCo qpos into `urdf-loader` caused motor-2 joints to clamp at zero.

The cached generated URDF also put closed-loop CAD meshes on simplified moving proximal/distal links. Live browser capture proved that those meshes orbit away from their motor pivots when driven as a two-link serial tree, so numeric joint success alone was not a valid visual pass.

## Fix

- Added a visualization-only MuJoCo-to-URDF hand projection: motor-1 remains positive and limited to the showroom range; motor-2 is negated and limited to `[0.0, 1.2]` only for viewer joint values.
- Preserved raw MuJoCo telemetry and the canonical 6D LeRobot action contract (`5 arm joints + amazinghand_motion`).
- Replaced the incompatible moving closed-loop CAD with joint-local rounded showroom segments derived from the existing contact dimensions. Each proximal link serves one cylinder; each distal link serves one cylinder plus its tip sphere.
- Removed the moving linkage/rod/pin meshes from the simplified URDF view instead of presenting them as detached hardware. The MuJoCo panel remains the physical closed-loop reference.
- Applied the visual policy to both `/robots/{name}/urdf` and `/api/superarm/urdf` paths.

## Verification

- Targeted ruff on changed Python/tests: passed.
- Full Python pytest: `230 passed, 5 warnings`.
- Frontend Vitest: `2 files / 6 tests passed`.
- Frontend build: passed.
- Known unrelated gaps: full repo ruff still reports `UP017` in `lelab/datasets.py`; frontend ESLint still reports pre-existing issues in UI/components/tailwind files.
- Real cached URDF check: stabilizer removes 136 incompatible moving mesh visuals; the second pass removes 0, proving idempotence.
- Live `/robots/SuperArm%20%2B%20AmazingHand/urdf`: HTTP 200; every proximal link serves one joint-local cylinder, and every distal link serves one cylinder plus one tip sphere, with no moving CAD mesh.
- Live `/ws/joint-data` settled close proof: 13 joints broadcast; all `finger*_motor2` values positive near `1.10`, all `finger*_motor1` near `0.95`.
- Browser visual proof: Xvfb + Firefox captured normal LeLab Teleoperation showroom with `13/13 physical joints live` across open, half, close. Reviewed frames show all four rounded fingers remain attached at their motor bases with no floating shells or passive rods.
- Delegated structured visual verdict: `92/100`, `PASS`, above the Ralph threshold of 90.

## Artifacts

- Full GIF: `omx_wiki/assets/superarm-urdf-hand-open-half-close.gif`
- Close-up GIF: `omx_wiki/assets/superarm-urdf-hand-open-half-close-closeup.gif`
- Screenshots: `omx_wiki/assets/superarm-urdf-hand-open.png`, `omx_wiki/assets/superarm-urdf-hand-half.png`, `omx_wiki/assets/superarm-urdf-hand-close.png`
- Numeric reports: `omx_wiki/assets/superarm-urdf-hand-ws-settled-close-report.json`, `omx_wiki/assets/superarm-urdf-hand-capture-report.json`
- Topology report: `omx_wiki/assets/superarm-urdf-hand-proxy-topology-report.json`
- Visual verdict: `omx_wiki/assets/superarm-urdf-hand-visual-verdict.json`

## Boundary

This is a stable joint-local showroom approximation for the `5 arm + 1 grasp` control contract, not a browser recreation of AmazingHand closed-loop passive kinematics. The MuJoCo panel remains the physical closed-loop reference.
