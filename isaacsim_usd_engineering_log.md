# SuperArm Isaac Sim USD engineering log

This is the cumulative, evidence-based record for the SuperArm and
AmazingHand Isaac Sim USD work. Update it immediately after each problem is
repaired and freshly verified. It is intentionally separate from MuJoCo: only
Isaac Sim USD assets, runtime results, validators, and reviewed Isaac renders
count as evidence here.

## Current asset contract

The authoritative hand source is the supplied Isaac Sim distribution:

```text
/home/dong/july/superarm_ws/isaacsim_test/artifacts/distributions/
  amazinghand_isaac_sim_usd_distribution_20260722.zip
SHA256: 3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377
entry: usd/amazinghand_graspable/amazinghand_graspable.usda
```

The archive defines an eight-joint AmazingHand articulation. The project may
map a single LeRobot grasp command to a fixed eight-joint pose, but it must not
misrepresent that logical action as one physical hand joint.

Proof categories stay separate:

- **Source integrity:** exact ZIP checksum and safe extraction.
- **USD structure:** prims, joints, rigid bodies, colliders, and articulation
  ownership.
- **Numeric runtime:** commanded and measured joint positions in Isaac Sim.
- **Visual runtime:** close-up, non-blank, reviewed Isaac Sim images.
- **Combined robot:** the hand is attached to the SuperArm wrist under one
  full-robot articulation.

A pass in one category does not imply a pass in another.

## Cumulative problem log

### 1. Hand validation could silently use a reconstructed asset instead of the supplied USD

- **Symptom:** earlier hand experiments could validate URDF/MJCF-derived
  geometry without proving that the supplied Isaac Sim package was used.
- **Cause:** there was no immutable source checksum, archive-entry contract,
  or extraction test for the supplied distribution.
- **Repair:** `isaacsim_validation/prepare_amazinghand_usd.py` now treats the
  supplied ZIP as authoritative, verifies its SHA256 before extraction,
  rejects unsafe archive paths, and records the eight USD revolute joints.
- **Regression coverage:**
  `tests/test_amazinghand_usd_distribution.py` verifies the accepted archive,
  wrong-checksum rejection, and path-traversal rejection.
- **Verification:** five targeted tests pass. Commit `1263293` contains the
  source-contract implementation.
- **Reusable rule:** simulation asset provenance must be machine-verifiable;
  a visually similar reconstruction is not proof that the requested USD was
  used.

### 2. The detailed static visual shell was incorrectly part of the rigid-body model

- **Symptom:** strict Isaac Sim asset validation reported a rigid body without
  a collider on `amazinghand_visual_shell`; the validator folder also included
  a preview stage that violated clean-package expectations.
- **Cause:** the source distribution mixed a detailed static presentation
  shell with the simplified articulated physics tree and registered that shell
  as a physical robot link.
- **Repair:** preparation removes the shell's invalid rigid-body/fixed-joint
  binding and its RobotAPI link/joint registrations while preserving the shell
  as visual data. The preview entry is excluded from the validator package.
- **Regression coverage:** the repair test confirms that the static shell is
  no longer a rigid body and that all eight physical hand joints remain.
- **Verification:** strict Isaac Sim 6.0 validation passes with zero blocking
  issues in
  `artifacts/isaacsim_superarm/20260722T043000Z-zip-usd-repaired/asset-validator.json`.
  Commit `4d6ed60` contains the repair.
- **Reusable rule:** a detailed showroom shell and an articulated physics tree
  may coexist, but decorative static geometry must not be declared as an
  uncollided rigid body.

## Entry format for every later fix

Append a numbered item and preserve earlier entries. Each item must include:

1. observed symptom and artifact path;
2. root cause;
3. smallest repair;
4. regression test or runtime check;
5. exact verification result and commit;
6. remaining boundary, if any;
7. reusable rule.

Do not write `PASS` until the named evidence exists and has been inspected.

### 3. The supplied hand drives produced too little visible travel

- **Observed evidence:** the first hand-only runtime could numerically change
  joint values, but the direct frames remained nearly identical and did not
  demonstrate useful open/close travel.
- **Cause:** the supplied angular drives had damping but no authored stiffness
  suitable for this position-control validation.
- **Repair:** preparation authors stiffness `3.1415927` on exactly the eight
  expected hand angular drives while retaining the supplied joint limits and
  damping.
- **Regression coverage:** the distribution test requires eight drive
  stiffness opinions and rejects a changed joint contract.
- **Verification:** the final combined run at
  `artifacts/isaacsim_superarm/20260722T045604Z-combined-zip-usd-clean/`
  measures monotonic open, half-close, and close motion with maximum joint
  errors below `0.000063 rad`. Commit `c94e980` contains the repair.
- **Remaining boundary:** this is an Isaac position-drive setting, not a claim
  about real AmazingHand current, torque, or serial-controller gains.
- **Reusable rule:** numeric movement alone is insufficient; the drive
  configuration must also produce an inspectable visual range without changing
  the hardware-facing action contract.

### 4. A hand-only USD could not be nested safely into the combined robot

- **Observed evidence:** the supplied archive already contained an eight-joint
  hand articulation, while the SuperArm URDF already contained those eight hand
  joints beneath its five arm joints. Referencing both would create competing
  physics ownership.
- **Cause:** the source hand USD was packaged as a standalone robot, not as a
  visual-only payload for an existing combined articulation.
- **Repair:** `zip_hand_binding.py` copies only the checked visual payloads and
  binds 26 wrist/palm parts plus 16 proximal/distal moving parts below the
  existing combined links. No hand articulation, rigid body, joint, or collider
  is imported from the archive.
- **Regression coverage:** `tests/test_zip_hand_binding.py` locks the visual
  payload allowlist, 26/16 part counts, and distal reference contract.
