from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from isaacsim_validation.generate_passive_linkage_keyframes import MANIFEST_VERSION

MANIFEST = Path("isaacsim_validation/data/amazinghand_passive_linkage_keyframes.json")


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text())


def test_module_imports_without_mujoco_for_manifest_boundaries() -> None:
    code = """
import builtins
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'mujoco' or name.startswith('mujoco.'):
        raise ModuleNotFoundError('blocked mujoco import')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from isaacsim_validation.generate_passive_linkage_keyframes import MANIFEST_VERSION, write_manifest
assert MANIFEST_VERSION == 1
assert callable(write_manifest)
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_mujoco_id_validation_rejects_missing_ids_before_indexing() -> None:
    from isaacsim_validation.generate_passive_linkage_keyframes import _require_mujoco_id

    assert _require_mujoco_id(0, "joint", "finger1_motor1") == 0
    with pytest.raises(ValueError, match="Missing MuJoCo joint: finger1_motor1"):
        _require_mujoco_id(-1, "joint", "finger1_motor1")
    with pytest.raises(ValueError, match="Missing MuJoCo body: r_wrist_interface"):
        _require_mujoco_id(-1, "body", "r_wrist_interface")


def test_source_mjcf_motor2_direction_matches_verified_amazinghand_close() -> None:
    from isaacsim_validation.generate_passive_linkage_keyframes import _source_mjcf_target

    assert _source_mjcf_target(1, 0.95) == pytest.approx(0.95)
    assert _source_mjcf_target(2, 1.10) == pytest.approx(-1.10)


def test_committed_manifest_serialization_is_byte_stable_without_generation(tmp_path: Path) -> None:
    from isaacsim_validation.generate_passive_linkage_keyframes import write_manifest

    manifest = load_manifest(MANIFEST)
    regenerated = tmp_path / "manifest.json"

    write_manifest(manifest, regenerated)

    assert regenerated.read_bytes() == MANIFEST.read_bytes()


def test_manifest_uses_checked_original_mjcf_and_zip_geometry() -> None:
    manifest = load_manifest(MANIFEST)

    assert MANIFEST_VERSION == 1
    assert manifest["manifest_version"] == MANIFEST_VERSION
    assert (
        manifest["source_mjcf_sha256"] == "d21366e7c9a1f5debe04b8abb5ea1ade7fade42e493e09d003f5db196548b098"
    )
    assert (
        manifest["source_hand_zip_sha256"]
        == "3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377"
    )
    assert (
        manifest["source_package_zip_sha256"]
        == "c10c91ac240ac18893ab0a102e2ac6f9aa8a6a2e75c738fe6209f2d50a122b4a"
    )
    assert manifest["finger_count"] == 4
    assert manifest["parts_per_finger"] == 22
    assert manifest["structural_visual_part_count"] == 88


def test_manifest_excludes_shells_and_decorative_fasteners() -> None:
    manifest = load_manifest(MANIFEST)
    names = [part["source_prim"] for part in manifest["parts"]]

    assert all("proximal_shell" not in name for name in names)
    assert all("distal_shell" not in name for name in names)
    assert set(manifest["excluded_shell_indices"]) == {45, 51, 78, 85, 114, 115, 144, 152}
    assert all(part["mesh_role"] not in {"screw", "washer", "std_fastener"} for part in manifest["parts"])
    assert {part["source_index"] for part in manifest["parts"]} >= {44, 52, 76, 84, 113, 117, 147, 153}


def test_manifest_has_three_normalized_keyframes_for_each_part() -> None:
    manifest = load_manifest(MANIFEST)

    assert [keyframe["name"] for keyframe in manifest["keyframes"]] == ["open", "half_close", "close"]
    assert manifest["solver"]["step_count"] == 5000
    assert manifest["solver"]["dt"] == 0.002
    assert manifest["solver"]["max_equality_site_pair_separation_m"] < 1e-6
    assert manifest["solver"]["max_motor_target_error_rad"] < 1e-4
    assert len(manifest["parts"]) == 88
    assert all(
        set(part["transforms"].keys()) == {"open", "half_close", "close"} for part in manifest["parts"]
    )
    assert all(
        len(transform["translate_m"]) == 3 and len(transform["orient_wxyz"]) == 4
        for part in manifest["parts"]
        for transform in part["transforms"].values()
    )


def test_manifest_close_uses_source_motor2_flexion_and_reaches_distal_endpoint() -> None:
    import math

    manifest = load_manifest(MANIFEST)
    reports = {item["name"]: item for item in manifest["solver"]["keyframe_reports"]}
    assert reports["open"]["source_mjcf_targets_rad"]["motor2"] == pytest.approx(-0.02)
    assert reports["half_close"]["source_mjcf_targets_rad"]["motor2"] == pytest.approx(-0.56)
    assert reports["close"]["source_mjcf_targets_rad"]["motor2"] == pytest.approx(-1.10)

    distal = next(
        part for part in manifest["parts"] if part["finger"] == 1 and part["mesh_role"] == "distal_core"
    )
    opened = distal["transforms"]["open"]
    closed = distal["transforms"]["close"]
    dot = min(
        1.0,
        abs(sum(a * b for a, b in zip(opened["orient_wxyz"], closed["orient_wxyz"], strict=True))),
    )
    rotation_deg = math.degrees(2.0 * math.acos(dot))
    translation_m = math.dist(opened["translate_m"], closed["translate_m"])
    assert rotation_deg > 60.0
    assert translation_m > 0.04


OPEN_MEASURED = {
    "finger1_motor1": 0.05,
    "finger1_motor2": 0.02,
    "finger2_motor1": 0.05,
    "finger2_motor2": 0.02,
    "finger3_motor1": 0.05,
    "finger3_motor2": 0.02,
    "finger4_motor1": 0.05,
    "finger4_motor2": 0.02,
}
HALF_CLOSE_MEASURED = {
    "finger1_motor1": 0.50,
    "finger1_motor2": 0.56,
    "finger2_motor1": 0.50,
    "finger2_motor2": 0.56,
    "finger3_motor1": 0.50,
    "finger3_motor2": 0.56,
    "finger4_motor1": 0.50,
    "finger4_motor2": 0.56,
}
CLOSE_MEASURED = {
    "finger1_motor1": 0.95,
    "finger1_motor2": 1.10,
    "finger2_motor1": 0.95,
    "finger2_motor2": 1.10,
    "finger3_motor1": 0.95,
    "finger3_motor2": 1.10,
    "finger4_motor1": 0.95,
    "finger4_motor2": 1.10,
}


def _pose_signature(pose):
    return (
        pose.finger,
        pose.source_prim,
        pose.instance_prim,
        tuple(round(v, 12) for v in pose.translate),
        tuple(round(v, 12) for v in pose.orient),
    )


def changed_fingers(baseline, moved) -> set[int]:
    return {
        before.finger
        for before, after in zip(baseline, moved, strict=True)
        if _pose_signature(before) != _pose_signature(after)
    }


def test_solver_imports_without_mujoco() -> None:
    code = """
