from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from isaacsim_validation.prepare_superarm_urdf import (
    EXPECTED_ARM_JOINTS,
    EXPECTED_HAND_JOINTS,
    prepare_package,
)


def _write_fixture(root: Path, *, missing_mesh: bool = False) -> Path:
    mesh = root / "source" / "meshes" / "part.stl"
    mesh.parent.mkdir(parents=True)
    if not missing_mesh:
        mesh.write_bytes(b"solid part\nendsolid part\n")

    links = ["base", *(f"link_{index}" for index in range(13))]
    robot = ET.Element("robot", {"name": "superarm_amazinghand"})
    for link_name in links:
        link = ET.SubElement(robot, "link", {"name": link_name})
        visual = ET.SubElement(link, "visual")
        geometry = ET.SubElement(visual, "geometry")
        ET.SubElement(geometry, "mesh", {"filename": "meshes/part.stl"})
    for index, joint_name in enumerate((*EXPECTED_ARM_JOINTS, *EXPECTED_HAND_JOINTS)):
        joint = ET.SubElement(robot, "joint", {"name": joint_name, "type": "revolute"})
        ET.SubElement(joint, "parent", {"link": links[index]})
        ET.SubElement(joint, "child", {"link": links[index + 1]})

    urdf = root / "source" / "superarm_amazinghand.urdf"
    ET.ElementTree(robot).write(urdf, encoding="utf-8", xml_declaration=True)
    return urdf


def test_prepare_package_copies_unique_mesh_and_records_contract(tmp_path: Path):
    source = _write_fixture(tmp_path)
    output = tmp_path / "output"

    manifest = prepare_package(source, output, source.parent)

    assert manifest["robot"] == "superarm_amazinghand"
    assert manifest["link_count"] == 14
    assert manifest["joint_count"] == 13
    assert manifest["arm_joints"] == list(EXPECTED_ARM_JOINTS)
    assert manifest["hand_joints"] == list(EXPECTED_HAND_JOINTS)
    assert manifest["mesh_reference_count"] == 14
    assert manifest["unique_mesh_count"] == 1
    packaged = ET.parse(output / "superarm_amazinghand.urdf").getroot()
    assert {mesh.get("filename") for mesh in packaged.findall(".//mesh")} == {
        "meshes/000_part.stl"
    }


def test_prepare_package_rejects_missing_mesh(tmp_path: Path):
    source = _write_fixture(tmp_path, missing_mesh=True)

    with pytest.raises(FileNotFoundError, match="referenced mesh does not exist"):
        prepare_package(source, tmp_path / "output", source.parent)


def test_prepare_package_rejects_mesh_outside_source_root(tmp_path: Path):
    source = _write_fixture(tmp_path)
    tree = ET.parse(source)
    external = tmp_path / "external.stl"
    external.write_bytes(b"solid external\nendsolid external\n")
    tree.getroot().find(".//mesh").set("filename", str(external))
    tree.write(source, encoding="utf-8", xml_declaration=True)

    with pytest.raises(ValueError, match="mesh escapes allowed source root"):
        prepare_package(source, tmp_path / "output", source.parent)


def test_prepare_package_rejects_unknown_profile(tmp_path: Path):
    source = _write_fixture(tmp_path)

    with pytest.raises(ValueError, match="profile must be"):
        prepare_package(source, tmp_path / "output", source.parent, profile="hybrid")