- **Verification:** `isaac-report.json` reports 13 physical DOFs and one
  articulation root; `asset-validator.json` independently reports 13 revolute
  joints and one articulation root. Commit `c94e980` contains the binding.
- **Remaining boundary:** detailed closed-loop linkage and backplate pieces are
  static visual geometry; the moving proximal/distal shells follow the eight
  simplified physical links.
- **Reusable rule:** when combining robot assets, choose one physics owner and
  import appearance separately rather than nesting duplicate articulations.

### 5. Whole-frame or cropped evidence could hide an incorrect hand

- **Observed evidence:** earlier views either showed only a small hand area or
  used derived crops, making it hard to judge whether all fingers moved and
  remained attached.
- **Cause:** the evidence pipeline did not require fixed-camera direct renders
  for all requested grasp states.
- **Repair:** the renderer opens each measured physics snapshot and captures
  direct `open`, `half_close`, and `close` frames with one fixed close-up camera,
  plus a separate whole-robot frame.
- **Regression coverage:** visual tests reject blank frames, derived crop
  evidence, static three-frame sequences, and inconsistent camera setup.
- **Verification:** reviewed files `hand_open.png`, `hand_half_close.png`,
  `hand_close.png`, and `whole_robot.png` in the final run are nonblank and show
  the attached hand; adjacent RMS changes are `26.3583` and `20.8902`. Commit
  `c94e980` contains the capture contract.
- **Remaining boundary:** images prove visible kinematic change, not contact
  force or grasp success.
- **Reusable rule:** visual robot claims require direct, close, fixed-camera,
  state-labeled frames in addition to numeric joint readback.

### 6. The whole-robot camera cropped the newly composed hand

- **Observed evidence:** an otherwise successful combined render omitted part
  of the hand in `20260722T042902Z-combined-zip-usd/whole_robot.png`.
- **Cause:** the URDF importer authored an extents hint before the ZIP visual
  overlay was composed, so the stale cached bound did not include later hand
  references.
- **Repair:** close-ups may use the hand-link hint, but the whole-robot camera
  now recomputes the composed render-purpose bound with
  `useExtentsHint=False`.
- **Regression coverage:** the camera-bound unit test requires render-purpose
  payloads and the profile-specific extents-hint behavior.
- **Verification:** the reviewed final `whole_robot.png` contains the complete
  arm and hand. Commit `c94e980` contains the repair.
- **Remaining boundary:** future post-import overlays also need live bounds or
  a deliberately regenerated root extents hint.
- **Reusable rule:** an extents hint is only authoritative for the composition
  state in which it was authored.

### 7. Distal placement opinions violated the clean robot-package rules

- **Observed evidence:** strict validation of the intermediate combined asset
  reported `NoOverrides` for locally authored distal visual offsets.
- **Cause:** the binding layer added transforms on prims whose referenced
  source already defined their content, producing validator-hostile override
  opinions in the robot root layer.
- **Repair:** the `-0.058 m` distal offset now lives inside the generated
  referenced `zip_hand_payloads/distal_visuals.usda`; the robot layer authors
  only clean reference arcs.
- **Regression coverage:** the binding test requires the generated distal
  payload and forbids the previous local translate-op implementation.
- **Verification:** the final strict validator reports zero blocking rules.
  Commit `c94e980` contains the repair.
- **Remaining boundary:** generated visual payloads must stay beside the robot
  package because the root layer uses relative references.
- **Reusable rule:** package-local placement belongs in a referenced asset
  layer when root-layer overrides violate SimReady composition rules.

### 8. Runtime world state contaminated the reusable robot USDA

- **Observed evidence:** one attempted final package contained `/physicsScene`,
  live body transforms, and velocity opinions; strict validation then failed
  `NoOverrides` and robot physics source-layer rules.
- **Cause:** Isaac runtime state was saved into the robot's root edit target,
  and the supposed pristine byte snapshot was taken only after `World()` had
  already created `/physicsScene`.
- **Repair:** the runner saves the clean robot root bytes after visual binding
  but before constructing `World`, exports measured states only to separate
  snapshot USDAs, and restores the clean root bytes after simulation.
- **Regression coverage:** the runtime-order test requires the pristine snapshot
  to precede `World()` and requires restoration after snapshot export.
- **Verification:** the final root USDA contains neither `/physicsScene` nor
  runtime velocity opinions. Isaac Sim 6.0 strict validation passes with zero
  blocking issues in
  `20260722T045604Z-combined-zip-usd-clean/zip_learning_isaac/asset-validator.json`.
  Commit `c94e980` contains the repair.
- **Remaining boundary:** the measured transforms intentionally remain in
  `hand_*_snapshot.usda`; those files are evidence stages, not publishable robot
  packages.
- **Reusable rule:** keep reusable robot assets and runtime scene state in
  separate layers, and capture the clean boundary before creating the world.

### 9. Rounded outer shells obscured the real moving-frame attachment

- **Observed evidence:** the earlier combined hand visibly opened and closed,
  but its rounded proximal/distal shells covered the smaller structural cores,
  making it difficult to judge whether the visual links were attached to the
  intended Isaac pivots.
- **Cause:** the visual binding selected both `proximal_shell`/`distal_shell`
  and their underlying proximal/distal core parts for every finger.
- **Repair:** `frame_first_no_outer_shells` keeps the 26 supplied
  wrist/palm/servo-frame parts, disables the full static presentation shell,
  excludes all eight rounded outer-shell instances, and binds one supplied
  proximal core plus one supplied distal core to each two-link finger.
- **Historical reference:** commit
  `0e53b0dfadaae3234d14fb5830108ae931734d0c` defines the same Isaac-friendly
  four-finger, two-link open-chain topology, including the `0.058 m` distal
  offset and current joint origins/axes. Its fixed 162-part visual shell and
  prop-oriented SimReady CAD output are not used as articulated-runtime proof.
