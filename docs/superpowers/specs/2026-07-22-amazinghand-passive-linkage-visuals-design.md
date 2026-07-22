# AmazingHand Passive-Linkage Visuals Design

## Goal

Reconstruct accurate shell-free AmazingHand passive-linkage motion in Isaac Sim
without changing the verified combined SuperArm physics or LeRobot action
contracts. Cosmetic proximal/distal shells are the final visual stage and are
not part of this work.

## Existing verified baseline

- One combined SuperArm plus AmazingHand articulation owns all physics.
- The articulation has 13 physical movable joints: five arm and eight hand.
- The LeRobot/VLA action remains six values: five arm values plus one grasp
  scalar expanded into eight hand-joint targets.
- Each finger uses the Isaac-friendly open-chain topology introduced by source
  commit `0e53b0dfadaae3234d14fb5830108ae931734d0c`:
  `palm -> proximal -> distal` with two revolute motors and a `0.058 m` distal
  offset.
- The supplied Isaac USD distribution is the hand visual source.
- The verified frame-first profile excludes `proximal_shell`, `distal_shell`,
  and the complete static presentation shell.

## Selected approach

Use a USD visual-follower linkage. The verified 13-DOF physics tree remains
unchanged. Passive linkage parts are visual-only and receive transforms derived
from each finger's two measured motor angles and the original AmazingHand
linkage anchors.

This approach is preferred over passive physics joints because it does not
reintroduce the original closed-loop constraint instability. It is preferred
over baked poses because it supports continuous policy motion rather than only
open/half-close/close presets.

## Visual component scope

The first linkage pass may include only shell-free mechanical parts whose
ownership and pivot relationship can be established from the supplied USD and
original AmazingHand model:

- servo horns;
- pins;
- rods and links;
- levers;
- gimbals;
- other small frame components with a proven linkage anchor.

The following remain disabled:

- `proximal_shell`;
- `distal_shell`;
- the complete fixed presentation shell;
- any part whose parent link or pivot cannot be proven.

No visual part may be promoted to collision or rigid-body physics by this
feature.

## Runtime architecture

1. The clean reusable robot USDA continues to contain the single combined
   articulation and the frame-first visual references.
2. Runtime reads the measured `finger*_motor1` and `finger*_motor2` values for
   each finger.
3. A deterministic follower solver computes each passive visual part's local
   transform from the measured motor angles and stored linkage anchors.
4. Computed transforms are authored into runtime evidence/snapshot layers, not
   into the clean reusable robot root.
5. The renderer opens those measured snapshot stages and captures direct
   fixed-camera evidence.

The follower solver must be isolated from Isaac application startup so that
its geometry and transform rules can be unit tested without Isaac Sim.

## Failure handling

- Unknown or ambiguous part ownership is a hard exclusion, not a guessed
  binding.
- Missing source prims or linkage anchors fail preparation with an actionable
  message.
- Non-finite transforms, impossible linkage lengths, or unsolved follower
  positions fail the visual evidence run.
- The reusable robot root must be restored byte-for-byte after runtime.
- A numeric PASS cannot upgrade a failed or missing visual review.

## Verification gates

### Static and unit tests

- Lock the exact allowlist of passive linkage source prims.
- Prove that no selected name contains `proximal_shell` or `distal_shell`.
- Test follower transforms at open, half-close, and close joint values.
- Check linkage-length invariants and finite transforms.
- Check that snapshot-only opinions never contaminate the clean robot root.

### Isaac numeric runtime

- Preserve 13 physical DOFs and one articulation root.
- Preserve the six-value logical LeRobot/VLA action.
- Confirm measured arm and hand targets continue to pass.

### Isaac visual runtime

- Capture open, half-close, and close frames with one fixed close-up camera.
- Capture independent motion evidence for all four fingers.
- Review for detached parts, wrong pivots, static followers, severe
  intersections, missing fingers, blank frames, and hidden outer shells.

### Asset validation

- Strict Isaac Sim 6.0 validation must report zero blocking issues.
- Passive linkage visuals must add no articulation roots, joints, rigid bodies,
  or colliders.

### Independent review

A verifier subagent must inspect the implementation, reports, strict validator,
and close-up visual evidence before the feature is accepted.

## Delivery and records

- Commit each verified feature slice.
- Append the resolved problem and exact evidence to
  `isaacsim_usd_engineering_log.md`.
- Update `omx_wiki` with durable decisions and reviewed artifacts.
- Push the feature branch after independent verification.

## Explicit proof boundaries

Passing this design proves shell-free passive-linkage visual following around
the verified open-chain physics hand. It does not prove closed-loop PhysX
physics, contact quality, object grasp retention, real AmazingHand hardware
transport, or final cosmetic shell fidelity.
