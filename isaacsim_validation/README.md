# SuperArm + AmazingHand USD validation in Isaac Sim 6.0

This directory builds and validates one SuperArm + AmazingHand robot asset for
Isaac Sim. The recommended learning profile uses the supplied Isaac Sim USD
archive for the detailed hand appearance while retaining the existing combined
SuperArm URDF as the single owner of robot physics.

## Authoritative hand source

```text
/home/dong/july/superarm_ws/isaacsim_test/artifacts/distributions/
  amazinghand_isaac_sim_usd_distribution_20260722.zip
SHA256: 3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377
entry: usd/amazinghand_graspable/amazinghand_graspable.usda
```

Preparation verifies the checksum, rejects unsafe archive paths, preserves the
eight hand-joint contract, and repairs the source package's invalid static
visual-shell rigid-body binding.

## Robot and control contract

The final `zip_learning` robot has:

- one articulation root;
- 13 physical movable joints: five SuperArm joints and eight AmazingHand joints;
- 28 rigid bodies and 13 collision prims in the verified import;
- one logical six-value LeRobot action: five arm values plus one grasp value;
- the grasp value expanded to fixed open/half-close/close targets for all eight
  hand joints.

The supplied hand USD's articulation is **not** nested into the SuperArm. Only
its checked visual payloads are copied and referenced beneath the existing
wrist, proximal, and distal links. This avoids two competing articulations.

## Profiles

| Profile | Purpose | Hand appearance |
| --- | --- | --- |
| `zip_learning` | Recommended Isaac/VLA/RL asset | Supplied Isaac USD wrist/palm/servo frame plus 88 shell-free structural linkage followers driven from the combined 13-DOF physics tree |
| `learning` | Diagnostic serial-URDF learning asset | Selected local URDF shell meshes |
| `aligned` | LeLab transforms with original URDF visuals | All generated URDF visuals retained |
| `served` | Browser/showroom-compatible tree | Hand visuals removed for the website overlay |
| `raw` | Unmodified source diagnostic | Original source transforms and visuals |

## Reproduce the verified run

The tested runtime is `nvcr.io/nvidia/isaac-sim:6.0.0`.

```bash
export SOURCE_WS=/home/dong/july/superarm_ws
export SOURCE_URDF="$SOURCE_WS/isaacsim_test/outputs/robot_arm_hand_from_zip_local_drive/superarm_amazinghand.urdf"
export HAND_ZIP="$SOURCE_WS/isaacsim_test/artifacts/distributions/amazinghand_isaac_sim_usd_distribution_20260722.zip"
export HAND_SHA=3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377
export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-combined-zip-usd"
export RUN_ROOT="artifacts/isaacsim_superarm/$RUN_ID"

.venv/bin/python -m isaacsim_validation.prepare_superarm_urdf \
  --source-urdf "$SOURCE_URDF" \
  --source-root "$SOURCE_WS" \
  --output-dir "$RUN_ROOT/zip_learning_input" \
  --profile zip_learning

.venv/bin/python -m isaacsim_validation.prepare_amazinghand_usd \
  --source-zip "$HAND_ZIP" \
  --expected-sha256 "$HAND_SHA" \
  --output-dir "$RUN_ROOT/zip_hand_source"

isaacsim_validation/run_isaacsim60_validation.sh \
  zip_learning "$RUN_ID" "$RUN_ROOT/zip_hand_source"

isaacsim_validation/run_asset_validator.sh \
  "$RUN_ROOT/zip_learning_isaac/superarm_amazinghand/superarm_amazinghand.usda" \
  "$RUN_ROOT/zip_learning_isaac/asset-validator.json"
```

## Export the validated distribution

The exporter accepts only a runtime `PASS`, a strict validator pass with zero
blocking issues, one articulation root, 13 revolute joints, successful arm and
hand motion, clean-package restoration, and the checked 88-part passive visual
contract. It writes a deterministic single-root ZIP with relative USD
composition arcs, both license texts, a manifest, per-file checksums, the
shell-free passive-linkage helper, and bounded validation evidence. Measured
70 MB snapshot stages and runtime world state are deliberately excluded.