- **Regression coverage:** 28 targeted tests pass and require the frame-first
  mode, eight moving core visuals, eight excluded outer-shell instances, no
  moving `_shell` references, the historical reference hash, and the existing
  clean distal payload composition.
- **Verification:** commit `e4100fc` contains the repair. The reviewed direct
  Isaac frames in
  `artifacts/isaacsim_superarm/20260722T051559Z-combined-zip-frame-first/`
  show attached proximal/distal cores moving through open, half-close, and
  close states. Adjacent RMS differences are `22.0600` and `19.6275`. Runtime
  reports 13 physical DOFs and a six-value logical action; strict validation
  passes with zero blocking issues.
- **Remaining boundary:** this is a truthful open-chain frame visualization,
  not a reconstruction of the original AmazingHand closed-loop passive
  linkage, contact quality, or lift-retain success.
- **Reusable rule:** validate the moving kinematic frame without cosmetic
  shells first; add appearance only after each visual part's pivot ownership is
  proven.

### 10. Frame-first cores omitted the supplied passive linkage structure

- **Observed evidence:** entry 9 proved the eight open-chain moving frames, but
  the closeups still showed only one proximal and one distal core per finger;
  rods, pins, and coupled structural members from the supplied AmazingHand USD
  distribution were absent.
- **Cause:** the frame-first binding intentionally reduced each finger to two
  source core meshes and therefore could not reproduce the detailed source
  linkage appearance.
- **Repair:** a checked offline generator solves open, half-close, and close
  poses from the original AmazingHand source model, locks the authoritative ZIP
  and source hashes, excludes rounded shells and decorative fasteners, and
  emits 88 structural visual followers: 22 per finger. Isaac runtime performs
  no MuJoCo physics; it interpolates the checked keyframes from the eight
  measured Isaac motor joints and authors visual-only followers into evidence
  snapshots.
- **Regression coverage:** the manifest, pure-Python solver, USD authoring, and
  visual-proof tests require exact provenance, exact 88/22 counts, per-finger
  motor isolation, finite normalized transforms, no outer shells, no follower
  physics schemas, independent-finger reset/readback, and reopened-stage
  source/transform agreement.
- **Verification:** run
  `20260722T070208Z-combined-zip-passive-linkage-r3` reports 13 physical DOFs,
  one articulation root, a six-value logical action, arm and hand motion PASS,
  all four independent-finger checks PASS, and seven snapshot stages with 88
  validated followers each. Reviewed open/half/close RMS differences are
  `14.7731` and `16.5492`; the strict validator reports zero blocking issues.
  Implementation spans commits `88a03b3` through `aaf639c`.
- **Remaining boundary:** the supplied structural members are visual followers,
  not closed-loop PhysX constraints; contact, grasp retention, shell clearance,
  and hardware behavior are not proven.
- **Reusable rule:** preserve one authoritative physics articulation and add
  source-faithful passive mechanisms as separately validated followers unless
  the simulator is intentionally being given a closed-loop physics model.

### 11. USD provenance text was mistaken for an external asset dependency

- **Observed evidence:** the first live passive-linkage run stopped after the
  open snapshot with `external source asset path leak in flattened snapshot:
  /tmp/`, although the snapshot contained no `@...@` asset tokens.
- **Cause:** the leak check rejected every `/tmp/` substring, including the USD
  layer `doc` field generated by Isaac's URDF importer to record temporary
  composition provenance.
- **Repair:** commit `3af7c5d` removes only the blanket text rejection. Actual
  asset references and the authoritative source-package paths remain denied.
- **Regression coverage:** the leak test now accepts non-asset `/tmp` provenance
  while continuing to reject `@/tmp/leaked.usd@` and absolute source paths.
- **Verification:** all seven final snapshots flatten without external asset
  references and reopen for source/transform validation.
- **Remaining boundary:** the flattened evidence snapshots are large diagnostic
  files, not the reusable robot package.
- **Reusable rule:** validate dependency syntax as dependency syntax; do not
  classify human-readable provenance metadata as a composition arc.

### 12. A zero Isaac process exit hid a numeric report failure

- **Observed evidence:** the failed first run launched the renderer and replaced
  the useful numeric error with `numeric snapshot report is not ready: FAIL`.
  A direct reproduction showed container exit status `0` while
  `isaac-report.json` was `FAIL`.
- **Cause:** the wrapper trusted only the container process status before
  launching the renderer.
- **Repair:** commit `00c9b7f` reads the numeric report immediately after the
  numeric container, requires `NUMERIC_PASS` or `PASS`, preserves `numeric.log`
  as `isaac.log`, and exits before renderer startup on any report failure.
- **Regression coverage:** a wrapper-order test requires the report gate to
  occur before the render container command.
- **Verification:** the accepted run advanced to rendering only after its
  numeric report recorded `NUMERIC_PASS` with three grasp and four independent
  measured snapshots.
- **Remaining boundary:** Isaac/Kit warnings still appear in logs and must not be
  interpreted as pass/fail without the structured report.
- **Reusable rule:** for simulator automation, treat the structured report as
  the acceptance contract even when the hosting process returns zero.

### 13. Whole-robot rendering retained a stale USD stage after context switches

- **Observed evidence:** two renderer runs produced all seven hand PNGs but
  failed before `whole_robot.png` with a `Stage.GetPrimAtPath` binding error.
- **Cause:** the renderer saved `last_stage` during the three grasp snapshots,
  then switched the global `omni.usd` context through four independent-finger
  stages without updating that saved handle. The final root lookup therefore
  used a stale stage wrapper rather than the active finger-4 stage.
- **Repair:** commits `0e2cc58` and `6c6430b` use an explicit `Sdf.Path` and
  retain the active independent-finger stage for the final whole-robot capture.
- **Regression coverage:** source-order tests require the typed root lookup and
  require `last_stage = stage` inside the independent snapshot loop before the
  final lookup.
