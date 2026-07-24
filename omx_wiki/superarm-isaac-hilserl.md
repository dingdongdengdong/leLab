# SuperArm Isaac HIL-SERL

## Implemented boundary

The V1 reinforcement-learning page is simulation-only and runs LeRobot's upstream HIL-SERL SAC actor/learner through a LeLab-owned Gymnasium environment registered as `gym_hil/SuperArmIsaacPickLift-v0`. It never edits LeRobot site-packages and always reports `is_intervention=false`.

The action contract is five normalized arm deltas (0.04 rad per control step, clamped to the existing joint limits) plus one categorical AmazingHand command (open, half-close, close). The observation is a fixed 23-value state vector and a 256 by 256 workspace RGB frame. Isaac advances twelve 120 Hz physics substeps per 10 Hz action.

The immutable V3 shell-free passive-linkage ZIP is the only accepted source distribution. The runtime overlay authors the 88 visual-only linkage followers, table, cube, camera, and palm/finger collision proxies in memory without saving the source USD. The expected archive SHA-256 is `c356d1157318b72532b82d73270ef06b5b11ed5b8a90641ea4e431941e4554f7`; every other archive is rejected.

## Lifecycle

`GET /system/rl-readiness` checks the driver, Docker, the Isaac Sim 6.0.1 image, the RL X11 display, exact distribution checksum, actor/learner dependencies, ports, and the local-job lock. The server resolves the single archive from `SUPERARM_ISAAC_DISTRIBUTION_ZIP` and supplies that path to the read-only UI, avoiding host-specific frontend defaults. `POST /jobs/reinforcement-learning` launches one detached supervisor. It starts managed Isaac, then the learner, waits for the learner gRPC port, and only then starts the actor. Stop or failure terminates actor, learner, bridge, and the owned container. `/jobs/{id}/frame` serves only the bounded per-job PPM frame path.

The RGB path is deliberately isolated from the existing 6.0.0 WebRTC/teleoperation path. RL uses Isaac Sim 6.0.1 as the image's non-root UID, Xvfb on `:100`, persistent Omni/Kit/RTX caches, asynchronous throttling disabled, and Isaac's `Camera` wrapper. The bridge first validates the 13-DOF PhysX articulation, then authors the 88 visual-only followers. Authoring them before PhysX initialization fragmented the arm in the task frame and is now a regression-tested forbidden ordering. Each capture waits for four valid rendered frames so a fresh passive-linkage transform cannot return a stale RGB frame, then restores the articulation and cube state before accepting the next atomic action.

Existing `/training` remains imitation learning. The new routes are `/reinforcement-learning` and `/reinforcement-learning/:jobId`.

## Verification status (2026-07-24 UTC)

- Contract and regression proof: PASS. Action mapping, state ordering, rewards, deterministic adapter reset, protocol validation, safe frame paths, upstream LeRobot config decoding, post-PhysX passive authoring, four-valid-frame capture, and the complete 486-test Python suite passed. The frontend also passed all 32 tests, lint with zero errors, and a production build.
- Exact V3 managed-control proof: PASS. Isaac Sim 6.0.0 loaded archive SHA `c356d1157318b72532b82d73270ef06b5b11ed5b8a90641ea4e431941e4554f7`, authenticated with the expected shell-free visual profile, reported 88 passive followers and zero outer shells, and settled one half-close command across all eight hand motors. See `/home/dong/july/superarm_ws.omx-artifacts/lelab-isaacsim-control/v3-passive-control-smoke-20260724/report.json`.
- Exact V3 RL RGB proof: PASS for one seeded reset and one half-close step. A real non-root Isaac Sim 6.0.1 container loaded the locked archive, reported one 13-DOF articulation, 88 passive followers, zero outer shells, and `visual_profile=superarm_isaac60_passive_linkage_no_shell/v1`. The reviewed [open reset frame](assets/superarm-isaac-rl-v3-open-20260724.png) shows the attached full arm and shell-free hand; the reviewed [half-close frame](assets/superarm-isaac-rl-v3-half-close-20260724.png) visibly closes the linkage while retaining the arm, table, and cube. Both are 256 by 256 RGB, state width is 23, and `is_intervention=false`. See the [runtime report](assets/superarm-isaac-rl-v3-smoke-report-20260724.json) and `/home/dong/july/superarm_ws.omx-artifacts/lelab-isaacsim-control/v3-passive-runtime-smoke-verified-20260724/`.
- Repeated reset/300-frame hold proof: PASS. Two consecutive `rl_reset(seed=42)` results matched with maximum absolute state error `0.0`. All 300 hold steps returned a strictly increasing fresh frame sequence, completed with `is_intervention=false`, held the five arm joints within `0.001191 rad`, and limited cube drift to `0.0000181 m`. Warmed median step latency was `0.2197 s`, p95 was `0.2803 s`, and maximum was `0.3500 s`. The reviewed [step-300 frame](assets/superarm-isaac-rl-v3-hold-step-300-20260724.png) still shows the attached full arm, shell-free open hand, table, and cube. See the [gate report](assets/superarm-isaac-rl-v3-hold-300-report-20260724.json).
- Contact/grasp-and-lift proof: NOT RUN. Collider contact and scripted grasp-and-lift still require their dedicated Isaac runs.
- Learner-update proof: NOT RUN. The V3 RGB blocker is cleared, but no 500-step SAC update/checkpoint run is yet claimed.
- Actual policy-improvement proof: NOT RUN. No 20,000-step learning result or successful learned lift is claimed.

Real hardware remains capped at grasp code `0.5` (half-close). Grasp code `1.0` is simulation-only.

The next runtime action is explicit collider contact plus scripted grasp-and-lift. Do not begin the long learner run until those task gates pass.