```bash
export ACCEPTED_RUN=artifacts/isaacsim_superarm/20260722T070208Z-combined-zip-passive-linkage-r3/zip_learning_isaac
export DIST=superarm_amazinghand_isaac_sim_usd_distribution_20260722

.venv/bin/python -m isaacsim_validation.export_superarm_usd_distribution \
  --source-asset-dir "$ACCEPTED_RUN/superarm_amazinghand" \
  --runtime-report "$ACCEPTED_RUN/isaac-report.json" \
  --validator-report "$ACCEPTED_RUN/asset-validator.json" \
  --preview-image "$ACCEPTED_RUN/passive_linkage_contact_sheet.png" \
  --hand-license-file artifacts/amazinghand_distribution_audit/20260722/amazinghand_isaac_sim_usd_distribution_20260722/LICENSE-AmazingHandControl \
  --output-zip "artifacts/distributions/$DIST.zip" \
  --distribution-name "$DIST" \
  --validation-run-id 20260722T070208Z-combined-zip-passive-linkage-r3
```

Accepted archive:

```text
artifacts/distributions/superarm_amazinghand_isaac_sim_usd_distribution_20260722.zip
SHA256: a26ba228eee76f815291adef029c7ed510020cd20bdfae9046c6319d7d99c195
entry: usd/superarm_amazinghand/superarm_amazinghand.usda
```

After extraction, run `sha256sum -c SHA256SUMS` from the distribution
directory. The accepted clean-extraction validator result is retained under
`artifacts/distribution_validation/superarm_amazinghand_isaac_sim_usd_distribution_20260722/`.

## Evidence and proof boundaries

The accepted passive-linkage run is
`artifacts/isaacsim_superarm/20260722T070208Z-combined-zip-passive-linkage-r3/`:

- `zip_learning_isaac/isaac-report.json`: runtime `PASS`, 13 DOFs, one
  articulation, six-value logical action, arm motion PASS, hand motion PASS,
  88 passive visual followers with 22 per finger, zero outer shells, and zero
  physics schemas on the visual followers;
- `zip_learning_isaac/asset-validator.json`: strict Isaac Sim 6.0 validator
  PASS with zero blocking issues;
- `zip_learning_isaac/whole_robot.png`: reviewed full robot with attached hand;
- `zip_learning_isaac/hand_open.png`, `hand_half_close.png`, and
  `hand_close.png`: reviewed, nonblank direct frames from measured physics
  snapshots, with adjacent RMS differences `14.7731` and `16.5492`;
- `zip_learning_isaac/hand_finger1_close.png` through
  `hand_finger4_close.png`: reviewed independent-finger evidence after each
  case resets to the measured open baseline;
- every reopened snapshot validates all 88 local transforms and source
  identities against its generated follower contract within `1e-6`.

This proves source integrity, USD composition, Isaac articulation response, and
visible open/close motion with source-informed passive-linkage geometry. It
does not prove real DM4340P CAN transport, AmazingHand serial transport,
closed-loop PhysX constraints, contact/grasp quality, or a trained policy
rollout. The runtime physics remains the existing eight-joint open-chain hand;
the supplied linkage members are visual-only followers solved from checked
offline source keyframes. Rounded `proximal_shell` and `distal_shell` parts
remain excluded and are intentionally deferred to a later cosmetic pass.

## Required engineering record

After each Isaac USD problem is repaired and freshly verified, append the
symptom, evidence path, cause, repair, regression check, result/commit,
remaining boundary, and reusable rule to
[`isaacsim_usd_engineering_log.md`](../isaacsim_usd_engineering_log.md). A fix
is not considered documented until that cumulative entry exists; earlier
entries are preserved rather than rewritten.
