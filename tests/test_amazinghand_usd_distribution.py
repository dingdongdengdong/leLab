from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from isaacsim_validation.prepare_amazinghand_usd import (
    AMAZINGHAND_LEARNING_ENTRY,
    AMAZINGHAND_USD_ENTRY,
    bind_learning_visuals,
    prepare_amazinghand_usd,
    repair_hand_only_binding,
)


def _write_distribution(path: Path, *, unsafe: bool = False) -> str:
    root = "amazinghand_isaac_sim_usd_distribution_20260722"
    static_parts = "\n".join(
        f'def Xform "mjcf_{index:03d}_static_part_{index}" {{ }}' for index in range(26)
    )
    drive_blocks = "\n".join(
        f'''        def PhysicsRevoluteJoint "finger{finger}_motor{motor}"
        {{
            float drive:angular:physics:damping = 0.08
        }}'''
        for finger in range(1, 5)
        for motor in range(1, 3)
    )
    files = {
        f"{root}/manifest.json": json.dumps(
            {
                "simready_articulation_binding": "binding_pending",
                "joint_names": [f"finger{finger}_motor{motor}" for finger in range(1, 5) for motor in range(1, 3)],
            }
        ),
        f"{root}/usd/amazinghand_graspable/amazinghand_graspable.usda": "#usda 1.0\n",
        f"{root}/usd/amazinghand_graspable/payloads/base.usda": f"#usda 1.0\n{static_parts}\n",
        f"{root}/usd/amazinghand_graspable/payloads/robot.usda": '''#usda 1.0
over "amazinghand_graspable"
{
    prepend rel isaac:physics:robotJoints = [
        </amazinghand_graspable/Physics/wrist_to_amazinghand_visual_shell>,
    ]
    prepend rel isaac:physics:robotLinks = [
        </amazinghand_graspable/Geometry/r_wrist_interface/amazinghand_visual_shell>,
    ]
    over "Geometry"
    {
        over "r_wrist_interface"
        {
            over "amazinghand_visual_shell" { }
        }
    }
    over "Physics"
    {
        over "wrist_to_amazinghand_visual_shell" { }
    }
}
''',
        f"{root}/usd/amazinghand_graspable/payloads/Physics/physics.usda": '''#usda 1.0
over "amazinghand_graspable"
{
    over "Geometry"
    {
        over "r_wrist_interface"
        {
            over "amazinghand_visual_shell" { }
        }
    }
    over "Physics"
    {
        def PhysicsFixedJoint "wrist_to_amazinghand_visual_shell" { }
DRIVE_BLOCKS
    }
}
'''.replace("DRIVE_BLOCKS", drive_blocks),
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
    assert manifest["entry_stage"] == AMAZINGHAND_LEARNING_ENTRY.as_posix()
    assert manifest["hand_joint_names"] == [
        f"finger{finger}_motor{motor}" for finger in range(1, 5) for motor in range(1, 3)
    ]
    assert not (entry.parent / "preview_hand.usda").exists()
    assert (tmp_path / "prepared" / AMAZINGHAND_LEARNING_ENTRY).is_file()


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


def test_repair_removes_static_visual_shell_from_hand_physics(tmp_path: Path):
    payloads = tmp_path / "usd" / "amazinghand_graspable" / "payloads"
    physics = payloads / "Physics" / "physics.usda"
    robot = payloads / "robot.usda"
    physics.parent.mkdir(parents=True)
    drive_blocks = "\n".join(
        f'''        def PhysicsRevoluteJoint "finger{finger}_motor{motor}"
        {{
            float drive:angular:physics:damping = 0.08
        }}'''
        for finger in range(1, 5)
        for motor in range(1, 3)
    )
    physics.write_text(
        '''#usda 1.0
over "amazinghand_graspable"
{
    over "Geometry"
    {
        over "r_wrist_interface"
        {
            over "amazinghand_visual_shell" (
                prepend apiSchemas = ["PhysicsRigidBodyAPI", "PhysicsMassAPI"]
            )
            {
                float physics:mass = 0.001
            }
        }
    }
    over "Physics"
    {
        def PhysicsFixedJoint "wrist_to_amazinghand_visual_shell"
        {
            rel physics:body1 = </amazinghand_graspable/Geometry/r_wrist_interface/amazinghand_visual_shell>
        }
DRIVE_BLOCKS
    }
}
'''.replace("DRIVE_BLOCKS", drive_blocks),
        encoding="utf-8",
    )
    robot.write_text(
        '''#usda 1.0
over "amazinghand_graspable"
{
    prepend rel isaac:physics:robotJoints = [
        </amazinghand_graspable/Physics/wrist_to_amazinghand_visual_shell>,
    ]
    prepend rel isaac:physics:robotLinks = [
        </amazinghand_graspable/Geometry/r_wrist_interface/amazinghand_visual_shell>,
    ]
    over "Geometry"
    {
        over "r_wrist_interface"
        {
            over "amazinghand_visual_shell" (
                prepend apiSchemas = ["IsaacLinkAPI"]
            )
            {
            }
        }
    }
    over "Physics"
    {
        over "wrist_to_amazinghand_visual_shell" (
            prepend apiSchemas = ["IsaacJointAPI"]
        )
        {
        }
    }
}
''',
        encoding="utf-8",
    )

    repair = repair_hand_only_binding(tmp_path)

    assert repair == {
        "authored_position_drive_stiffness": True,
        "removed_visual_shell_rigid_body": True,
        "removed_visual_shell_fixed_joint": True,
        "removed_visual_shell_robot_link": True,
        "removed_visual_shell_robot_joint": True,
    }
    assert "amazinghand_visual_shell" not in physics.read_text(encoding="utf-8")
    assert physics.read_text(encoding="utf-8").count("physics:stiffness = 3.1415927") == 8
    assert "amazinghand_visual_shell" not in robot.read_text(encoding="utf-8")


def test_learning_stage_uses_zip_geometry_on_moving_finger_links(tmp_path: Path):
    asset = tmp_path / "usd" / "amazinghand_graspable"
    payloads = asset / "payloads"
    payloads.mkdir(parents=True)
    (asset / "amazinghand_graspable.usda").write_text("#usda 1.0\n", encoding="utf-8")
    static_parts = "\n".join(
        f'''def Xform "mjcf_{index:03d}_static_part_{index}" (
    prepend references = @./instances.usda@</Instances/mjcf_{index:03d}_static_part_{index}>
)
{{
    double3 xformOp:translate = ({index}, 0, 0)
}}
'''
        for index in range(26)
    )
    (payloads / "base.usda").write_text(f"#usda 1.0\n{static_parts}", encoding="utf-8")

    result = bind_learning_visuals(tmp_path)

    learning = (tmp_path / AMAZINGHAND_LEARNING_ENTRY).read_text(encoding="utf-8")
    overlay = (payloads / "learning_visuals.usda").read_text(encoding="utf-8")
    assert result["static_visual_part_count"] == 26
    assert result["moving_visual_part_count"] == 16
    assert "amazinghand_visual_shell" in overlay and "active = false" in overlay
    assert overlay.count('def Xform "zip_proximal_') == 8
    assert overlay.count('def Xform "zip_distal_') == 8
    assert "double3 xformOp:translate = (0, -0.058, 0)" in overlay
    assert "learning_visuals.usda" in learning
    assert "amazinghand_graspable.usda" in learning
