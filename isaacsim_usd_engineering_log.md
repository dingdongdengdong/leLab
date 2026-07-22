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
