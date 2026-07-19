"""Non-destructive alignment for the custom SuperArm and AmazingHand assets."""

from __future__ import annotations

import math
import struct
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from functools import cache
from pathlib import Path

ATTACHMENT_JOINT = "wrist_adapter_to_amazinghand"
ATTACHMENT_XYZ = "0 0 0.011753"
HAND_ROOT_LINK = "r_wrist_interface"
MOVING_FINGER_MESHES = {
    "proximal": {"proximal.stl", "proximal_shell.stl"},
    "distal": {"distal.stl", "distal_shell.stl"},
}

Vector3 = tuple[float, float, float]
Matrix3 = tuple[Vector3, Vector3, Vector3]
Pose = tuple[Vector3, Matrix3]


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
    return " ".join(f"{0.0 if abs(value) < 1e-12 else value:.9g}" for value in values)


def _origin_pose(origin: ET.Element | None) -> Pose:
    xyz = tuple(float(value) for value in (origin.get("xyz", "0 0 0") if origin is not None else "0 0 0").split())
    roll, pitch, yaw = (
        tuple(float(value) for value in origin.get("rpy", "0 0 0").split())
        if origin is not None
        else (0.0, 0.0, 0.0)
    )
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    rotation: Matrix3 = (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp, cp * sr, cp * cr),
    )
    return xyz, rotation


def _compose_pose(parent: Pose, child: Pose) -> Pose:
    parent_xyz, parent_rotation = parent
    child_xyz, child_rotation = child
    xyz = tuple(
        parent_xyz[row] + sum(parent_rotation[row][column] * child_xyz[column] for column in range(3))
        for row in range(3)
    )
    rotation: Matrix3 = tuple(
        tuple(
            sum(parent_rotation[row][index] * child_rotation[index][column] for index in range(3))
            for column in range(3)
        )
        for row in range(3)
    )
    return xyz, rotation


def _pose_rpy(rotation: Matrix3) -> Vector3:
    pitch = math.asin(max(-1.0, min(1.0, -rotation[2][0])))
    if abs(math.cos(pitch)) > 1e-9:
        roll = math.atan2(rotation[2][1], rotation[2][2])
        yaw = math.atan2(rotation[1][0], rotation[0][0])
    else:
        roll = math.atan2(-rotation[1][2], rotation[1][1])
        yaw = 0.0
    return roll, pitch, yaw


def _set_origin_pose(node: ET.Element, pose: Pose) -> None:
    origin = node.find("origin")
    if origin is None:
        origin = ET.Element("origin")
        node.insert(0, origin)
    xyz, rotation = pose
    origin.set("xyz", _format_vector(list(xyz)))
    origin.set("rpy", _format_vector(list(_pose_rpy(rotation))))


def _tree_aligned_segment_origin(mesh_path: Path) -> Vector3 | None:
    """Reuse the proven tree-local placement from the prior hand visual fix."""
    try:
        data = mesh_path.read_bytes()
    except OSError:
        return None
    if len(data) < 84:
        return None
    triangle_count = struct.unpack("<I", data[80:84])[0]
    if len(data) < 84 + triangle_count * 50:
        return None
    minimum = [math.inf, math.inf, math.inf]
    maximum = [-math.inf, -math.inf, -math.inf]
    offset = 84
    for _ in range(triangle_count):
        coordinates = struct.unpack("<12fH", data[offset : offset + 50])[3:12]
        for coordinate in range(0, 9, 3):
            for axis in range(3):
                value = coordinates[coordinate + axis]
                minimum[axis] = min(minimum[axis], value)
                maximum[axis] = max(maximum[axis], value)
        offset += 50
    center_x = (minimum[0] + maximum[0]) * 0.5
    center_z = (minimum[2] + maximum[2]) * 0.5
    return -center_x, -minimum[1], -center_z


def stabilize_amazinghand_visuals(root: ET.Element) -> int:
    """Keep only true segment shells on the simplified moving finger links.

    AmazingHand's passive closed-loop hardware cannot follow the showroom's
    two-link serial approximation. Reparent those visuals to the wrist at their
    zero-pose transforms so they remain assembled instead of tearing away.
    """
    links = {link.get("name"): link for link in root.findall(".//link") if link.get("name")}
    wrist = links.get(HAND_ROOT_LINK)
    if wrist is None:
        return 0

    parent_joints: dict[str, tuple[str, Pose]] = {}
    for joint in root.findall(".//joint"):
        parent = joint.find("parent")
        child = joint.find("child")
        if parent is None or child is None:
            continue
        parent_name = parent.get("link")
        child_name = child.get("link")
        if parent_name and child_name:
            parent_joints[child_name] = (parent_name, _origin_pose(joint.find("origin")))

    identity: Pose = ((0.0, 0.0, 0.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))

    @cache
    def wrist_to_link(link_name: str) -> Pose | None:
        if link_name == HAND_ROOT_LINK:
            return identity
        parent_joint = parent_joints.get(link_name)
        if parent_joint is None:
            return None
        parent_name, joint_pose = parent_joint
        parent_pose = wrist_to_link(parent_name)
        return None if parent_pose is None else _compose_pose(parent_pose, joint_pose)

    moved = 0
    for finger in range(1, 5):
        for segment, allowed_meshes in MOVING_FINGER_MESHES.items():
            link_name = f"finger{finger}_{segment}"
            link = links.get(link_name)
            link_pose = wrist_to_link(link_name)
            if link is None or link_pose is None:
                continue
            for visual in list(link.findall("visual")):
                mesh = visual.find("geometry/mesh")
                filename = mesh.get("filename") if mesh is not None else None
                if not filename:
                    continue
                mesh_path = Path(filename.removeprefix("file://"))
                if mesh_path.name in allowed_meshes:
                    aligned_origin = _tree_aligned_segment_origin(mesh_path)
                    if aligned_origin is not None:
                        _set_origin_pose(
                            visual,
                            (aligned_origin, identity[1]),
                        )
                    continue
                root_pose = _compose_pose(link_pose, _origin_pose(visual.find("origin")))
                link.remove(visual)
                _set_origin_pose(visual, root_pose)
                wrist.append(visual)
                moved += 1
    return moved


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
