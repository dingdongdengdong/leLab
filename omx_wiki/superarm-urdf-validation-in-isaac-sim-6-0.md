---
title: "SuperArm URDF validation in Isaac Sim 6.0"
tags: ["superarm", "isaac-sim", "urdf", "amazinghand", "lerobot"]
created: 2026-07-22T01:19:58.784Z
updated: 2026-07-22T01:25:40.808Z
sources: []
links: ["exact-amazinghand-mjcf-visuals-in-the-lelab-urdf-showroom.md", "superarm-real-hardware-motor-protocol-boundary.md"]
category: debugging
confidence: medium
schemaVersion: 1
---

# SuperArm URDF validation in Isaac Sim 6.0

# SuperArm URDF validation in Isaac Sim 6.0

## Decision

Isaac Sim validation is isolated on `feature/isaacsim-superarm-urdf-validation`; the MuJoCo website branch remains free of Isaac runtime code. The source robot is the existing generated `superarm_amazinghand.urdf`, not a replacement asset.

Use three explicit profiles:

- **served**: applies the same LeLab transforms and removes the hand visuals because the website overlays official MJCF hand bodies;
- **aligned**: recommended for Isaac, applying the same joint-5 and `wrist_adapter_to_amazinghand` transforms while retaining all URDF visuals;
- **raw**: source diagnostic only, preserving the generated 0.600003 m attachment offset that visibly detaches the hand.

## Control contract

The imported articulation has 13 physical movable joints, but the LeRobot action remains six logical values: `joint_rev_1` through `joint_rev_5` plus `amazinghand_motion`. Open, half-close, and close expand the one grasp scalar to the eight `fingerN_motorM` targets. Do not expose 13 independent policy actions.

## Verified evidence

Run ID: `20260722T002134Z`.

- `served_isaac/isaac-report.json`: PASS; 13 DOFs, 36 mesh prims, 28 rigid bodies, 31 collision prims, all arm and hand motions passed. Its screenshot intentionally shows no detailed fingers.
- `raw_isaac/whole_robot.png`: diagnostic evidence that the unmodified source hand is rendered but detached above the wrist.
- `aligned_isaac/isaac-report.json`: authoritative Isaac result; 13 DOFs, 198 mesh prims, 28 rigid bodies, 31 collision prims, five independent arm moves, and monotonic open/half/close hand motion.
- `aligned_isaac/whole_robot.png`: reviewed nonblank frame with the detailed AmazingHand attached to the arm.
- `aligned_isaac/hand_closeup.png`: labeled crop derived from the same Isaac frame; the visual helper rejects uniform/blank images.

Ignored runtime artifacts are under `artifacts/isaacsim_superarm/20260722T002134Z/`. Reproduction commands and proof boundaries live in `isaacsim_validation/README.md`.

## Limits

This proves URDF packaging, Isaac URDF-to-USD import, articulation schema, collisions, drive response, and visible aligned geometry. It does not prove physical DM4340P CAN, AmazingHand serial transport, contact-quality tuning, or an ACT/VLA policy rollout. The hand remains a serial URDF approximation of the official closed-loop MJCF mechanism.

Related: [[exact-amazinghand-mjcf-visuals-in-the-lelab-urdf-showroom]] and [[superarm-real-hardware-motor-protocol-boundary]].

---

## Update (2026-07-22T01:25:40.808Z)

## Decision

Isaac Sim validation is isolated on `feature/isaacsim-superarm-urdf-validation`; the MuJoCo website branch remains free of Isaac runtime code. The source robot is the existing generated `superarm_amazinghand.urdf`, not a replacement asset.

Use three explicit profiles:

- **served**: applies the same LeLab transforms and removes the hand visuals because the website overlays official MJCF hand bodies;
- **aligned**: recommended for Isaac, applying the same joint-5 and `wrist_adapter_to_amazinghand` transforms while retaining all URDF visuals;
- **raw**: source diagnostic only, preserving the generated 0.600003 m attachment offset that visibly detaches the hand.

## Control contract

The imported articulation has 13 physical movable joints, but the LeRobot action remains six logical values: `joint_rev_1` through `joint_rev_5` plus `amazinghand_motion`. Open, half-close, and close expand the one grasp scalar to the eight `fingerN_motorM` targets. Do not expose 13 independent policy actions.

## Verified evidence

Run ID: `20260722T002134Z`.

- [`served` screenshot](assets/superarm-isaac60-served-no-hand.png): 13-DOF physics PASS; detailed fingers are intentionally absent.
- [`raw` screenshot](assets/superarm-isaac60-raw-detached-hand.png): the unmodified source hand renders but is detached above the wrist.
- [`aligned` report](assets/superarm-isaac60-aligned-report.json): authoritative Isaac PASS with 13 DOFs, 198 mesh prims, 28 rigid bodies, 31 collision prims, five independent arm moves, and monotonic open/half/close hand motion.
- [`aligned` whole robot](assets/superarm-isaac60-aligned-whole.png): reviewed nonblank frame with the detailed AmazingHand attached.
- [`aligned` hand close-up](assets/superarm-isaac60-aligned-hand-closeup.png): labeled crop derived from the same Isaac frame; the visual helper rejects uniform or blank images.

Ignored full runtime artifacts are under `artifacts/isaacsim_superarm/20260722T002134Z/`. Reproduction commands and proof boundaries live in `isaacsim_validation/README.md`.

## Limits

This proves URDF packaging, Isaac URDF-to-USD import, articulation schema, collisions, drive response, and visible aligned geometry. It does not prove physical DM4340P CAN, AmazingHand serial transport, contact-quality tuning, or an ACT/VLA policy rollout. The hand remains a serial URDF approximation of the official closed-loop MJCF mechanism.

Related: [[exact-amazinghand-mjcf-visuals-in-the-lelab-urdf-showroom]] and [[superarm-real-hardware-motor-protocol-boundary]].