- **Verification:** the renderer retry against the unchanged measured r3
  snapshots completed with report `PASS` and produced the reviewed
  `whole_robot.png` plus all seven nonblank hand frames.
- **Remaining boundary:** first-frame RTX compilation takes roughly five
  minutes on this host; that is a performance cost, not a correctness failure.
- **Reusable rule:** objects returned by a mutable global USD context are valid
  only while that context owns their stage; refresh retained handles after
  every stage switch.

### 14. Cleanup and metadata fallbacks could weaken the acceptance contract

- **Observed evidence:** the final changed-file deslop review found that a
  reusable-package restore failure was recorded but not forced to fail the run,
  a visual ordering test targeted an unused helper, and mandatory passive-part
  metadata authoring could silently return when the USD API was unavailable.
- **Cause:** compatibility-oriented exception handling and test fakes had
  introduced fallback paths broader than the production contract.
- **Repair:** commit `d78a225` makes pristine-package restore failure set report
  `FAIL` and produce a non-success outcome without overwriting an earlier root
  exception. Commit `43082b8` deletes the dead validator helper and makes the
  regression test target the active snapshot-contract call. Commit `4613855`
  uses explicit `Sdf.ValueTypeNames.Int/String` and requires both attribute
  creation and value assignment to succeed.
- **Regression coverage:** restore-failure tests lock report status and original
  error preservation; renderer-order tests inspect the live contract-validation
  path; USD fake prims now implement the required attribute API instead of
  relying on a production bypass.
- **Verification:** the post-cleanup full suite passes 334 tests; changed-file
  Ruff, format, `py_compile`, shell syntax, and diff checks pass.
- **Remaining boundary:** cleanup failure paths are unit-tested because forcing
  a real published-package write failure would be destructive to the validated
  run directory.
- **Reusable rule:** evidence metadata and package restoration are acceptance
  requirements, not optional compatibility features; fail explicitly when
  either cannot be authored.

### 15. A validated working directory was not yet a relocatable distribution

- **Observed evidence:** the accepted reusable USDA loaded from its original
  run directory, but there was no single archive contract proving that all USD
  dependencies, licenses, control metadata, and checked passive-linkage support
  would survive extraction elsewhere.
- **Cause:** the runtime pipeline produced validation evidence and a clean robot
  package, not a bounded release artifact. Copying only the root USDA would
  omit its payload layers; copying the complete run would add roughly 490 MB of
  measured-state snapshot stages and runtime world state.
- **Repair:** commit `721cd60` adds a deterministic exporter that accepts only
  the approved runtime/validator contracts, rejects external or unresolved text
  USD references, and packages the clean asset, licenses, manifest,
  `SHA256SUMS`, reviewed contact sheet, validation reports, and the checked
  88-part passive follower helper under one archive root. Large snapshot stages
  remain excluded.
- **Regression coverage:** three exporter tests prove byte-for-byte repeatable
  ZIP output, safe single-root members, complete checksum coverage, the
  13-DOF/6-action contract, passive-visual boundaries, external-reference
  rejection, and refusal to publish failed evidence.
- **Verification:** archive
  `artifacts/distributions/superarm_amazinghand_isaac_sim_usd_distribution_20260722.zip`
  is 2,987,778 bytes with SHA256
  `a26ba228eee76f815291adef029c7ed510020cd20bdfae9046c6319d7d99c195`.
  `unzip -t` and every packaged `SHA256SUMS` entry pass. A clean extraction
  resolves 457 text USD asset references, the packaged solver returns 88
  follower poses, and Isaac Sim 6.0 strict validation of the extracted
  entrypoint reports one articulation root, 13 revolute joints, and zero
  blocking issues in
  `artifacts/distribution_validation/superarm_amazinghand_isaac_sim_usd_distribution_20260722/extracted-asset-validator.json`.
- **Remaining boundary:** the clean entrypoint contains the moving frame-first
  hand, not baked measured-state passive followers. The included runtime helper
  authors the 88 structural members into file-backed measured-state snapshots;
  it does not add closed-loop PhysX constraints, contact proof, hardware proof,
  or a trained policy.
- **Reusable rule:** publish the smallest reusable robot package, validate it
  after clean extraction, and ship state-dependent visual logic as a checked
  helper rather than confusing diagnostic snapshot stages with the asset.

### 16. The exported ZIP was relocatable but not yet safe to trust as a LeLab runtime input

- **Observed evidence:** the distribution exporter proved a deterministic
  single-root archive, but LeLab had no consumer-side guard against traversal,
  links/devices, normalized duplicates, resource-exhaustion archives, modified
  checksums, renamed joints, or a corrupted extraction cache. LeLab and the
  Isaac contract also quantized exact grasp boundaries differently.
- **Cause:** validation existed only on the producer path, and the LeLab action
  layer duplicated the grasp table/nearest-code rule instead of importing the
  already-tested Isaac contract.
- **Repair:** the new host-safe distribution loader validates an optional
  trusted archive SHA, member type/path/count/size/compression bounds, the
  manifest schema, the exact five-arm/eight-hand joint-name sets, the complete
  `SHA256SUMS` inventory, and every extracted cache file before reuse. LeLab now
  imports the shared grasp degrees and threshold quantizer, and its Isaac action
  wrapper emits one canonical, finite, exactly named 13-target mapping.
- **Regression coverage:** 27 focused contract/distribution tests cover the
  trusted digest, unsafe paths, symlinks/devices, normalized duplicates,
  checksum mismatch, wrong schema/counts/names, oversized extraction, cache
  corruption repair, exact 0.25/0.75 boundaries, strict numeric targets, arm
  clamping, and six-to-13 expansion. The exporter’s three existing tests remain
  green.
- **Verification:** the 30-test focused suite passes; Ruff passes on every
  changed Python file; a built wheel contains the validation package, shell
  launchers, passive-linkage JSON data, and the new LeLab loader. The accepted
  ZIP validates with its pinned SHA
  `a26ba228eee76f815291adef029c7ed510020cd20bdfae9046c6319d7d99c195`
  and resolves the expected USD entrypoint from a fresh cache.
