---
title: "Exact AmazingHand MJCF visuals in the LeLab URDF showroom"
tags: ["superarm", "amazinghand", "mujoco", "urdf", "showroom", "lerobot", "exact-visuals"]
created: 2026-07-19T16:12:00.857Z
updated: 2026-07-19T16:12:00.857Z
sources: []
links: ["superarm-urdf-amazinghand-showroom-motion-fix.md"]
category: architecture
confidence: medium
schemaVersion: 1
---

# Exact AmazingHand MJCF visuals in the LeLab URDF showroom

# Exact AmazingHand MJCF visuals in the LeLab URDF showroom

Date: 2026-07-19  
Branch: `feature/mujoco-web-lerobot`  
Supersedes the approximation described in [[superarm-urdf-amazinghand-showroom-motion-fix]].

## Result

The normal LeLab URDF showroom remains the primary website, but its AmazingHand rendering now uses the exact official MuJoCo visual geometry and the exact runtime body transforms. The MuJoCo video remains a second comparison panel; this work did not introduce a separate site and contains no Isaac Sim path.

The browser keeps the configured arm URDF and its five scalar arm joints. Below `r_wrist_interface`, the server removes the old approximate hand visual elements while preserving the URDF joints used for the `13/13 physical joints live` status. A Three.js layer attached to `r_wrist_interface` then renders the official hand visual instances and applies root-relative MuJoCo body poses.

## Official source and provenance

- Upstream: `pollen-robotics/AmazingHand`
- Pinned upstream commit: `3e8241074df3436a3044ced4881e3bb2133aa725`
- Official right-hand `mjcf/robot.xml` SHA-256: `d21366e7c9a1f5debe04b8abb5ea1ade7fade42e493e09d003f5db196548b098`
- Configured combined model SHA-256: `bb79766bee7ce5f844b17871d6756028144897788b1318540a8caf7fc5215e54`
- Configured showroom URDF SHA-256: `bf7d183dcbfae1233c75350fcbcb2e9a385e38fd79466dacb9c25d8bf05c7c1e`

The manifest is generated from the configured compiled MuJoCo model rather than from a hand-written browser model. It compensates MuJoCo's mesh preprocessing offsets (`mesh_pos` and `mesh_quat`) so raw STL vertices reconstruct the same visual geometry at the same body-local pose.

## Runtime contract

- Root attachment: `r_wrist_interface`
- Coordinate frame: root-relative metres and quaternion WXYZ
- Exact hand bodies: 33
- Exact visual mesh instances: 162
- Unique mesh definitions: 23
- Equality constraints in the configured physical model: 20
- Pose telemetry: 20 Hz on both `/ws/superarm` and `/ws/joint-data`
- Browser smoothing: 50 ms interpolation with no extrapolation; last valid pose is retained across a temporary gap
- Asset routes: `/api/superarm/mujoco-visual-assets/{mesh}.stl` and record-scoped `/robots/{name}/mujoco-visual-assets/{mesh}.stl`

The LeRobot action contract is unchanged: `5 arm joints + 1 amazinghand_motion`. The last scalar selects the configured fixed AmazingHand grasp states; it does not expose eight independent hand-policy actions. Manual live verification exercised open (`0.0`), half (`0.5`), and close (`1.0`).

## Verification

### Unit and integration

- A binary-STL reconstruction test transforms raw `proximal_shell` vertices using the manifest and compares them with the compiled MuJoCo mesh under its geom transform; tolerance is `2e-6 m`.
- Targeted Python integration: `70 passed`.
- Frontend visual-layer tests: `12 passed`.
- TypeScript build, production build, changed-file ESLint, Ruff, Ruff format, and `git diff --check`: passed before live capture.

### Live browser

The production LeLab server was rebuilt and started with the configured combined URDF/MJCF, and Firefox/WebGL loaded the normal `/teleoperation?robot=SuperArm%20%2B%20AmazingHand` route.

- DOM/Three scene: 33 `mjcf-body:*` groups and 162 meshes.
- Missing-mesh fallback proxies: 0.
- Streamed telemetry bodies: 33.
- Maximum browser-vs-latest-backend body position error over open/half/close: `8.794e-6 m`.
- Maximum quaternion angular error: `0.01965 degrees`.
- All numeric live acceptance gates passed (`<=1 mm`, `<=1 degree`).
- The reviewed screenshots are non-blank and show the detailed official linkage/shell geometry bending through all three fixed grasp poses while remaining attached to the arm wrist.
- Delegated visual review: `96/100`, `PASS`. The reviewer confirmed connected detailed geometry and matching grasp silhouettes; the recorded limitation is unmatched camera/lighting/materials, so this evidence is qualitative rather than a pixel diff.

## Evidence

- GIF, LeLab + MuJoCo: `omx_wiki/assets/superarm-exact-amazinghand-lelab-mujoco.gif`
- LeLab open: `omx_wiki/assets/superarm-exact-amazinghand-open.png`
- LeLab half: `omx_wiki/assets/superarm-exact-amazinghand-half.png`
- LeLab close: `omx_wiki/assets/superarm-exact-amazinghand-close.png`
- Paired comparison frames: `omx_wiki/assets/superarm-exact-amazinghand-{open,half,close}-paired.jpg`
- Live browser numeric report: `omx_wiki/assets/superarm-exact-amazinghand-browser-report.json`
- Endpoint/count/hash report: `omx_wiki/assets/superarm-exact-amazinghand-endpoint-report.json`

## Boundaries

- Primary surface: existing LeLab URDF showroom.
- Secondary reference: MuJoCo renderer/video.
- Control: existing six-value LeRobot/manual contract.
- Not included: Isaac Sim, a new website, an independent 13-DOF policy action space, or synthetic replacement hand geometry.

