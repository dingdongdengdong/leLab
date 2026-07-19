from __future__ import annotations

import math
from pathlib import Path

import pytest

from lelab.superarm.programs import ProgramStore
from lelab.superarm.service import SuperArmService
from lelab.superarm.showroom import build_amazinghand_visual_manifest


@pytest.fixture
def official_model_path(tmp_path: Path) -> Path:
    service = SuperArmService(ProgramStore(tmp_path / "programs.yaml"))
    return service.model_path()


def test_official_amazinghand_manifest_has_exact_compiled_counts(
    official_model_path: Path,
) -> None:
    manifest = build_amazinghand_visual_manifest(
        official_model_path,
        asset_url_prefix="/api/superarm/mujoco-visual-assets",
    )

    assert manifest["root_link"] == "r_wrist_interface"
    assert manifest["coordinate_frame"] == "root-relative, meters, quaternion-wxyz"
    assert manifest["counts"] == {
        "bodies": 33,
        "visuals": 162,
        "mesh_definitions": 23,
        "equalities": 20,
    }
    assert len(manifest["bodies"]) == 33
    assert sum(len(body["visuals"]) for body in manifest["bodies"]) == 162
    assert not any(
        primitive in visual
        for body in manifest["bodies"]
        for visual in body["visuals"]
        for primitive in ("box", "cylinder", "sphere")
    )
    mesh_urls = {
        visual["mesh_url"]
        for body in manifest["bodies"]
        for visual in body["visuals"]
    }
    assert len(mesh_urls) == 23
    assert all(url.startswith("/api/superarm/mujoco-visual-assets/") for url in mesh_urls)
    assert all(
        math.isclose(
            sum(value * value for value in visual["quaternion_wxyz"]),
            1.0,
            abs_tol=1e-9,
        )
        for body in manifest["bodies"]
        for visual in body["visuals"]
    )

    default_bodies = manifest["default_pose"]["bodies"]
    assert len(default_bodies) == 33
    assert default_bodies["r_wrist_interface"] == {
        "position_m": [0.0, 0.0, 0.0],
        "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
    }
    assert all(
        math.isclose(
            sum(value * value for value in pose["quaternion_wxyz"]),
            1.0,
            abs_tol=1e-9,
        )
        for pose in default_bodies.values()
    )


def test_visual_asset_lookup_rejects_traversal(
    tmp_path: Path,
    official_model_path: Path,
) -> None:
    service = SuperArmService(ProgramStore(tmp_path / "programs.yaml"))

    with pytest.raises(ValueError, match="Invalid AmazingHand visual asset"):
        service.amazinghand_visual_asset_path("../proximal.stl", model_path=official_model_path)
    with pytest.raises(FileNotFoundError, match="visual asset is missing"):
        service.amazinghand_visual_asset_path("arm_link1", model_path=official_model_path)

    asset = service.amazinghand_visual_asset_path(
        "proximal_shell",
        model_path=official_model_path,
    )
    assert asset.name == "proximal_shell.stl"
    assert asset.is_file()
