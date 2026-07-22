from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from isaacsim_validation.prepare_amazinghand_usd import (
    AMAZINGHAND_USD_ENTRY,
    prepare_amazinghand_usd,
)


def _write_distribution(path: Path, *, unsafe: bool = False) -> str:
    root = "amazinghand_isaac_sim_usd_distribution_20260722"
    files = {
        f"{root}/manifest.json": json.dumps(
            {
                "simready_articulation_binding": "binding_pending",
                "joint_names": [f"finger{finger}_motor{motor}" for finger in range(1, 5) for motor in range(1, 3)],
            }
        ),
        f"{root}/usd/amazinghand_graspable/amazinghand_graspable.usda": "#usda 1.0\n",
        f"{root}/usd/amazinghand_graspable/payloads/base.usda": "#usda 1.0\n",
        f"{root}/usd/amazinghand_graspable/payloads/robot.usda": "#usda 1.0\n",
        f"{root}/usd/amazinghand_graspable/payloads/Physics/physics.usda": "#usda 1.0\n",
        f"{root}/usd/amazinghand_graspable/payloads/Physics/physx.usda": "#usda 1.0\n",
        f"{root}/usd/amazinghand_graspable/payloads/geometries.usd": "usd",
        f"{root}/usd/amazinghand_graspable/payloads/instances.usda": "#usda 1.0\n",
        f"{root}/usd/amazinghand_graspable/preview_hand.usda": "#usda 1.0\n",
    }
    if unsafe:
        files["../escape.txt"] = "unsafe"
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in files.items():
            archive.writestr(name, body)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_prepare_uses_zip_usd_as_authoritative_hand_source(tmp_path: Path):
    archive = tmp_path / "hand.zip"
    digest = _write_distribution(archive)

    manifest = prepare_amazinghand_usd(archive, tmp_path / "prepared", expected_sha256=digest)

    entry = tmp_path / "prepared" / AMAZINGHAND_USD_ENTRY
    assert entry.is_file()
    assert manifest["source_kind"] == "isaac_sim_usd_distribution"
    assert manifest["source_zip_sha256"] == digest
    assert manifest["source_binding_status"] == "binding_pending"
    assert manifest["hand_joint_names"] == [
        f"finger{finger}_motor{motor}" for finger in range(1, 5) for motor in range(1, 3)
    ]
    assert not (entry.parent / "preview_hand.usda").exists()


def test_prepare_rejects_wrong_distribution_checksum(tmp_path: Path):
    archive = tmp_path / "hand.zip"
    _write_distribution(archive)

    with pytest.raises(ValueError, match="checksum"):
        prepare_amazinghand_usd(archive, tmp_path / "prepared", expected_sha256="0" * 64)


def test_prepare_rejects_unsafe_archive_paths(tmp_path: Path):
    archive = tmp_path / "hand.zip"
    digest = _write_distribution(archive, unsafe=True)

    with pytest.raises(ValueError, match="unsafe ZIP member"):
        prepare_amazinghand_usd(archive, tmp_path / "prepared", expected_sha256=digest)
