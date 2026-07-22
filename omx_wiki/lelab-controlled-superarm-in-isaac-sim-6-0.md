---
title: "LeLab-controlled SuperArm in Isaac Sim 6.0"
tags: ["lelab", "isaac-sim", "superarm", "lerobot", "vla", "evidence"]
created: 2026-07-22T11:38:17.732Z
updated: 2026-07-22T15:00:00.000Z
sources: []
links: ["lelab-isaac-sim-control-scope.md", "superarm-urdf-validation-in-isaac-sim-6-0.md"]
category: architecture
confidence: high
schemaVersion: 1
---

# LeLab-controlled SuperArm in Isaac Sim 6.0

## Decision and scope

LeLab is the control owner for the SuperArm + AmazingHand Isaac Sim backend. This branch does not import AmazingHandControl, ROS 2, DM4340P CAN, or a real-hardware transport. LeLab, manual/SO-101 teleoperation, recording, and future ACT/VLA policies expose exactly six logical values: `joint_rev_1` through `joint_rev_5`, then one fixed AmazingHand grasp value. Isaac alone expands that action to 13 named physical joints.

The accepted asset is:

```text
artifacts/distributions/superarm_amazinghand_isaac_sim_usd_distribution_20260722.zip
SHA256 9386f054e6d75ee1abfeac0b7a6e7304e7c163440bcd092c38df0610f9314ba2
entry usd/superarm_amazinghand/superarm_amazinghand.usda
```

## Architecture

```text
browser / manual leader / SO-101 / LeRobot policy
                    |
                    | five arm radians + one grasp code
                    v
          LeLab SuperArmService
                    |
                    | authenticated JSONL on 127.0.0.1
                    v
       managed Isaac Sim 6.0 container
                    |
                    | exact named 13-joint targets
                    v
       SuperArm + AmazingHand articulation
```

The host FastAPI process never imports `isaacsim`, `omni`, or `pxr`. The managed runtime validates and extracts the ZIP, creates a mode-0600 token file, launches `isaacsim_validation/run_isaacsim60_control_bridge.sh`, waits for an authenticated hello, and owns only its unique child process/container. External mode attaches to a caller-owned loopback bridge and does not terminate it.

The versioned protocol supports `hello`, `command`, `observe`, `hold`, `capture`, and `shutdown`. `command` contains exactly the five arm plus eight hand target names. The `capture` operation remains protocol-compatible but the current Isaac runtime advertises `supports_capture=false` and rejects it immediately; see the proof boundary below.

## Six-to-13 mapping

The logical order is:

```text
[joint_rev_1, joint_rev_2, joint_rev_3, joint_rev_4, joint_rev_5, amazinghand_motion]
```

The fixed grasp code expands to four motor pairs:

| grasp | motor1 per finger | motor2 per finger |
| --- | ---: | ---: |
| open `0.0` | `0.05 rad` | `0.02 rad` |
| half `0.5` | `0.50 rad` | `0.56 rad` |
| close `1.0` | `0.95 rad` | `1.10 rad` |

The physical names are `joint_rev_1` through `joint_rev_5` and `finger1_motor1`, `finger1_motor2` through `finger4_motor1`, `finger4_motor2`. Array order is never trusted. A 13-wide policy action is rejected; 13 joints are an internal backend contract, not the LeRobot action shape.

## Launch and website workflow

```bash
export SUPERARM_ISAAC_DISTRIBUTION_ZIP="$PWD/artifacts/distributions/superarm_amazinghand_isaac_sim_usd_distribution_20260722.zip"
export ISAAC_SIM_STARTUP_TIMEOUT_S=240
uv run lelab --no-open
```

Open `/superarm`, select `Isaac Sim 6.0 (USD)`, keep managed mode and loopback defaults, then connect. Arm sliders, fixed open/half-close/close motions, poses, sequences, emergency stop, and live-command timeout all use the same SuperArm service boundary. The measured five arm plus eight hand positions drive the hand-preserving URDF showroom. MuJoCo remains a separate backend and keeps continuous MJCF video.

For LeRobot, select `superarm_isaac`. Manual web input and an SO-101 leader both emit the same six-value action, and recording keeps six action/observation features for future ACT/VLA training. The sixth observation is the last commanded fixed grasp code, not a claimed measured grasp classifier.

## Standalone copied LeLab page and WebRTC boundary

`/isaac-sim` does not define a second control dashboard. It renders the existing
`SuperArm` page with its runtime fixed to `isaac_sim`, so poses, sequences,
emergency stop, arm sliders, AmazingHand controls, telemetry, and the URDF
showroom continue to use the same LeLab component and API calls. Only the
right-hand visualization panel changes to NVIDIA's
`@nvidia/omniverse-webrtc-streaming-library` direct client.

The browser resolves the signaling/media host from `window.location.hostname`.
Therefore a client opening the LeLab site through a private host address dials
that Isaac host rather than its own `127.0.0.1`. Isaac uses TCP `49100` for
signaling and UDP `47998` for media. The managed runtime launches Isaac Sim
6.0's `isaacsim.exp.full.streaming.kit` experience at `1280x720` and continues
to keep its authenticated control protocol loopback-only.