- **Result/commit:** this entry is included with the distribution-contract
  feature slice.
- **Remaining boundary:** archive checksums prove internal integrity; source
  authenticity requires the caller to provide the expected archive SHA. This
  slice does not start Isaac or prove runtime motion.
- **Reusable rule:** validate release artifacts again at the consumer boundary,
  pin external inputs when authenticity matters, and keep one action/joint-name
  contract shared across producer, host, and simulator code.

### 17. A local Isaac control socket needed a bounded, authenticated, non-retrying contract

- **Observed evidence:** LeLab had no versioned way to exchange commands with an
  Isaac process, and a naïve stream client could accept oversized or
  uncorrelated frames, leak its token in diagnostics, interleave concurrent
  calls, or replay a state-changing command after a truncated response.
- **Cause:** the host and simulator process boundary had no shared schema,
  framing, validation, or failure semantics.
- **Repair:** a pure-stdlib shared JSON Lines contract now authenticates each
  request, bounds every frame, validates exact operation fields and all 13
  physical targets, correlates responses by schema and request ID, restricts
  capture names, and prevents payloads from overriding reserved response
  fields. The LeLab client serializes access, reconstructs fragmented frames,
  redacts credentials, invalidates uncertain connections, and never retries a
  state-changing operation implicitly.
- **Regression coverage:** 27 focused tests cover malformed/oversized frames,
  stable request error codes, exact numeric targets, unsafe capture paths,
  reserved response fields, fragmented and mismatched responses, token
  redaction, pre-send validation, truncated state changes, timeouts, and
  concurrent request serialization.
- **Verification:** `tests/test_superarm_isaac_protocol.py` passes all 27 tests
  with only the repository's existing Starlette deprecation warning, and Ruff
  passes the shared contract, LeLab client, and protocol tests.
- **Remaining boundary:** this slice defines and verifies the host-safe wire
  contract only; the Isaac main-thread server and container launcher are the
  next committed slice.
- **Reusable rule:** once delivery of a state change becomes uncertain, close
  the transport and require explicit recovery instead of guessing or replaying.

### 18. The validated USD had no package-safe long-lived Isaac control process

- **Observed evidence:** the accepted distribution could be validated and
  rendered only by one-shot scripts. LeLab could not keep its articulation
  alive, address joints by name, request telemetry, hold safely, or capture a
  frame without restarting Isaac.
- **Cause:** validation scripts owned both the test sequence and application
  lifecycle; there was no main-thread bridge, isolated launcher, or installed
  package contract for continuous local control.
- **Repair:** the new bridge starts `SimulationApp` before every Omniverse/Isaac
  import, enumerates and requires exactly one articulation root, rejects anything
  except the exact 13 names, maps indices by name, and runs physics plus a
  single-client authenticated JSONL server on the Isaac main thread. Its
  launcher mounts the asset and Python package read-only, keeps artifacts in a
  session run directory, requires the token file to remain outside that
  read-write directory, mounts the token separately read-only, uses a unique
  owned container, and records metadata/logs. Each on-demand capture warms
  Replicator, waits for output, independently tears down its writer, render
  product, camera, and temporary layer, validates a staged PNG, and atomically
  publishes it before another command. All post-app imports are inside the
  application cleanup guard, and token-bearing client/server messages are
  redacted. An unauthenticated socket stays pending only until a one-second
  hello deadline (and is replaced by the next candidate), while the read-write
  run directory must be disjoint from both read-only source mounts. Runtime or
  peer-reset failures during authenticated hello close only that pending socket,
  leaving the bridge available for a later client.
- **Regression coverage:** the 41-test protocol/bridge suite locks standalone
  import ordering and cleanup, unique articulation selection, request-ID bounds,
  non-ASCII authentication, token redaction, full loopback lifecycle, executed
  Replicator teardown including a detach failure, repeated capture followed by
  command, exact launcher mounts/environment/entrypoint/token isolation,
  idle-client eviction, failed/reset hello recovery, read-write/read-only path
  disjointness, framing, correlation, serialization, and non-retry semantics.
- **Verification:** all 41 focused tests pass; shell syntax and Ruff pass. A
  fresh wheel installed into an isolated Python 3.14 environment imports the
  shared protocol and host-safe bridge module and contains `bridge_protocol.py`,
  `control_bridge.py`, `run_isaacsim60_control_bridge.sh`, and the required
  passive-linkage JSON data.
- **Remaining boundary:** these are host-safe, packaging, and loopback proofs.
  Live Isaac articulation motion and close-up visual capture through this new
  bridge remain mandatory end-to-end acceptance work after the LeLab runtime
  and API are connected.
- **Reusable rule:** keep simulator APIs on the thread that owns the application,
  mount code/assets read-only, and treat wheel-installed resources as part of
  the runtime contract rather than relying on the repository checkout.

### 19. LeLab had no runtime-neutral session path to the long-lived Isaac bridge

- **Observed evidence:** the managed bridge could own and control the 13-joint
  articulation, but the LeLab service accepted only MuJoCo modes. Browser,
  manual, and future LeRobot callers had no six-value logical-action route,
  Isaac telemetry route, explicit capture route, or independent live-command
  watchdog.
- **Cause:** the existing service and API were typed and constructed around
  `MuJoCoRuntime`; MuJoCo arm and hand updates were separate calls, continuous
  video was assumed for every runtime, and the ten-second hold depended on a
  later telemetry/websocket call.
