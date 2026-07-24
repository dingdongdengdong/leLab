---
title: "SuperArm plus AmazingHand USD validation in Isaac Sim 6.0"
tags: ["superarm", "isaac-sim", "usd", "amazinghand", "lerobot", "vla"]
created: 2026-07-22T01:19:58.784Z
updated: 2026-07-22T16:47:00.000Z
sources: []
links: ["superarm-real-hardware-motor-protocol-boundary.md", "lelab-controlled-superarm-in-isaac-sim-6-0.md"]
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

Run ID: `20260722T163556Z-motor2-flexion-fix`.

- [Runtime report](assets/superarm-isaac60-passive-linkage-report.json): PASS,
  13 DOFs, one articulation, six-value logical action, arm and hand motion
  PASS, and maximum eight-joint hand error `0.0000622 rad`.
- [Strict validator](assets/superarm-isaac60-passive-linkage-validator.json):
  PASS with zero blocking issues.
- [Whole robot](assets/superarm-isaac60-passive-linkage-whole.png): reviewed
  complete SuperArm with the composed hand attached.
- [Open](assets/superarm-isaac60-passive-linkage-open.png),
  [half close](assets/superarm-isaac60-passive-linkage-half-close.png), and
  [close](assets/superarm-isaac60-passive-linkage-close.png): reviewed direct
  fixed-camera frames from measured Isaac physics snapshots. Adjacent RMS
  differences are `25.1546` and `23.0963`.
- [Open/half/close GIF](assets/superarm-isaac60-passive-linkage-motion.gif) and
  [endpoint sheet](assets/superarm-isaac60-passive-linkage-endpoint-sheet.png)
  hold the actual rendered endpoints long enough for visual review; they do not
  interpolate or invent intermediate robot states.
- Independent-finger closeups:
  [finger 1](assets/superarm-isaac60-passive-linkage-finger1-close.png),
  [finger 2](assets/superarm-isaac60-passive-linkage-finger2-close.png),
  [finger 3](assets/superarm-isaac60-passive-linkage-finger3-close.png), and
  [finger 4](assets/superarm-isaac60-passive-linkage-finger4-close.png).
- [Reviewed contact sheet](assets/superarm-isaac60-passive-linkage-contact-sheet.png)
  contains all seven hand frames plus the whole-robot frame.

Each of the seven reopened snapshot stages contains exactly 88 structural
visual followers, 22 per finger, zero excluded outer shells, and zero
rigid-body/collision/joint/mass schemas on those followers. Source identity,
local translation, and local orientation are checked against the generated
snapshot contract within `1e-6`; quaternion sign equivalence is accepted.

The full ignored runtime artifact is under
`artifacts/isaacsim_superarm/20260722T163556Z-motor2-flexion-fix/`.
Reproduction instructions are in `isaacsim_validation/README.md`.

## Motor-2 flexion-direction correction

The first passive-linkage screenshots were numerically valid in Isaac but did
not visually reach the verified AmazingHand close pose. The generator had sent
Isaac's positive motor-2 curl value directly into the source MuJoCo hand, whose
verified flexion convention is negative for motor 2. The generated follower
poses therefore used extension-biased source states while Isaac's eight motor
joints still reached their positive targets.

The generator now converts only the source-MJCF motor-2 target to the negative
coordinate before solving the visual follower transforms. The public Isaac and
LeRobot conventions remain positive and unchanged. For the first finger's
distal core, open-to-close travel increased from `30.990 deg` and `0.01072 m`
to `64.900 deg` and `0.04201 m`. A fresh nine-step Isaac sweep
`0 -> 27.5 -> 55 -> 82.5 -> 110 -> 82.5 -> 55 -> 27.5 -> 0 deg` passed both
directions, reached the 110-degree endpoint, and returned to open. The fresh
Isaac render/physics report and strict validator both pass; the validator finds
one articulation root, 13 revolute joints, and zero blocking issues.

## Distribution artifact

The current relocatable distribution is:

