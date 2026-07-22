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
    assert manifest["source_mjcf_sha256"] == "d21366e7c9a1f5debe04b8abb5ea1ade7fade42e493e09d003f5db196548b098"
    assert manifest["source_hand_zip_sha256"] == "3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377"
    assert manifest["source_package_zip_sha256"] == "c10c91ac240ac18893ab0a102e2ac6f9aa8a6a2e75c738fe6209f2d50a122b4a"
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
    assert all(set(part["transforms"].keys()) == {"open", "half_close", "close"} for part in manifest["parts"])
    assert all(
        len(transform["translate_m"]) == 3 and len(transform["orient_wxyz"]) == 4
        for part in manifest["parts"]
        for transform in part["transforms"].values()
    )