- **Repair:** a common SuperArm runtime protocol now supports atomic partial and
  six-value logical commands. `IsaacSimRuntime` validates/extracts the pinned
  distribution, launches or connects to the authenticated bridge, requires the
  exact 13-joint hello contract, caches complete named targets, maps website
  hand degrees to positive Isaac coordinates, bounds observation polling to
  20 Hz, owns hold/shutdown/process-group escalation, and confines captures to
  regular non-symlink files under its managed session directory or an explicit
  shared external run directory. The LeLab service
  now selects MuJoCo or Isaac through injected factories, enforces live timeout
  from a per-session watchdog, validates the configured distribution before
  advertising readiness, and exposes session, telemetry, logical-action, and
  on-demand capture APIs while returning a clear 409 for Isaac continuous
  video. MuJoCo received compatible atomic wrappers and remains a separate
  backend with its existing sign convention.
- **Regression coverage:** fake bridge/process tests cover file-backed token
  launch, exact hello names/counts, full 13-target commands, positive motor2
  mapping, 20 Hz observation caching, bounded timeout phase/log diagnostics,
  safe and repeated captures followed by command, managed/external ownership,
  and TERM/KILL process-group fallback. Service/API tests cover serialized
  command/e-stop ordering, blocked-watchdog disconnect/reconnect safety, stale
  callback rejection, validated capability reporting, factory
  selection, 5+8 telemetry, six-value logical action, independent watchdog,
  capture/latest, video rejection, and atomic MuJoCo arm-plus-hand updates. The
  former MuJoCo-only source-removal assertion is replaced by the durable host
  import boundary: importing `lelab.server` must not load `isaacsim`, `omni`,
  or `pxr`.
- **Verification:** the runtime/service/API plus MuJoCo regression command
  passes 54 tests with the repository's existing deprecation warnings; Ruff,
  `py_compile`, and `git diff --check` pass on the changed slice. An independent
  read-only test-engineer reran the 54 tests and focused Ruff, re-reviewed every
  rejected safety/lifecycle gap, and returned APPROVE with no remaining Task 4
  blocker.
- **Result/commit:** this entry is included in the LeLab Isaac session-runtime
  feature commit.
- **Remaining boundary:** this is fake-bridge, host lifecycle, API, and MuJoCo
  regression proof. The LeRobot `superarm_isaac` backend, website selector,
  real Isaac Sim 6.0 process, measured motion, close-up captures, GIF, and final
  distribution-controlled acceptance report remain pending. Isaac capture is
  intentionally serialized and non-preemptible on the single bridge connection,
  so an emergency stop waits for an in-progress capture request to finish.
- **Reusable rule:** keep policy actions logical and runtime-neutral, expand to
  the physical joint set only at the backend boundary, and give simulators with
  different rendering capabilities explicit APIs rather than pretending every
  backend is a video stream.

### 20. The Isaac session existed but was not a registered six-control LeRobot robot

- **Observed evidence:** LeLab could start and command an `isaac_sim` session,
  but LeRobot knew only `superarm_mujoco`. Policy, dataset, and future ACT/VLA
  code therefore had no registered Isaac robot configuration even though the
  service already enforced the correct six-to-13 action boundary.
- **Cause:** runtime integration and LeRobot device registration are separate
  layers. Reusing the MuJoCo class would also have projected motor2 through the
  wrong negative-coordinate visualization mapping.
- **Repair:** `SuperArmIsaacRobotConfig` is registered as `superarm_isaac` with
  managed/external bridge settings and the server-local distribution path.
  `SuperArmIsaacRobot` exposes exactly the canonical five-arm-plus-one-grasp
  action and observation features, sends actions through the service's atomic
  logical route, returns the 13 measured Isaac joint positions unchanged for
  visualization, rejects an active non-Isaac session, and disconnects only a
  session it started. The sixth observation is explicitly last-commanded grasp
  state until a measured-grasp classifier is separately validated. A matching
  YAML config preserves the arm limits, SO-101 mapping, three fixed motions,
  exact 13 physical names, and Isaac 6.0 defaults.
- **Regression coverage:** fake-service tests lock six-wide NumPy actions,
  13-target expansion, positive `finger1_motor2=1.10` at close, wrong-width
  rejection, measured arm observations, commanded-grasp semantics, positive
  13-joint visualization, session ownership, non-Isaac conflict, and YAML
  parity. Existing MuJoCo and shared Isaac contract tests remain green.
- **Verification:** the focused LeRobot Isaac, MuJoCo, and shared-contract suite
  passes 19 tests; focused Ruff passes. An independent read-only test-engineer
  reran the suite, reviewed registration, feature width, sign semantics,
  ownership, conflict behavior, and YAML parity, then returned APPROVE with no
  remaining Task 5 blocker.
- **Result/commit:** this entry is included in the registered Isaac LeRobot
  backend feature commit.
- **Remaining boundary:** teleoperation, manual leader, recording, website
  selection, and live Isaac motion/visual evidence are still pending. This
  slice does not claim a measured sixth grasp observation.
- **Reusable rule:** policy feature width belongs to the robot-learning
  contract, while simulator joint count and coordinate signs belong to the
  backend; never expose the 13 physical joints as a 13-wide ACT/VLA action.

### 21. The registered Isaac robot was still unreachable from LeLab teleoperation and recording

- **Observed evidence:** `superarm_isaac` existed at the LeRobot device layer,
  but the website teleoperation, manual leader, recording config, recording
  action, and saved-robot paths still selected only `superarm_mujoco`. An Isaac
  request therefore fell into SO-101 calibration or was rejected, and the
  manual hand panel still projected the hand through MuJoCo's coordinate signs.
- **Cause:** backend selection was duplicated as exact MuJoCo string checks in
  the web and recording layers, and robot records did not persist or validate
  the server-local Isaac distribution and bridge settings.
