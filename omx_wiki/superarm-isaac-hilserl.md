# SuperArm Isaac HIL-SERL

## Implemented boundary

The V1 reinforcement-learning page is simulation-only and runs LeRobot's upstream HIL-SERL SAC actor/learner through a LeLab-owned Gymnasium environment registered as `gym_hil/SuperArmIsaacPickLift-v0`. It never edits LeRobot site-packages and always reports `is_intervention=false`.

The action contract is five normalized arm deltas (0.04 rad per control step, clamped to the existing joint limits) plus one categorical AmazingHand command (open, half-close, close). The observation is a fixed 23-value state vector and a 256 by 256 workspace RGB frame. Isaac advances twelve 120 Hz physics substeps per 10 Hz action.

The immutable V2 robot ZIP remains the source distribution. The runtime overlay authors the table, cube, camera, and palm/finger collision proxies in memory. The expected archive SHA-256 is `3bd316090d17f9903562139983a6c66731717f7246045ebdaf90610bf3e596d3`.

## Lifecycle

`GET /system/rl-readiness` checks the driver, Docker, Isaac 6.0 image, exact distribution checksum, actor/learner dependencies, ports, and the local-job lock. `POST /jobs/reinforcement-learning` launches one detached supervisor. It starts managed Isaac, then the learner, waits for the learner gRPC port, and only then starts the actor. Stop or failure terminates actor, learner, bridge, and the owned container. `/jobs/{id}/frame` serves only the bounded per-job PPM frame path.

Existing `/training` remains imitation learning. The new routes are `/reinforcement-learning` and `/reinforcement-learning/:jobId`.

## Verification status (2026-07-23 UTC)

- Contract and regression proof: PASS. Action mapping, state ordering, rewards, deterministic adapter reset, protocol validation, safe frame paths, upstream LeRobot config decoding, backend tests, 31 frontend tests, lint, and production build passed.
- Task/contact proof: BLOCKED. A real managed Isaac 6.0 container loaded the exact V2 distribution, validated the 13-DOF articulation, created the overlay, and opened the authenticated bridge. The standard Isaac camera RGB annotator did not return a 256 by 256 frame before `rl_reset` timed out. See `assets/superarm-isaac-rl-smoke/live-gate-report.json` and `host-smoke3.log`. Therefore zero/hold, collider contact, scripted grasp/lift, and repeated seeded reset are not claimed.
- Learner-update proof: NOT RUN. The actor cannot complete reset without a valid policy frame, so no 500-step SAC/checkpoint claim is made.
- Actual policy-improvement proof: NOT RUN. No 20,000-step learning result or successful learned lift is claimed.

The next runtime action is to repair the Isaac 6.0 offscreen camera render-product initialization, then rerun the gates in order; do not begin the long learner run until the reviewed close-up RGB and deterministic reset gates pass.