import builtins
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'mujoco' or name.startswith('mujoco.'):
        raise ModuleNotFoundError('blocked mujoco import')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from isaacsim_validation.passive_linkage import PassiveVisualPose, solve_passive_linkage
assert PassiveVisualPose.__dataclass_params__.frozen
assert callable(solve_passive_linkage)
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_solver_returns_normalized_finite_pose_for_every_part() -> None:
    import math

    from isaacsim_validation.passive_linkage import solve_passive_linkage

    poses = solve_passive_linkage(HALF_CLOSE_MEASURED)

    assert len(poses) == 88
    assert {pose.finger for pose in poses} == {1, 2, 3, 4}
    assert all(all(math.isfinite(v) for v in pose.translate) for pose in poses)
    assert all(math.isclose(sum(v * v for v in pose.orient), 1.0, abs_tol=1e-6) for pose in poses)


def test_each_finger_uses_only_its_measured_motor_pair() -> None:
    from isaacsim_validation.passive_linkage import solve_passive_linkage

    baseline = solve_passive_linkage(OPEN_MEASURED)
    moved = solve_passive_linkage({**OPEN_MEASURED, "finger3_motor1": 0.95, "finger3_motor2": 1.10})

    assert changed_fingers(baseline, moved) == {3}


def test_solver_exactly_returns_manifest_keyframes_for_open_half_close() -> None:
    from isaacsim_validation.passive_linkage import solve_passive_linkage

    manifest = load_manifest(MANIFEST)

    for measured, keyframe in [
        (OPEN_MEASURED, "open"),
        (HALF_CLOSE_MEASURED, "half_close"),
        (CLOSE_MEASURED, "close"),
    ]:
        poses = solve_passive_linkage(measured)
        for pose, part in zip(poses, manifest["parts"], strict=True):
            expected = part["transforms"][keyframe]
            assert pose.source_prim == part["source_prim"]
            assert pose.instance_prim == part["instance_prim"]
            assert pose.translate == tuple(expected["translate_m"])
            assert pose.orient == tuple(expected["orient_wxyz"])