- **Repair:** a shared SuperArm backend predicate now routes both simulation
  backends without changing SO-101 behavior. Teleoperation and recording
  construct `SuperArmIsaacRobotConfig`, propagate the optional pinned SHA and
  managed/external bridge settings, and preserve the canonical six-wide
  dataset contract. The manual leader keeps five arm sliders plus one fixed
  grasp code but expands Isaac hand presets directly to positive URDF targets.
  An optional diagnostic Isaac robot record appears only when the configured
  distribution validates; MuJoCo remains the first primary built-in. Isaac
  cleanliness revalidates the pinned archive, YAML type, bridge mode, managed
  loopback host, and port. A malformed ZIP is omitted instead of breaking robot
  listing. Record-scoped MJCF visual routes remain MuJoCo-only.
- **Regression coverage:** tests lock SO-101 calibration bypass, backend labels
  and telemetry, six logical plus 13 physical targets, positive close values,
  manual recording actions, LeRobot action/observation shape `(6,)`, request or
  server-default distribution selection, pinned-SHA cleanliness, malformed-ZIP
  omission, MuJoCo-first built-in ordering, and Isaac rejection by MJCF-only
  routes.
- **Verification:** the full Task 6 regression command passes 91 tests with only
  the repository's existing deprecation warnings; focused Ruff, `py_compile`,
  and `git diff --check` pass. An independent read-only test engineer first
  rejected the uncaught real `BadZipFile`, then reproduced the repair with a
  malformed file and returned APPROVE after 87 focused tests and Ruff passed.
- **Result/commit:** this entry is included in the LeLab Isaac teleoperation and
  recording feature commit.
- **Remaining boundary:** the website still needs an explicit Isaac selector and
  capture UI. This slice is host/fake-runtime proof and does not claim live
  Isaac Sim motion, screenshots, GIF evidence, a real recorded episode, or a
  trained ACT/VLA policy.
- **Reusable rule:** select related backends through one shared predicate, but
  keep coordinate conversion, asset capability, and visual-route ownership
  backend-specific; optional diagnostics must fail closed without taking down
  the primary robot list.

### 22. The website needed a truthful Isaac selector, visible URDF hand, and immutable capture response

- **Observed evidence:** the website treated every SuperArm record as MuJoCo,
  offered only continuous `/api/superarm/video`, and did not propagate Isaac
  distribution/bridge settings into manual or recording requests. The first
  Isaac UI draft then disabled the MJCF overlay while still loading the URDF
  variant that deliberately removes every AmazingHand visual, producing an
  arm-only showroom despite valid hand telemetry. Its capture image route also
  rechecked only suffix, existence, and size, so a same-size replacement could
  be served after validation.
- **Cause:** backend checks and visualization assumptions were duplicated in
  frontend pages. The MuJoCo-specific stripped-URDF-plus-MJCF-overlay design
  was reused for Isaac even though Isaac must not fetch MJCF routes. Capture
  metadata was treated as a stable file authorization rather than a snapshot
  whose identity and bytes must be revalidated at response time.
- **Repair:** shared frontend helpers now recognize both LeLab simulation
  backends, build only the relevant Isaac session fields, keep continuous
  video MuJoCo/hybrid-only, and expose Isaac as explicit on-demand whole/hand
  capture. The dashboard reports measured joint coverage, not a hard-coded
  13/13, and merges measured five-arm plus eight-hand positions into the URDF
  viewer. Isaac loads a hand-preserving URDF query variant; MuJoCo keeps its
  stripped URDF and exact MJCF visual overlay. The server fingerprints each
  accepted capture by resolved path, device, inode, size, modification time,
  and SHA-256, rereads it through a stable regular-file descriptor, and returns
  validated PNG bytes instead of reopening a `FileResponse` path. Capture UI
  metadata is cleared and its image-version key advanced on connect,
  disconnect, and websocket-reported disconnect; image responses are marked
  `Cache-Control: no-store`, so a prior session cannot be labeled current.
- **Regression coverage:** Vitest locks backend predicates, video/capture
  capability separation, Isaac payload construction, measured hand merging,
  measured joint count, cache-busted image URLs, and hand-preserving URDF URL
  selection. The same helper suite locks capture clear-and-invalidate behavior
  at session boundaries. Python tests lock the MuJoCo stripped-visual default, the Isaac
  hand-visual variant, capture/latest API behavior, PNG identity/size/content
  validation, and rejection of a same-size post-capture replacement. Existing
  teleoperation, recording, MuJoCo, runtime, and server suites remain gates.
- **Verification:** leader verification passes 122 focused Python tests, Ruff,
  frontend ESLint with zero errors, 27 frontend tests, the Vite production
  build, and `git diff --check`. An independent read-only test engineer first
  rejected the invisible Isaac URDF hand and mutable capture route, then found
  and rejected stale cross-session browser capture state. After both repair
  rounds it reran the focused gates and returned APPROVE with no remaining
  Task 7 blocker. Final leader verification passes 122 Python tests, Ruff,
  28 frontend tests, ESLint, and the Vite build.
- **Result/commit:** this entry is included in the truthful Isaac website
  control feature commit.
- **Remaining boundary:** website and fake-runtime behavior are verified, but
  live Isaac Sim 6.0 motion, nonblank open/half/close close-ups, GIF review,
  numeric thresholds, and a real recording episode remain unclaimed and are
  the next acceptance gate.
- **Reusable rule:** a backend capability must select its own visualization
  assets and evidence semantics; never hide one renderer without restoring a
  visible alternative, never display expected joint coverage as measured
  coverage, and never serve a mutable artifact path after authorization.

### 23. Live LeLab control passed, but long-lived Isaac capture could not be made deadline-safe

- **Observed evidence:** the first API-driven acceptance draft could report a
  settled case from cached telemetry before Isaac had accepted the new target.
  Once the runner required a strictly newer `command_sequence` and an exact
  reported 13-target match, all three commands settled within the planned
  numeric limits. The same live session could not produce a deadline-safe
  image: Replicator writer capture stalled, the legacy camera returned no RGBA,
  experimental `RtxCamera`/`CameraSensor` returned no RGB, viewport capture
  stalled, an isolated child Kit render exceeded its 120--300 second deadline
  while consuming more than 10 GB, and a two-step paused Replicator attempt
  timed out at 300 seconds. A one-step attempt returned without a frame.
