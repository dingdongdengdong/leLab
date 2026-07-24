# SuperArm Isaac HIL-SERL

## Implemented boundary

The V1 reinforcement-learning page is simulation-only and runs LeRobot's upstream HIL-SERL SAC actor/learner through a LeLab-owned Gymnasium environment registered as `gym_hil/SuperArmIsaacPickLift-v0`. It never edits LeRobot site-packages and always reports `is_intervention=false`.

The action contract is five normalized arm deltas (0.04 rad per control step, clamped to the existing joint limits) plus one categorical AmazingHand command (open, half-close, close). The observation is a fixed 23-value state vector and a 256 by 256 workspace RGB frame. Isaac advances twelve 120 Hz physics substeps per 10 Hz action.

The immutable V2 robot ZIP remains the source distribution. The runtime overlay authors the table, cube, camera, and palm/finger collision proxies in memory. The expected archive SHA-256 is `3bd316090d17f9903562139983a6c66731717f7246045ebdaf90610bf3e596d3`.

## Lifecycle

`GET /system/rl-readiness` checks the driver, Docker, the Isaac Sim 6.0.1 image, the RL X11 display, exact distribution checksum, actor/learner dependencies, ports, and the local-job lock. `POST /jobs/reinforcement-learning` launches one detached supervisor. It starts managed Isaac, then the learner, waits for the learner gRPC port, and only then starts the actor. Stop or failure terminates actor, learner, bridge, and the owned container. `/jobs/{id}/frame` serves only the bounded per-job PPM frame path.

The RGB path is deliberately isolated from the existing 6.0.0 WebRTC/teleoperation path. RL uses Isaac Sim 6.0.1 as the image's non-root UID, Xvfb on `:100`, persistent Omni/Kit/RTX caches, asynchronous throttling disabled, and a blocking Replicator RGB annotator step. Because stopping the timeline for a reliable Replicator capture invalidates PhysX tensor views, the bridge snapshots the articulation and cube state, recreates the physics view, and restores that state before accepting the next atomic action.

Existing `/training` remains imitation learning. The new routes are `/reinforcement-learning` and `/reinforcement-learning/:jobId`.

## Verification status (2026-07-23 UTC)

- Contract and regression proof: PASS. Action mapping, state ordering, rewards, deterministic adapter reset, protocol validation, safe frame paths, upstream LeRobot config decoding, and the targeted 70-test Isaac/RL suite passed.
- Real task-scene/RGB proof: PASS. The engineering-log recipe in `/home/dong/ai/.worktrees/synthetic-steel-data/synthetic_steel_sdg_engineering_log.md` was reproduced and adapted to the managed RL bridge. A real non-root Isaac Sim 6.0.1 container loaded the exact V2 distribution, validated the 13-DOF articulation, created the overlay, and completed authenticated `rl_reset(seed=42)` plus one atomic hold `rl_step`. Both descriptors were 256 by 256 RGB, both state vectors had width 23, both reported `is_intervention=false`, and both reviewed images visibly contain the SuperArm, table, and yellow cube. See [the reset frame](assets/superarm-isaac-rl-rgb-smoke-20260723/reset-seed-42.png), [the hold-step frame](assets/superarm-isaac-rl-rgb-smoke-20260723/hold-step.png), and [the JSON report](assets/superarm-isaac-rl-rgb-smoke-20260723/report.json).
- Runtime-throughput proof: NOT YET PASS. The verified warmed hold step took 0.458 seconds, so the 10 Hz actor-control target still needs a capture-path performance pass even though correctness and cleanup now pass.
- Contact/grasp-and-lift proof: NOT RUN. The camera and atomic step blocker is removed, but collider contact, repeated seeded reset equivalence, the 300-frame hold gate, and scripted grasp-and-lift still require their dedicated Isaac runs.
- Learner-update proof: NOT RUN. The camera blocker is removed, but no 500-step SAC update/checkpoint run was executed in this slice.
- Actual policy-improvement proof: NOT RUN. No 20,000-step learning result or successful learned lift is claimed.

The next runtime action is to reduce per-step capture latency while preserving the now-proven RGB/physics recovery contract, then run repeated seeded reset and the 300-frame zero/hold gate. Explicit collider contact and scripted grasp-and-lift follow; do not begin the long learner run until those task gates pass.