def test_solver_rejects_missing_or_nonfinite_measured_joints() -> None:
    from isaacsim_validation.passive_linkage import solve_passive_linkage

    with pytest.raises(ValueError, match="Missing measured joint: finger2_motor1"):
        solve_passive_linkage({k: v for k, v in OPEN_MEASURED.items() if k != "finger2_motor1"})
    with pytest.raises(ValueError, match="Non-finite measured joint: finger4_motor2"):
        solve_passive_linkage({**OPEN_MEASURED, "finger4_motor2": float("nan")})


def test_finger_closedness_uses_both_motors_and_clamps() -> None:
    import math

    from isaacsim_validation.passive_linkage import finger_closedness

    assert finger_closedness(0.05, 0.02) == 0.0
    assert finger_closedness(0.95, 1.10) == 1.0
    assert math.isclose(finger_closedness(0.50, 1.10), 0.75, abs_tol=1e-12)
    assert finger_closedness(-99.0, -99.0) == 0.0
    assert finger_closedness(99.0, 99.0) == 1.0


def test_manifest_validation_rejects_bad_counts_duplicate_sources_and_solver_thresholds() -> None:
    from copy import deepcopy

    from isaacsim_validation.passive_linkage import validate_passive_linkage_manifest

    manifest = load_manifest(MANIFEST)
    validate_passive_linkage_manifest(manifest)

    bad_count = deepcopy(manifest)
    bad_count["structural_visual_part_count"] = 87
    with pytest.raises(ValueError, match="structural_visual_part_count"):
        validate_passive_linkage_manifest(bad_count)

    duplicate = deepcopy(manifest)
    duplicate["parts"][1]["source_prim"] = duplicate["parts"][0]["source_prim"]
    with pytest.raises(ValueError, match="Duplicate source_prim"):
        validate_passive_linkage_manifest(duplicate)

    loose_sites = deepcopy(manifest)
    loose_sites["solver"]["max_equality_site_pair_separation_m"] = 1e-6
    with pytest.raises(ValueError, match="equality-site"):
        validate_passive_linkage_manifest(loose_sites)

    loose_motor = deepcopy(manifest)
    loose_motor["solver"]["max_motor_target_error_rad"] = 1e-4
    with pytest.raises(ValueError, match="motor target"):
        validate_passive_linkage_manifest(loose_motor)


def test_manifest_validation_rejects_nonfinite_translation_and_bad_quaternion() -> None:
    from copy import deepcopy

    from isaacsim_validation.passive_linkage import validate_passive_linkage_manifest

    manifest = load_manifest(MANIFEST)

    bad_translation = deepcopy(manifest)
    bad_translation["parts"][0]["transforms"]["open"]["translate_m"][0] = float("inf")
    with pytest.raises(ValueError, match="Non-finite translation"):
        validate_passive_linkage_manifest(bad_translation)

    bad_quat = deepcopy(manifest)
    bad_quat["parts"][0]["transforms"]["open"]["orient_wxyz"] = [2.0, 0.0, 0.0, 0.0]
    with pytest.raises(ValueError, match="Non-normalized quaternion"):
        validate_passive_linkage_manifest(bad_quat)


def test_manifest_validation_rejects_tampered_locked_provenance() -> None:
    from copy import deepcopy

    from isaacsim_validation.passive_linkage import validate_passive_linkage_manifest

    manifest = load_manifest(MANIFEST)
    for key in ("source_mjcf_sha256", "source_hand_zip_sha256", "source_package_zip_sha256"):
        tampered = deepcopy(manifest)
        tampered[key] = "0" * 64
        with pytest.raises(ValueError, match=key):
            validate_passive_linkage_manifest(tampered)


def test_manifest_validation_rejects_tampered_shell_exclusion_contract() -> None:
    from copy import deepcopy

    from isaacsim_validation.passive_linkage import validate_passive_linkage_manifest

    manifest = load_manifest(MANIFEST)
    tampered = deepcopy(manifest)
    tampered["excluded_shell_indices"] = [45, 51, 78, 85, 114, 115, 144]

    with pytest.raises(ValueError, match="excluded_shell_indices"):
        validate_passive_linkage_manifest(tampered)


def test_manifest_validation_rejects_tampered_source_index_or_prim_allowlist() -> None:
    from copy import deepcopy

    from isaacsim_validation.passive_linkage import validate_passive_linkage_manifest

    manifest = load_manifest(MANIFEST)

    bad_index = deepcopy(manifest)
    bad_index["parts"][0]["source_index"] = 999
    with pytest.raises(ValueError, match="source_index allowlist"):
        validate_passive_linkage_manifest(bad_index)

    bad_prim = deepcopy(manifest)
    bad_prim["parts"][0]["source_prim"] = "mjcf_026_custom_servo_horn_unapproved_visual_0"
    with pytest.raises(ValueError, match="source_prim allowlist"):
        validate_passive_linkage_manifest(bad_prim)