```text
artifacts/distributions/superarm_amazinghand_isaac60_passive_linkage_no_shell_distribution_20260724_v3.zip
SHA256: c356d1157318b72532b82d73270ef06b5b11ed5b8a90641ea4e431941e4554f7
size: 4,029,301 bytes
entry: usd/superarm_amazinghand/superarm_amazinghand.usda
visual profile: superarm_isaac60_passive_linkage_no_shell/v1
```

It has one archive root and 30 files: the clean relative-reference USD package,
project and AmazingHandControl licenses, README, manifest, checksums, accepted
runtime/validator reports, the reviewed contact sheet, four exact pose PNGs,
and the namespaced passive linkage solver plus live USD update/snapshot helper. The manifest,
`SHA256SUMS`, and archive inventory independently agree on each PNG path, byte
length, and SHA-256. It deliberately excludes the seven roughly 70 MB measured
snapshot stages and other runtime logs/state.

Clean-extraction checks passed: `unzip -t`, every `SHA256SUMS` entry, 457
resolved text USD asset references, and 88 poses from the packaged passive
solver. Strict Isaac Sim 6.0 validation of the extracted entrypoint passed with
one articulation root, 13 revolute joints, and zero blocking issues. The fresh
report is retained at
`artifacts/distribution_validation/superarm_amazinghand_isaac_sim_usd_distribution_20260722/extracted-asset-validator.json`.
The motor-direction-corrected v2 clean-extraction report is at
`artifacts/distribution_validation/superarm_amazinghand_isaac_sim_usd_distribution_20260722_v2/extracted-asset-validator.json`.

The clean entrypoint remains snapshot-free. At bridge startup, the packaged
runtime deactivates the eight simplified frame-first core references, authors
the 88 detailed linkage pieces once into Isaac's session layer, and then updates
only their wrist-local transforms from the eight measured hand joints. These
followers are not baked runtime state or closed-loop PhysX bodies.

Fresh exact-archive managed-control evidence is at
`/home/dong/july/superarm_ws.omx-artifacts/lelab-isaacsim-control/v3-passive-control-smoke-20260724/report.json`.
Isaac Sim 6.0.0 accepted the V3 checksum and visual profile, reported 88
followers with no outer shells, and settled the eight hand motors at the
half-close targets. A separate Isaac Sim 6.0.1 RL RGB smoke now passes after
moving live passive-follower authoring behind successful PhysX articulation
validation and waiting for four valid camera frames after each visual update.
The reviewed open and half-close task frames preserve the attached arm and show
the shell-free hand changing state. Evidence is at
`/home/dong/july/superarm_ws.omx-artifacts/lelab-isaacsim-control/v3-passive-runtime-smoke-verified-20260724/`
and in `omx_wiki/assets/superarm-isaac-rl-v3-*-20260724.*`.

## Proof boundaries

This proves archive provenance, USD composition, one-articulation ownership,
Isaac joint response, clean-package validation, source-identity-preserving
linkage following, and visible grasp-state change. It does not prove hardware
transport, torque/current tuning, closed-loop linkage physics, contact-quality
simulation, grasp success, or a trained ACT/VLA policy. The 88 supplied
structural linkage parts are visual-only followers driven from the eight
measured hand motors. Rounded proximal/distal outer shells remain deliberately
excluded until a later appearance-only validation pass.

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

## LeLab control integration

The accepted distribution is now a pinned LeLab runtime input rather than an
unvalidated ZIP. Commit `a7c63c9` validates safe members, exact manifest/joint
contracts, checksums, and the extraction cache, then expands one six-value
logical action into the named 13-joint Isaac target. Commit `7c994a8` adds the
versioned authenticated localhost JSONL protocol with bounded frames, response
correlation, credential redaction, serialized calls, and no implicit retry of
state changes.

