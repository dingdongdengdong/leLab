---
title: "SuperArm plus AmazingHand USD validation in Isaac Sim 6.0"
tags: ["superarm", "isaac-sim", "usd", "amazinghand", "lerobot", "vla"]
created: 2026-07-22T01:19:58.784Z
updated: 2026-07-22T05:03:00.000Z
sources: []
links: ["superarm-real-hardware-motor-protocol-boundary.md"]
category: debugging
confidence: high
schemaVersion: 1
---

# SuperArm plus AmazingHand USD validation in Isaac Sim 6.0

## Durable decision

Use `zip_learning` for the Isaac/VLA/RL robot. It keeps the combined SuperArm
URDF as the only physics owner and uses the supplied Isaac USD distribution as
the detailed AmazingHand visual source.

Authoritative archive:

```text
/home/dong/july/superarm_ws/isaacsim_test/artifacts/distributions/
  amazinghand_isaac_sim_usd_distribution_20260722.zip
SHA256: 3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377
```

Do not nest the standalone hand articulation into the combined robot. Copy and
reference only its visual payloads beneath the existing wrist and finger links.

## Control contract

- Physical Isaac articulation: 13 movable joints, five arm plus eight hand.
- Logical LeRobot/VLA action: six values, five arm plus one grasp scalar.
- The grasp scalar expands to fixed eight-joint open/half-close/close targets.
- Exactly one articulation root owns the full arm and hand.

## Accepted evidence

Run ID: `20260722T051559Z-combined-zip-frame-first`.

- [Runtime report](assets/superarm-isaac60-zip-frame-first-report.json): PASS,
  13 DOFs, one articulation, five arm joints moved independently, and all eight
  hand joints moved monotonically with maximum error below `0.000063 rad`.
- [Strict validator](assets/superarm-isaac60-zip-frame-first-validator.json): PASS,
  zero blocking issues, one articulation root, 13 revolute joints, 28 rigid
  bodies, and 13 collisions.
- [Whole robot](assets/superarm-isaac60-zip-frame-first-whole.png): reviewed full
  SuperArm with the AmazingHand attached.
- [Open](assets/superarm-isaac60-zip-frame-first-open.png),
  [half close](assets/superarm-isaac60-zip-frame-first-half-close.png), and
  [close](assets/superarm-isaac60-zip-frame-first-close.png): reviewed direct
  fixed-camera frames from measured Isaac physics snapshots. Adjacent RMS
  differences are `22.0600` and `19.6275`.

The full ignored runtime artifact is under
`artifacts/isaacsim_superarm/20260722T051559Z-combined-zip-frame-first/`.
Reproduction instructions are in `isaacsim_validation/README.md`.

## Proof boundaries

This proves archive provenance, USD composition, one-articulation ownership,
Isaac joint response, clean-package validation, and visible grasp-state change.
It does not prove hardware transport, torque/current tuning, contact-quality
simulation, grasp success, or a trained ACT/VLA policy. The stable
wrist/palm/servo-frame assembly remains visual-only. Rounded proximal/distal
outer shells are deliberately excluded, while the supplied proximal/distal
cores follow the eight moving links.

## Historical Isaac frame lineage

Reference commit `0e53b0dfadaae3234d14fb5830108ae931734d0c` introduced the
Isaac-friendly four-finger open-chain model used by this project: two revolute
joints per finger, a `0.058 m` distal offset, and the same per-finger joint
origins and axes used by the current combined URDF. Its original 162-part
default visual shell was fixed to the wrist, and its CAD SimReady asset was a
validated prop rather than the controlled hand articulation. Therefore the
current asset follows that commit's frame/control topology and visual-versus-
physics separation, but uses the supplied Isaac USD distribution for the
shell-free moving visuals and retains one combined articulation owner.

## Engineering-log rule

Every later resolved Isaac USD problem must be appended to
`isaacsim_usd_engineering_log.md` with observed evidence, cause, smallest
repair, regression check, exact result/commit, remaining boundary, and reusable
rule. Do not delete earlier entries and do not write PASS before inspecting the
named evidence.
