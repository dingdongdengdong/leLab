from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np
import pytest

from lelab.superarm.programs import ProgramStore
from lelab.superarm.service import SuperArmService
from lelab.superarm.showroom import build_amazinghand_visual_manifest


def _stl_vertices(path: Path) -> np.ndarray:
    payload = path.read_bytes()
    triangle_count = struct.unpack("<I", payload[80:84])[0]
    if len(payload) != 84 + triangle_count * 50:
        raise AssertionError(f"Expected binary STL fixture: {path}")
    vertices = np.empty((triangle_count * 3, 3), dtype=np.float64)
    for triangle in range(triangle_count):
        offset = 84 + triangle * 50 + 12
        vertices[triangle * 3 : triangle * 3 + 3] = np.asarray(
            struct.unpack("<9f", payload[offset : offset + 36]),
        ).reshape(3, 3)
    return np.unique(vertices, axis=0)


def _rotation_matrix(quaternion: list[float] | np.ndarray) -> np.ndarray:
    import mujoco

    values = np.empty(9, dtype=np.float64)
    mujoco.mju_quat2Mat(values, np.asarray(quaternion, dtype=np.float64))
    return values.reshape(3, 3)


def _sort_vertices(vertices: np.ndarray) -> np.ndarray:
    rounded = np.round(vertices, decimals=7)
    return rounded[np.lexsort((rounded[:, 2], rounded[:, 1], rounded[:, 0]))]


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
    mesh_urls = {visual["mesh_url"] for body in manifest["bodies"] for visual in body["visuals"]}
    assert len(mesh_urls) == 23
    assert all(
        url.startswith("/api/superarm/mujoco-visual-assets/") and url.endswith(".stl") for url in mesh_urls
    )
    assert manifest["hand_joint_names"] == [
        f"finger{finger}_motor{motor}" for finger in range(1, 5) for motor in range(1, 3)
    ]
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
        "proximal_shell.stl",
        model_path=official_model_path,
    )
    assert asset.name == "proximal_shell.stl"
    assert asset.is_file()


def test_manifest_reconstructs_raw_stl_at_compiled_mujoco_pose(
    tmp_path: Path,
    official_model_path: Path,
) -> None:
    """Lock the tiny mesh-centering offsets that caused the prior XYZ mismatch."""
    import mujoco

    service = SuperArmService(ProgramStore(tmp_path / "programs.yaml"))
    manifest = build_amazinghand_visual_manifest(
        official_model_path,
        asset_url_prefix="/api/superarm/mujoco-visual-assets",
    )
    bodies = {body["name"]: body for body in manifest["bodies"]}
    model = mujoco.MjModel.from_xml_path(str(official_model_path))
    per_body_index: dict[int, int] = {}

    selected = None
    for geom_id in range(model.ngeom):
        if (
            int(model.geom_type[geom_id]) != int(mujoco.mjtGeom.mjGEOM_MESH)
            or int(model.geom_group[geom_id]) != 2
        ):
            continue
        mesh_id = int(model.geom_dataid[geom_id])
        mesh_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_MESH, mesh_id)
        body_id = int(model.geom_bodyid[geom_id])
        body_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
        if body_name not in bodies:
            continue
        visual_index = per_body_index.get(body_id, 0)
        per_body_index[body_id] = visual_index + 1
        if mesh_name == "proximal_shell":
            selected = (geom_id, mesh_id, bodies[body_name]["visuals"][visual_index])
            break

    assert selected is not None
    geom_id, mesh_id, visual = selected
    raw_vertices = _stl_vertices(
        service.amazinghand_visual_asset_path(
            "proximal_shell.stl",
            model_path=official_model_path,
        )
    )
    browser_vertices = (raw_vertices * np.asarray(visual["scale"])) @ _rotation_matrix(
        visual["quaternion_wxyz"]
    ).T + np.asarray(visual["position_m"])

    vertex_address = int(model.mesh_vertadr[mesh_id])
    vertex_count = int(model.mesh_vertnum[mesh_id])
    compiled_vertices = np.asarray(
        model.mesh_vert[vertex_address : vertex_address + vertex_count],
        dtype=np.float64,
    )
    mujoco_vertices = compiled_vertices @ _rotation_matrix(model.geom_quat[geom_id]).T + np.asarray(
        model.geom_pos[geom_id]
    )

    np.testing.assert_allclose(
        _sort_vertices(browser_vertices),
        _sort_vertices(mujoco_vertices),
        atol=2e-6,
        rtol=0.0,
    )
