"""Non-destructive alignment for the custom SuperArm and AmazingHand assets."""

from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

ATTACHMENT_JOINT = "wrist_adapter_to_amazinghand"
ATTACHMENT_XYZ = "0 0 0.011753"


def _joint_between(root: ET.Element, parent: str, child: str) -> ET.Element | None:
    for joint in root.findall(".//joint"):
        parent_node = joint.find("parent")
        child_node = joint.find("child")
        if (
            parent_node is not None
            and child_node is not None
            and parent_node.get("link") == parent
            and child_node.get("link") == child
        ):
            return joint
    return None


def align_joint5_urdf(root: ET.Element) -> bool:
    """Rotate joint 5 at the motor boundary while keeping its shell fixed."""
    motor_joint = _joint_between(root, "arm_link2b", "motor_5")
    shell_mount = _joint_between(root, "motor_5", "arm_link3b")
    if motor_joint is None or shell_mount is None:
        return False
    if motor_joint.get("name") == "joint_rev_5" and shell_mount.get("type") == "fixed":
        return False

    motor_joint.set("name", "joint_rev_5")
    motor_joint.set("type", "continuous")
    axis = motor_joint.find("axis")
    if axis is None:
        axis = ET.SubElement(motor_joint, "axis")
    axis.set("xyz", "0 0 -1")

    shell_mount.set("name", "joint_fix_28")
    shell_mount.set("type", "fixed")
    shell_axis = shell_mount.find("axis")
    if shell_axis is not None:
        shell_mount.remove(shell_axis)
    return True


def _parse_vector(node: ET.Element, attribute: str = "pos") -> list[float]:
    return [float(value) for value in node.get(attribute, "0 0 0").split()]


def _format_vector(values: list[float]) -> str:
    return " ".join(f"{value:.9g}" for value in values)


def align_joint5_mjcf(root: ET.Element) -> bool:
    """Move the joint-5 pivot to motor 5 without changing its zero pose."""
    moving = root.find(".//body[@name='arm_link3b']")
    if moving is None:
        return False
    parent = next(
        (
            body
            for body in root.findall(".//body")
            if moving in body.findall("body")
        ),
        None,
    )
    joint = moving.find("joint[@name='joint_rev_5']")
    if parent is None or joint is None:
        return False
    motor_geom = parent.find("geom[@mesh='motor_5']")
    if motor_geom is None:
        return False
    if moving.get("quat") not in {None, "1 0 0 0"}:
        raise ValueError("Joint 5 alignment requires an unrotated arm_link3b body frame")

    old_body_pos = _parse_vector(moving)
    new_body_pos = [0.02, 0.0, 0.05]
    delta = [old - new for old, new in zip(old_body_pos, new_body_pos, strict=True)]
    moving.set("pos", _format_vector(new_body_pos))
    joint.set("axis", "0 0 -1")

    for tag in ("inertial", "geom", "site", "camera", "body"):
        for node in moving.findall(tag):
            position = _parse_vector(node)
            node.set(
                "pos",
                _format_vector(
                    [value + offset for value, offset in zip(position, delta, strict=True)]
                ),
            )

    parent.remove(motor_geom)
    motor_position = _parse_vector(motor_geom)
    motor_geom.set(
        "pos",
        _format_vector(
            [value - offset for value, offset in zip(motor_position, new_body_pos, strict=True)]
        ),
    )
    moving.insert(1, motor_geom)
    return True


@contextmanager
def aligned_mujoco_model_path(model_path: str | Path) -> Iterator[Path]:
    """Materialize the corrected MJCF beside its relative mesh assets."""
    source = Path(model_path)
    tree = ET.parse(source)
    if not align_joint5_mjcf(tree.getroot()):
        yield source
        return

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=source.parent,
            prefix=".lelab-joint5-",
            suffix=".xml",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            tree.write(temporary, encoding="utf-8", xml_declaration=True)
        yield temporary_path
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def align_amazinghand_attachment(root: ET.Element) -> bool:
    """Match the URDF hand mount to the attached transform used by MuJoCo."""
    for joint in root.findall(".//joint"):
        if joint.get("name") != ATTACHMENT_JOINT:
            continue
        origin = joint.find("origin")
        if origin is None:
            origin = ET.SubElement(joint, "origin")
        origin.set("xyz", ATTACHMENT_XYZ)
        return True
    return False