Fresh browser evidence proved the WebRTC transport itself: the video element
reached `readyState=4`, played without pause, advanced to `18.44792` seconds,
and reported `1280x720`. The runtime simultaneously reported one 13-DOF
articulation, logical width six, the same controlled and viewport USD stage,
finite robot bounds, and the validated camera position. However, the reviewed
frame at
`omx_wiki/assets/lelab-isaac-webrtc-viewport-black-evidence.png`
still shows a black Isaac viewport inside the otherwise live Isaac UI. This is
not visual robot proof. The LeLab URDF showroom remains visible and controlled;
the WebRTC transport is integrated, but Isaac 6.0 viewport rendering in this
embedded `SimulationApp` launch remains an explicit unresolved runtime issue.

## Acceptance command and evidence

```bash
uv run python -m isaacsim_validation.run_lelab_isaac_e2e \
  --base-url http://127.0.0.1:8000 \
  --distribution-zip "$SUPERARM_ISAAC_DISTRIBUTION_ZIP" \
  --run-dir isaacsim_test/artifacts/lelab_isaac_e2e_20260722T123815Z \
  --http-timeout-s 400
```

Live report:

```text
isaacsim_test/artifacts/lelab_isaac_e2e_20260722T123815Z/lelab-isaac-e2e-report.json
status PASS
report SHA256 717fa8a968946f8559808c8dce6d2b6ddf8c2abc70535ce6a2b65f4b17fa311f
Isaac Sim 6.0.0
one articulation, 13 exact joint names, logical width 6
distribution validation run 20260722T070208Z-combined-zip-passive-linkage-r3
embedded validation report SHA256 1785dfe1b790ad42f0ce4798637eab13e3325acf86a9f507289c33b76e84d29b
maximum settled arm error 0.012761 rad
maximum settled hand error 0.004891 rad
emergency hold 210 stable physics steps
live-timeout hold 134 stable physics steps
managed disconnect 1.490 s
reconnect PASS
```

The runner requires a strictly newer command sequence and the exact requested 13-target map before it accepts a settled sample. This prevents cached telemetry from passing a new command.

Static visual evidence copied into that directory includes the whole robot, open, half-close, close, and `lelab-isaac-open-half-close.gif`. The report labels it `proof_category=prevalidated_static_isaac_visuals` and `is_live_session_capture=false`. The source report SHA, validation run ID, and all four PNG paths, byte lengths, and SHA-256 values must match the controlled ZIP manifest, `SHA256SUMS`, and archive bytes. The three final passive-linkage frames are nonblank; adjacent mean absolute differences are `3.0509` and `3.5683`.

Robot/camera setup is transactional at both the LeRobot robot boundary and the
recording-device boundary. A failed camera or teleoperator connection releases
every attempted camera/device and disconnects only an Isaac session owned by
that setup attempt; a borrowed website session remains connected. Cleanup
errors never replace the original setup exception. Final robot disconnect also
attempts owned-session teardown even when an attached camera raises during its
own cleanup, then reports the first cleanup error.

Closed-hand passive-linkage physics can occasionally make an Isaac step take hundreds of milliseconds. The localhost bridge therefore uses a bounded five-second response deadline, while the hold verifier allows up to 30 seconds to accumulate 120 actual physics steps. It never substitutes wall time for physics progress and never retries a state-changing request.

## Capture and proof boundary

Live headless capture is disabled. Browser WebRTC streaming is now a separate
transport capability and must not be confused with the capture API or with a
successful robot-visible frame. Replicator writer, legacy camera, experimental
RTX camera, viewport capture, isolated child-Kit rendering, and paused
Replicator experiments did not both return a usable frame and terminate within
their deadlines on the long-lived control stage. Therefore the website does
not offer an Isaac capture button and does not call static evidence live. The
URDF showroom is driven by live measured joint telemetry; the PNGs/GIF are
separately validated USD pose evidence.

The live report proves named six-to-13 control, measured convergence, emergency/live-timeout hold, managed cleanup, and reconnect. It does not prove a real recorded episode, a trained ACT/VLA policy, contact/grasp retention, 88 passive followers as PhysX bodies, ROS 2, AmazingHand serial control, DM4340P CAN, or real hardware.

## Cleanup and troubleshooting

- A managed disconnect sends shutdown, reaps the child, then escalates only its owned process group if required.
- The wrapper removes only its unique `superarm-isaac-control-*` container.
- A connect failure should be diagnosed from the phase file and bounded container-log suffix under `~/.cache/lelab/superarm_isaac/sessions/<id>/run`.
- A hello mismatch is fatal unless the runtime reports Isaac `6.0.x`, one articulation, logical width six, physical width 13, and the exact named joint set.
- If a control case appears to settle instantly, inspect `command_sequence` and `reported_targets`; a cached observation must not satisfy the gate.
- Do not expose the bridge port over Tailscale. Only the website/API port is intended for a client.

## Commit history

```text
a7c63c9 distribution and six-to-13 target validation
7c994a8 versioned localhost protocol
2f7f0d7 long-lived managed articulation bridge
72179ae runtime/service/API session
b216503 six-control LeRobot backend
fb27e3a teleoperation and recording integration
2784bac website Isaac controls and URDF telemetry
9d006e3 live numeric/lifecycle acceptance and truthful capture boundary
152fcaa durable LeLab-controlled Isaac architecture record
1c47eb9 distribution-bound visual bytes and transactional setup rollback
b4e7ef4 failure-isolated owned-session teardown
```

See [[lelab-isaac-sim-control-scope]] and [[superarm-urdf-validation-in-isaac-sim-6-0]].