- **Cause:** command telemetry and rendering are different proof paths. The
  long-lived headless control stage is stable at 120 Hz, but none of the tested
  Isaac Sim 6.0 headless render surfaces both produced a usable image and
  returned within a bounded request deadline on this host. Treating the old
  static pose frames as live capture would have hidden that failure.
- **Repair:** the acceptance runner now gates each case on sequence advancement,
  exact named targets, and settled measured error. It records numeric control,
  emergency hold, ten-second live-timeout hold, managed cleanup, and reconnect
  as live evidence. Static whole/open/half/close Isaac frames are copied only as
  `prevalidated_static_isaac_visuals` with `is_live_session_capture=false`; the
  generated GIF inherits that boundary. The runtime advertises
  `supports_capture=false`, bridge/runtime capture calls fail immediately, the
  service rejects unsupported capture, and the website uses measured 5+8 joint
  telemetry to animate the URDF showroom while explaining the separate static
  evidence.
- **Regression coverage:** E2E unit tests reject stale sequences, wrong targets,
  nonblank-but-identical visual frames, and any visual record mislabeled as a
  live session capture. Runtime/service/protocol tests lock the immediate
  capture rejection while preserving command and hold behavior. Frontend tests
  lock Isaac capture capability off without changing MuJoCo continuous video.
- **Verification:**
  `isaacsim_test/artifacts/lelab_isaac_e2e_20260722T112742Z/lelab-isaac-e2e-report.json`
  is `PASS` with Isaac Sim `6.0.0`, one articulation, 13 unique expected joints,
  logical width six, maximum settled arm error `0.009771 rad`, maximum settled
  hand error `0.000989 rad`, 196 emergency-hold steps, 137 live-timeout-hold
  steps, managed disconnect in `6.446 s`, and reconnect success. Adjacent static
  hand-frame mean absolute differences are `8.2428` and `6.3470`; all three
  frames are nonblank. These visual metrics are not live-session evidence.
- **Result/commit:** this entry accompanies the LeLab-controlled Isaac
  acceptance and capture-boundary commit.
- **Remaining boundary:** there is no live Isaac viewport PNG or browser
  screenshot of the actual runtime in this slice, and no real LeRobot episode,
  trained ACT/VLA policy, contact/grasp-retention proof, ROS 2 path, or physical
  motor protocol. The static images prove the distributed USD's visual poses;
  the live report proves named control, measured convergence, safety holds, and
  lifecycle cleanup.
- **Reusable rule:** never let a stale observation satisfy a new command, and
  never merge numeric runtime proof with a separate static visual proof. A
  simulator capture capability is enabled only after it produces a reviewed
  frame and returns within a bounded control-safe deadline.

### 24. Final evidence now binds to the controlled distribution and tolerates measured Isaac stalls

- **Observed evidence:** independent verification rejected the first Task 8
  report because its static frames came from validation run
  `20260722T045604Z-combined-zip-usd-clean`, while the controlled ZIP embeds the
  later passive-linkage validation run
  `20260722T070208Z-combined-zip-passive-linkage-r3`. During the repair, repeated
  full-sequence runs also exposed occasional headless Isaac physics steps long
  enough to exceed the original two-second bridge response deadline or make a
  ten-second 120-step stability window too short. Instrumented diagnosis showed
  the request protocol remained ordered and measured closed-hand step latency
  spikes into the hundreds of milliseconds; one clean run completed at 9,211
  physics steps.
- **Cause:** the acceptance runner proved that its copied report was `PASS`, but
  did not prove that report was the one hashed into the controlled distribution
  manifest. Separately, host bridge deadlines assumed smoother Isaac stepping
  than this passive-linkage asset produces under a long closed-hand dwell.
- **Repair:** distribution validation now extracts the manifest before launch,
  requires the exact `validation/isaac-report.json` SHA and validation run ID,
  copies only the final passive-linkage whole/open/half/close evidence, and
  rejects any provenance mismatch. The host bridge request timeout is still
  bounded but is five seconds, and the condition-based hold gate allows up to
  30 seconds to accumulate 120 real physics steps. No state-changing request is
  retried, and the live timeout remains ten seconds.
- **Regression coverage:** tests reject mismatched report SHA/run provenance,
  require nonempty passive-linkage visual results, lock the five-second bounded
  bridge timeout, and prove the stability gate waits for 120 steps rather than
  accepting elapsed wall time.
- **Verification:** the clean final report
  `isaacsim_test/artifacts/lelab_isaac_e2e_20260722T121339Z/lelab-isaac-e2e-report.json`
  is `PASS` for distribution SHA
  `a26ba228eee76f815291adef029c7ed510020cd20bdfae9046c6319d7d99c195`
  and embedded validation report SHA
  `1785dfe1b790ad42f0ce4798637eab13e3325acf86a9f507289c33b76e84d29b`.
  All three logical cases settled with maximum arm error `0.008436 rad` and
  maximum hand error `0.000995 rad`; emergency hold remained stable for 211
  steps, the ten-second watchdog hold for 137 steps, managed disconnect took
  `6.249 s`, and reconnect passed. Final passive-linkage frame differences are
  `3.0509` and `3.5683`; their reviewed static/not-live proof boundary is
  unchanged.
- **Result/commit:** this entry accompanies the distribution-bound evidence and
  Isaac stall-tolerance repair commit.
- **Remaining boundary:** this remains numeric live control plus separately
  validated static USD visual evidence. It does not claim live viewport capture,
  a real LeRobot episode, contact/grasp retention, ROS 2, or real hardware.
- **Reusable rule:** bind every copied proof artifact to the exact tested
  distribution digest, and express simulator stability as a physics-step
  condition with a bounded but hardware-realistic wall-clock allowance.