Commit `73409a2` records the architect-approved implementation boundaries. The
subsequent bridge slice keeps all Isaac APIs on the SimulationApp main thread,
discovers the articulation root rather than hard-coding a prim path, maps every
DOF by name, and provides atomic command, observation, hold, on-demand capture,
and shutdown operations. The container launcher mounts the asset and package
read-only, passes a file-backed token, writes session-local metadata/logs, and
removes only its unique owned container. The token and read-write run directory
must be disjoint from both read-only source mounts. A socket is not promoted to
the sole active client until it completes an authenticated `hello`; idle
unauthenticated sockets expire after one second.

Current proof is intentionally limited to host-safe tests, loopback protocol
lifecycle, shell validation, Ruff, and an isolated installed-wheel resource
smoke. Do not call the new LeLab-to-Isaac path live-validated until the host
runtime/API are connected and the final Isaac 6.0 motion plus close-up capture
acceptance run is recorded. MuJoCo remains a separate backend; no ROS 2, real
hardware, or external AmazingHandControl runtime is introduced.

The host session layer now has a runtime-neutral boundary. `isaac_sim` sessions
validate and extract the distribution, authenticate to the long-lived bridge,
require the exact 13 named joints, and accept the same six logical values used
by MuJoCo and future ACT/VLA policies. Website hand-degree commands are mapped
to positive Isaac motor coordinates; MuJoCo retains its negative motor2 runtime
coordinate and its own backend. LeLab exposes explicit telemetry,
logical-action, and on-demand whole/hand capture routes. Continuous video is a
MuJoCo-only capability, not an Isaac claim.

The per-session service watchdog sends one hold after a stale live command even
when no browser, websocket, or telemetry poll occurs, and a blocked old
watchdog prevents disconnect/reconnect rather than being reused against a new
runtime. Commands, emergency stop, capture, and disconnect share one serialized
control boundary, while stale runtime callbacks are generation-filtered.
Managed runtime shutdown asks
the bridge to stop, reaps the owned child, then escalates the child process
group through TERM and KILL only if needed. Host-side tests also prove 20 Hz
observation caching, timeout phase plus bounded suffix-only log diagnostics,
cleanup after signal failures, observed-target cache rebasing, finite direct
inputs, exact unique hello names, validated distribution capability reporting,
explicit shared-root external capture, capture path confinement, and that
importing the LeLab server does not load Isaac/Omniverse modules. This remains
host/fake-bridge proof; the live Isaac motion and visual acceptance gate is
still open. On-demand capture is intentionally non-preemptible on the single
serialized bridge connection, so emergency stop waits for an in-progress
capture request to finish; continuous capture/video is not enabled for Isaac.

`superarm_isaac` is now a registered LeRobot robot type rather than only a
service mode. Its action and observation feature lists remain exactly six wide:
five measured arm values plus one fixed grasp code. The runtime expands an
action to the 13 named Isaac targets, while visualization returns all five arm
and eight positive-coordinate hand positions unchanged. The sixth observation
is last-commanded grasp state, not a claimed measured classifier. The robot
attaches only to an existing Isaac session or starts/owns one itself, rejects an
active MuJoCo session, and disconnects only what it owns. Teleoperation and
recording selection are the next integration slice.

LeLab teleoperation, the manual web leader, recording configuration, manual
recording actions, and robot records now recognize both `superarm_mujoco` and
`superarm_isaac` through one shared backend predicate. Isaac requests bypass
SO-101 follower calibration, carry the server-local distribution path, optional
pinned SHA, bridge ownership mode, host, port, and shared external run
directory, and still expose exactly five arm actions plus one fixed grasp
action to LeRobot datasets and future ACT/VLA policies. The manual hand motions
expand to positive Isaac/URDF joint targets; they do not reuse MuJoCo's negative
motor2 projection.

The existing `SuperArm + AmazingHand` MuJoCo record remains the first primary
built-in. `SuperArm + AmazingHand (Isaac Sim)` is an optional diagnostic record
and is listed only when `SUPERARM_ISAAC_DISTRIBUTION_ZIP` points to a validated
archive. Cleanliness revalidates the recorded SHA, Isaac YAML type, bridge mode,
managed loopback host, and port. Invalid and malformed ZIPs fail closed by
omitting the diagnostic record without breaking `/robots`. MJCF visual manifest
and asset routes remain MuJoCo-only. Independent verification rejected and then
confirmed the malformed-ZIP repair; the final Task 6 suite passed 91 tests plus
Ruff and bytecode compilation. Website selection/capture and live Isaac motion,
screenshots, and GIF evidence remain open gates.

