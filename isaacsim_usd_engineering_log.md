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