def test_manifest_validation_rejects_shell_names_and_decorative_roles() -> None:
    from copy import deepcopy

    from isaacsim_validation.passive_linkage import validate_passive_linkage_manifest

    manifest = load_manifest(MANIFEST)

    shell_name = deepcopy(manifest)
    shell_name["parts"][0]["source_prim"] = "mjcf_045_finger_proximal_shell"
    with pytest.raises(ValueError, match="shell visual"):
        validate_passive_linkage_manifest(shell_name)

    decorative_mesh_role = deepcopy(manifest)
    decorative_mesh_role["parts"][0]["mesh_role"] = "screw"
    with pytest.raises(ValueError, match="decorative role"):
        validate_passive_linkage_manifest(decorative_mesh_role)

    decorative_role = deepcopy(manifest)
    decorative_role["parts"][0]["role"] = "washer"
    with pytest.raises(ValueError, match="decorative role"):
        validate_passive_linkage_manifest(decorative_role)


def test_manifest_validation_requires_unique_integer_source_indices() -> None:
    from copy import deepcopy

    from isaacsim_validation.passive_linkage import validate_passive_linkage_manifest

    manifest = load_manifest(MANIFEST)

    fractional = deepcopy(manifest)
    fractional["parts"][0]["source_index"] = 26.5
    with pytest.raises(ValueError, match="source_index must be an integer"):
        validate_passive_linkage_manifest(fractional)

    boolean = deepcopy(manifest)
    boolean["parts"][0]["source_index"] = True
    with pytest.raises(ValueError, match="source_index must be an integer"):
        validate_passive_linkage_manifest(boolean)

    duplicate = deepcopy(manifest)
    duplicate["parts"][1]["source_index"] = duplicate["parts"][0]["source_index"]
    with pytest.raises(ValueError, match="Duplicate source_index"):
        validate_passive_linkage_manifest(duplicate)


def test_manifest_validation_rejects_bool_finger_values() -> None:
    from copy import deepcopy

    from isaacsim_validation.passive_linkage import validate_passive_linkage_manifest

    manifest = load_manifest(MANIFEST)
    tampered = deepcopy(manifest)
    tampered["parts"][0]["finger"] = True

    with pytest.raises(ValueError, match="finger must be an integer"):
        validate_passive_linkage_manifest(tampered)


def test_solver_rejects_bool_measured_joint_values() -> None:
    from isaacsim_validation.passive_linkage import solve_passive_linkage

    with pytest.raises(ValueError, match="Non-finite measured joint: finger1_motor1"):
        solve_passive_linkage({**OPEN_MEASURED, "finger1_motor1": True})


def test_manifest_validation_rejects_empty_or_nonmapping_raw_xml_geom_local() -> None:
    from copy import deepcopy

    from isaacsim_validation.passive_linkage import validate_passive_linkage_manifest

    manifest = load_manifest(MANIFEST)

    empty_raw = deepcopy(manifest)
    empty_raw["parts"][0]["raw_xml_geom_local"] = {}
    with pytest.raises(ValueError, match="raw_xml_geom_local"):
        validate_passive_linkage_manifest(empty_raw)

    nonmapping_raw = deepcopy(manifest)
    nonmapping_raw["parts"][0]["raw_xml_geom_local"] = "not-a-transform"
    with pytest.raises(ValueError, match="raw_xml_geom_local"):
        validate_passive_linkage_manifest(nonmapping_raw)


def test_slerp_uses_shortest_arc_for_negative_dot_quaternions() -> None:
    from isaacsim_validation.passive_linkage import _slerp

    result = _slerp((1.0, 0.0, 0.0, 0.0), (-0.7071067811865476, -0.7071067811865476, 0.0, 0.0), 0.5)

    assert result[0] > 0.9
    assert result[1] > 0.0
    assert abs(sum(value * value for value in result) - 1.0) < 1e-12


def test_slerp_normalizes_near_identical_quaternion_fallback() -> None:
    import math

    from isaacsim_validation.passive_linkage import _slerp

    result = _slerp((1.0, 0.0, 0.0, 0.0), (0.99999999, 0.0001, 0.0, 0.0), 0.5)

    assert math.isclose(sum(value * value for value in result), 1.0, abs_tol=1e-12)
    assert result[0] > 0.999999
    assert result[1] > 0.0