The website now exposes `Isaac Sim 6.0 (USD)` without pretending it is another
continuous stream. MuJoCo and hybrid serial retain `/api/superarm/video`; Isaac
shows connection state, measured physics step, measured joint coverage, and
explicit whole-robot or hand capture controls. Robot records forward the
server-local distribution, optional pinned SHA, managed/external ownership,
host, port, and shared external run directory into manual and recording paths
while keeping the LeRobot action contract five arm values plus one fixed grasp.

Isaac does not fetch the MJCF visual manifest. Instead, its URDF URL requests
the hand-preserving source variant, then applies all five measured arm and eight
measured hand joint positions to that geometry. MuJoCo still requests the
stripped-hand URDF and overlays the exact MJCF hand, preventing duplicate
geometry. This distinction fixed an independently detected arm-only Isaac
showroom regression.

The latest Isaac capture image is served only after matching the capture-time
resolved path, device, inode, byte length, modification time, and SHA-256. The
server reads and validates PNG bytes through a stable regular-file descriptor
and returns those bytes directly, so a same-size replacement is rejected with
409 rather than being reopened through a `FileResponse` race. Connect,
disconnect, and websocket disconnect also clear browser capture metadata,
advance the image URL version, and use `Cache-Control: no-store`, preventing a
session-A image from being presented as session-B evidence. Independent
verification rejected the invisible hand, mutable-file draft, and stale
cross-session UI state in sequence, then approved the repairs. Final leader
verification passed 122 Python tests, Ruff, 28 frontend tests, ESLint, and the
Vite build. This is still website/fake-runtime proof; live Isaac motion, reviewed
close-ups, GIF, numeric acceptance, and real episode evidence remain open.

## Live LeLab-to-Isaac acceptance result

This section supersedes the earlier pending-live-capture statements above. The
API-driven run at
`isaacsim_test/artifacts/lelab_isaac_e2e_20260722T121339Z/` is `PASS` for the
live numeric and lifecycle categories. Its report records Isaac Sim `6.0.0`,
one articulation, exactly 13 expected joint names, and a six-value logical
action. Open, arm-probe plus half-close, and close were accepted only after the
command sequence advanced and all 13 reported targets matched the requested
vector. The controlled ZIP and copied visual report are bound by manifest SHA
`1785dfe1b790ad42f0ce4798637eab13e3325acf86a9f507289c33b76e84d29b`
and validation run `20260722T070208Z-combined-zip-passive-linkage-r3`.
Maximum settled errors were `0.008436 rad` for the arm and
`0.000995 rad` for the hand. Emergency hold remained stable for 211 physics
steps, the ten-second live timeout hold for 137 steps, managed disconnect
finished in `6.249 s`, and reconnect passed.

Live headless capture is disabled. Replicator writer, legacy camera,
experimental RTX camera, viewport, isolated child-Kit, and paused Replicator
probes did not both create a usable frame and return within a bounded deadline
on the long-lived control stage. The runtime now reports
`supports_capture=false`; the UI hides capture controls and uses measured 5+8
joint telemetry to drive the hand-preserving URDF showroom.

The copied whole/open/half-close PNGs and GIF in the run directory are
explicitly `prevalidated_static_isaac_visuals` with
`is_live_session_capture=false`. They are nonblank and visibly distinct, with
adjacent mean absolute differences `3.0509` and `3.5683`, but they are not
frames from the live LeLab session. The live report proves control convergence,
holds, cleanup, and reconnect; it does not prove a real recording episode,
trained policy, contact/grasp retention, passive followers as live PhysX
bodies, ROS 2, or physical motor protocols. See
[[lelab-controlled-superarm-in-isaac-sim-6-0]] for commands and troubleshooting.
