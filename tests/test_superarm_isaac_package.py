from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from isaacsim_validation.prepare_superarm_urdf import (
    EXPECTED_ARM_JOINTS,
    EXPECTED_HAND_JOINTS,
    prepare_package,
    retain_learning_hand_visuals,
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
    assert {mesh.get("filename") for mesh in packaged.findall(".//mesh")} == {"meshes/000_part.stl"}


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


def test_zip_learning_profile_removes_hand_visuals_but_keeps_joint_chain(tmp_path: Path):
    source = _write_fixture(tmp_path)
    tree = ET.parse(source)
    robot = tree.getroot()
    hand_anchor = robot.findall("link")[6]
    old_name = hand_anchor.get("name")
    hand_anchor.set("name", "r_wrist_interface")
    for endpoint in robot.findall(".//parent") + robot.findall(".//child"):
        if endpoint.get("link") == old_name:
            endpoint.set("link", "r_wrist_interface")
    tree.write(source, encoding="utf-8", xml_declaration=True)

    output = tmp_path / "zip-learning"
    manifest = prepare_package(source, output, source.parent, profile="zip_learning")
    packaged = ET.parse(output / "superarm_amazinghand.urdf").getroot()

    assert manifest["profile"] == "zip_learning"
    assert manifest["movable_joints"] == [*EXPECTED_ARM_JOINTS, *EXPECTED_HAND_JOINTS]
    assert packaged.find("./link[@name='r_wrist_interface']/visual") is None
    assert manifest["mesh_reference_count"] < 14
    assert manifest["mesh_reference_count"] > 0


def test_aligned_profile_preserves_hand_visuals_and_rewrites_attachment(tmp_path: Path):
    source = _write_fixture(tmp_path)
    tree = ET.parse(source)
    robot = tree.getroot()
    attachment = ET.SubElement(robot, "joint", {"name": "wrist_adapter_to_amazinghand", "type": "fixed"})
    ET.SubElement(attachment, "parent", {"link": "link_12"})
    ET.SubElement(attachment, "child", {"link": "link_13"})
    ET.SubElement(attachment, "origin", {"xyz": "0 0 0.6", "rpy": "0 0 0"})
    tree.write(source, encoding="utf-8", xml_declaration=True)

    output = tmp_path / "aligned"
    manifest = prepare_package(source, output, source.parent, profile="aligned")
    packaged = ET.parse(output / "superarm_amazinghand.urdf").getroot()
    rewritten = next(
        joint for joint in packaged.findall("joint") if joint.get("name") == "wrist_adapter_to_amazinghand"
    )

    assert rewritten.find("origin").get("xyz") == "0 0 0.011753"
    assert manifest["mesh_reference_count"] == 14
    assert len(packaged.findall(".//visual")) == 14


def test_learning_hand_visuals_keep_shells_but_remove_unbound_linkages():
    robot = ET.Element("robot", {"name": "superarm_amazinghand"})

    def add_link(name: str, meshes: list[str]) -> None:
        link = ET.SubElement(robot, "link", {"name": name})
        for filename in meshes:
            visual = ET.SubElement(link, "visual")
            ET.SubElement(visual, "origin", {"xyz": "9 9 9", "rpy": "1 2 3"})
            geometry = ET.SubElement(visual, "geometry")
            ET.SubElement(geometry, "mesh", {"filename": filename})

    add_link("r_wrist_interface", ["r_hand_plate.stl", "finger_frame_1.stl"])
    add_link(
        "finger1_proximal",
        ["proximal_shell.stl", "proximal.stl", "rotule_lever.stl"],
    )
    add_link("finger1_distal", ["distal_shell.stl", "distal.stl", "parallel_pin.stl"])
    add_link("arm_link1", ["arm_link1.stl"])

    removed = retain_learning_hand_visuals(robot)

    assert removed == 2
    remaining = {
        link.get("name"): [
            Path(visual.find("geometry/mesh").get("filename")).name for visual in link.findall("visual")
        ]
        for link in robot.findall("link")
    }
    assert remaining["r_wrist_interface"] == ["r_hand_plate.stl", "finger_frame_1.stl"]
    assert remaining["finger1_proximal"] == ["proximal_shell.stl", "proximal.stl"]
    assert remaining["finger1_distal"] == ["distal_shell.stl", "distal.stl"]
    assert remaining["arm_link1"] == ["arm_link1.stl"]

    proximal_origins = [
        visual.find("origin").attrib
        for visual in robot.find("./link[@name='finger1_proximal']").findall("visual")
    ]
    distal_origins = [
        visual.find("origin").attrib
        for visual in robot.find("./link[@name='finger1_distal']").findall("visual")
    ]
    assert proximal_origins == [{"xyz": "0 0 0", "rpy": "0 0 0"}] * 2
    assert distal_origins == [{"xyz": "0 -0.058 0", "rpy": "0 0 0"}] * 2
