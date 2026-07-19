"""Non-destructive alignment for the custom SuperArm and AmazingHand assets."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

ATTACHMENT_JOINT = "wrist_adapter_to_amazinghand"
ATTACHMENT_XYZ = "0 0 0.011753"
HAND_ROOT_LINK = "r_wrist_interface"
HAND_VISUAL_GROUP = 2
HAND_OPEN_TARGETS = {
    f"finger{finger}_motor{motor}": 0.05 if motor == 1 else -0.02
    for finger in range(1, 5)
    for motor in range(1, 3)
}


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


def remove_amazinghand_visuals(root: ET.Element) -> int:
    """Remove every visual below the hand anchor without altering its joints.

    The serial URDF approximation cannot express AmazingHand's closed loops.
    The browser renders the official MJCF bodies from streamed poses instead,
    so retaining any URDF hand visual would create duplicate geometry.
    """
    links = {link.get("name"): link for link in root.findall(".//link") if link.get("name")}
    if HAND_ROOT_LINK not in links:
        return 0
    children: dict[str, list[str]] = {}
    for joint in root.findall(".//joint"):
        parent = joint.find("parent")
        child = joint.find("child")
        if parent is None or child is None:
            continue
        parent_name = parent.get("link")
        child_name = child.get("link")
        if parent_name and child_name:
            children.setdefault(parent_name, []).append(child_name)

    subtree: set[str] = set()
    pending = [HAND_ROOT_LINK]
    while pending:
        link_name = pending.pop()
        if link_name in subtree:
            continue
        subtree.add(link_name)
        pending.extend(children.get(link_name, []))

    removed = 0
    for link_name in subtree:
        link = links.get(link_name)
        if link is None:
            continue
        for visual in list(link.findall("visual")):
            link.remove(visual)
            removed += 1
    return removed


def _normalize_quaternion(values: Any) -> list[float]:
    quaternion = [float(value) for value in values]
    norm = math.sqrt(sum(value * value for value in quaternion))
    if not math.isfinite(norm) or norm < 1e-12:
        raise ValueError("MuJoCo produced an invalid quaternion")
    normalized = [value / norm for value in quaternion]
    if normalized[0] < 0:
        normalized = [-value for value in normalized]
    return [0.0 if abs(value) < 1e-12 else value for value in normalized]


def _quaternion_multiply(left: list[float], right: list[float]) -> list[float]:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return [
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    ]


def _rotate_vector(quaternion: list[float], vector: Any) -> list[float]:
    pure = [0.0, *(float(value) for value in vector)]
    inverse = [quaternion[0], -quaternion[1], -quaternion[2], -quaternion[3]]
    return _quaternion_multiply(_quaternion_multiply(quaternion, pure), inverse)[1:]


def amazinghand_body_ids(model: Any) -> list[int]:
    """Return the hand root and every descendant in deterministic model order."""
    os.environ.setdefault("MUJOCO_GL", "egl")
    import mujoco

    root_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, HAND_ROOT_LINK)
    if root_id < 0:
        raise ValueError(f"MuJoCo model is missing hand root body {HAND_ROOT_LINK!r}")
    body_ids: list[int] = []
    for body_id in range(model.nbody):
        ancestor = body_id
        while ancestor and ancestor != root_id:
            ancestor = int(model.body_parentid[ancestor])
        if body_id == root_id or ancestor == root_id:
            body_ids.append(body_id)
    return body_ids


def amazinghand_visual_pose(model: Any, data: Any, body_ids: list[int] | None = None) -> dict[str, Any]:
    """Return all hand body poses in the wrist-root coordinate frame."""
    os.environ.setdefault("MUJOCO_GL", "egl")
    import mujoco

    ids = body_ids or amazinghand_body_ids(model)
    root_id = ids[0]
    root_position = [float(value) for value in data.xpos[root_id]]
    root_quaternion = _normalize_quaternion(data.xquat[root_id])
    root_inverse = [root_quaternion[0], *(-value for value in root_quaternion[1:])]
    bodies: dict[str, dict[str, list[float]]] = {}
    for body_id in ids:
        name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id)
        world_offset = [float(data.xpos[body_id][axis]) - root_position[axis] for axis in range(3)]
        position = _rotate_vector(root_inverse, world_offset)
        quaternion = _normalize_quaternion(
            _quaternion_multiply(root_inverse, _normalize_quaternion(data.xquat[body_id]))
        )
        bodies[name] = {
            "position_m": [0.0 if abs(value) < 1e-12 else value for value in position],
            "quaternion_wxyz": quaternion,
        }
    return bodies


def _mesh_sources(model_path: Path) -> dict[str, Path]:
    root = ET.parse(model_path).getroot()
    compiler = root.find("compiler")
    meshdir = Path(compiler.get("meshdir", "")) if compiler is not None else Path()
    sources: dict[str, Path] = {}
    for mesh in root.findall("./asset/mesh"):
        filename = mesh.get("file")
        if not filename:
            continue
        source = Path(filename)
        if not source.is_absolute():
            source = model_path.parent / meshdir / source
        name = mesh.get("name") or Path(filename).stem
        resolved = source.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"MuJoCo mesh asset is missing: {filename}")
        sources[name] = resolved
    return sources


def amazinghand_visual_assets(model_path: str | Path) -> dict[str, Path]:
    """Return the exact mesh files used by visual hand geoms, keyed by mesh name."""
    os.environ.setdefault("MUJOCO_GL", "egl")
    import mujoco

    path = Path(model_path).expanduser().resolve()
    model = mujoco.MjModel.from_xml_path(str(path))
    body_ids = set(amazinghand_body_ids(model))
    sources = _mesh_sources(path)
    assets: dict[str, Path] = {}
    for geom_id in range(model.ngeom):
        if (
            int(model.geom_bodyid[geom_id]) not in body_ids
            or int(model.geom_type[geom_id]) != int(mujoco.mjtGeom.mjGEOM_MESH)
            or int(model.geom_group[geom_id]) != HAND_VISUAL_GROUP
        ):
            continue
        mesh_id = int(model.geom_dataid[geom_id])
        mesh_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_MESH, mesh_id)
        if mesh_name not in sources:
            raise FileNotFoundError(f"MuJoCo mesh definition has no source file: {mesh_name}")
        assets[mesh_name] = sources[mesh_name]
    return assets


def build_amazinghand_visual_manifest(
    model_path: str | Path,
    *,
    asset_url_prefix: str,
) -> dict[str, Any]:
    """Compile the configured model into the browser's exact visual contract."""
    os.environ.setdefault("MUJOCO_GL", "egl")
    import mujoco

    path = Path(model_path).expanduser().resolve()
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    body_ids = amazinghand_body_ids(model)
    body_id_set = set(body_ids)
    assets = _mesh_sources(path)

    actuator_ids = {
        name: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in HAND_OPEN_TARGETS
    }
    if any(actuator_id < 0 for actuator_id in actuator_ids.values()):
        raise ValueError("MuJoCo model is missing an AmazingHand actuator")
    for name, actuator_id in actuator_ids.items():
        data.ctrl[actuator_id] = HAND_OPEN_TARGETS[name]
    for _ in range(max(1, int(1.0 / float(model.opt.timestep)))):
        mujoco.mj_step(model, data)

    visuals_by_body: dict[int, list[dict[str, Any]]] = {body_id: [] for body_id in body_ids}
    used_mesh_names: set[str] = set()
    visual_count = 0
    for geom_id in range(model.ngeom):
        body_id = int(model.geom_bodyid[geom_id])
        if (
            body_id not in body_id_set
            or int(model.geom_type[geom_id]) != int(mujoco.mjtGeom.mjGEOM_MESH)
            or int(model.geom_group[geom_id]) != HAND_VISUAL_GROUP
        ):
            continue
        mesh_id = int(model.geom_dataid[geom_id])
        mesh_name = mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_MESH, mesh_id)
        source = assets.get(mesh_name)
        if source is None:
            raise FileNotFoundError(f"MuJoCo mesh definition has no source file: {mesh_name}")
        used_mesh_names.add(mesh_name)
        geom_quaternion = _normalize_quaternion(model.geom_quat[geom_id])
        mesh_quaternion = _normalize_quaternion(model.mesh_quat[mesh_id])
        mesh_inverse = [mesh_quaternion[0], *(-value for value in mesh_quaternion[1:])]
        raw_mesh_quaternion = _normalize_quaternion(_quaternion_multiply(geom_quaternion, mesh_inverse))
        mesh_offset = _rotate_vector(
            raw_mesh_quaternion,
            [-float(value) for value in model.mesh_pos[mesh_id]],
        )
        visuals_by_body[body_id].append(
            {
                "mesh_url": f"{asset_url_prefix.rstrip('/')}/{quote(mesh_name, safe='')}.stl",
                "position_m": [float(model.geom_pos[geom_id][axis]) + mesh_offset[axis] for axis in range(3)],
                "quaternion_wxyz": raw_mesh_quaternion,
                "scale": [float(value) for value in model.mesh_scale[mesh_id]],
                "rgba": [float(value) for value in model.geom_rgba[geom_id]],
            }
        )
        visual_count += 1

    bodies = [
        {
            "name": mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body_id),
            "visuals": visuals_by_body[body_id],
        }
        for body_id in body_ids
    ]
    return {
        "schema_version": 1,
        "root_link": HAND_ROOT_LINK,
        "coordinate_frame": "root-relative, meters, quaternion-wxyz",
        "source_model_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "counts": {
            "bodies": len(body_ids),
            "visuals": visual_count,
            "mesh_definitions": len(used_mesh_names),
            "equalities": int(model.neq),
        },
        "bodies": bodies,
        "hand_joint_names": list(HAND_OPEN_TARGETS),
        "default_pose": {
            "timestamp": 0.0,
            "bodies": amazinghand_visual_pose(model, data, body_ids),
        },
    }


def align_joint5_mjcf(root: ET.Element) -> bool:
    """Move the joint-5 pivot to motor 5 without changing its zero pose."""
    moving = root.find(".//body[@name='arm_link3b']")
    if moving is None:
        return False
    parent = next(
        (body for body in root.findall(".//body") if moving in body.findall("body")),
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
                _format_vector([value + offset for value, offset in zip(position, delta, strict=True)]),
            )

    parent.remove(motor_geom)
    motor_position = _parse_vector(motor_geom)
    motor_geom.set(
        "pos",
        _format_vector([value - offset for value, offset in zip(motor_position, new_body_pos, strict=True)]),
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
