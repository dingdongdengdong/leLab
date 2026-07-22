from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

import mujoco
import numpy as np

MANIFEST_VERSION = 1
SOURCE_MJCF_SHA256 = "d21366e7c9a1f5debe04b8abb5ea1ade7fade42e493e09d003f5db196548b098"
SOURCE_PACKAGE_ZIP_SHA256 = "c10c91ac240ac18893ab0a102e2ac6f9aa8a6a2e75c738fe6209f2d50a122b4a"
SOURCE_HAND_ZIP_SHA256 = "3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377"
FINGER_BLOCKS = {1: range(26, 60), 2: range(60, 94), 3: range(94, 128), 4: range(128, 162)}
EXCLUDED_SHELL_INDICES = frozenset({45, 51, 78, 85, 114, 115, 144, 152})
SOURCE_CORE_INDICES = frozenset({44, 52, 76, 84, 113, 117, 147, 153})
KEYFRAMES = {
    "open": {"motor1": 0.05, "motor2": 0.02},
    "half_close": {"motor1": 0.50, "motor2": 0.56},
    "close": {"motor1": 0.95, "motor2": 1.10},
}
MOTOR_NAMES = tuple(f"finger{finger}_motor{motor}" for finger in range(1, 5) for motor in range(1, 3))
STEP_COUNT = 5000
TIMESTEP_S = 0.002
MAX_EQUALITY_SITE_PAIR_SEPARATION_M = 1e-6
MAX_MOTOR_TARGET_ERROR_RAD = 1e-4
PACKAGE_ROBOT_XML_ENTRY = "robot_arm_hand_package/hand_mjcf/robot.xml"
PACKAGE_HAND_MJCF_PREFIX = "robot_arm_hand_package/hand_mjcf/"
DISTRIBUTION_BASE_USDA_SUFFIX = "/usd/amazinghand_graspable/payloads/base.usda"


@dataclass(frozen=True)
class SourcePrim:
    name: str
    instance_prim: str
    translate_m: tuple[float, float, float]
    orient_wxyz: tuple[float, float, float, float]


@dataclass(frozen=True)
class VisualGeom:
    source_index: int
    body_name: str
    body_chain: tuple[str, ...]
    mesh_name: str
    local_translate_m: tuple[float, float, float]
    local_orient_wxyz: tuple[float, float, float, float]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_numbers(raw: str) -> tuple[float, ...]:
    return tuple(float(value.strip()) for value in raw.split("," if "," in raw else " ") if value.strip())


def _parse_xml_numbers(raw: str | None, expected_len: int, default: tuple[float, ...]) -> tuple[float, ...]:
    if raw is None:
        return default
    values = tuple(float(value) for value in raw.split())
    if len(values) != expected_len:
        raise ValueError(f"Expected {expected_len} numbers, got {len(values)} in {raw!r}")
    return values


def _read_zip_member(archive: zipfile.ZipFile, *, exact: str | None = None, suffix: str | None = None) -> bytes:
    if exact is not None:
        return archive.read(exact)
    matches = [name for name in archive.namelist() if suffix is not None and name.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one ZIP member ending in {suffix!r}, found {matches}")
    return archive.read(matches[0])


def _safe_extract_prefix(archive: zipfile.ZipFile, prefix: str, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.infolist():
        if not member.filename.startswith(prefix):
            continue
        target = (destination / member.filename).resolve()
        if destination not in target.parents and target != destination:
            raise ValueError(f"unsafe ZIP member: {member.filename}")
        archive.extract(member, destination)


def parse_base_usda(text: str) -> dict[int, SourcePrim]:
    pattern = re.compile(
        r'def Xform "(?P<name>mjcf_(?P<index>\d{3})_[^"]+)"\s*\(\s*'
        r'instanceable = true\s*'
        r'prepend references = @\./instances\.usda@<(?P<instance>[^>]+)>\s*'
        r'\)\s*\{\s*'
        r'quatd xformOp:orient = \((?P<orient>[^)]*)\)\s*'
        r'double3 xformOp:scale = \([^)]*\)\s*'
        r'double3 xformOp:translate = \((?P<translate>[^)]*)\)',
        re.MULTILINE,
    )
    sources: dict[int, SourcePrim] = {}
    for match in pattern.finditer(text):
        index = int(match.group("index"))
        translate = _parse_numbers(match.group("translate"))
        orient = _parse_numbers(match.group("orient"))
        if len(translate) != 3 or len(orient) != 4:
            raise ValueError(f"Invalid transform for source prim {match.group('name')}")
        sources[index] = SourcePrim(
            name=match.group("name"),
            instance_prim=match.group("instance"),
            translate_m=translate,  # type: ignore[arg-type]
            orient_wxyz=orient,  # type: ignore[arg-type]
        )
    if len(sources) != 162:
        raise ValueError(f"Expected 162 source prims in base.usda, found {len(sources)}")
    return sources


def parse_robot_visual_geoms(robot_xml: bytes) -> dict[int, VisualGeom]:
    root = ElementTree.fromstring(robot_xml)
    worldbody = root.find("worldbody")
    if worldbody is None:
        raise ValueError("MJCF has no worldbody")
    geoms: list[VisualGeom] = []

    def walk(body: ElementTree.Element, chain: tuple[str, ...]) -> None:
        body_name = body.attrib.get("name")
        if body_name is None:
            raise ValueError("Body without name in source MJCF")
        body_chain = (*chain, body_name)
        for child in body:
            if child.tag == "geom" and child.attrib.get("class") == "visual" and child.attrib.get("type") == "mesh":
                geoms.append(
                    VisualGeom(
                        source_index=len(geoms),
                        body_name=body_name,
                        body_chain=body_chain,
                        mesh_name=child.attrib["mesh"],
                        local_translate_m=_parse_xml_numbers(child.attrib.get("pos"), 3, (0.0, 0.0, 0.0)),  # type: ignore[arg-type]
                        local_orient_wxyz=_parse_xml_numbers(child.attrib.get("quat"), 4, (1.0, 0.0, 0.0, 0.0)),  # type: ignore[arg-type]
                    )
                )
            elif child.tag == "body":
                walk(child, body_chain)

    for child in worldbody:
        if child.tag == "body":
            walk(child, ())
    if len(geoms) != 162:
        raise ValueError(f"Expected 162 visual mesh geoms in source MJCF, found {len(geoms)}")
    return {geom.source_index: geom for geom in geoms}


def mesh_role(mesh_name: str) -> str:
    lowered = mesh_name.lower()
    if "shell" in lowered:
        return "shell"
    if "screw" in lowered:
        return "screw"
    if "washer" in lowered:
        return "washer"
    if lowered.startswith("std"):
        return "std_fastener"
    if lowered == "proximal":
        return "proximal_core"
    if lowered == "distal":
        return "distal_core"
    if "parallel_pin" in lowered:
        return "pin"
    if lowered == "gimbal":
        return "gimbal"
    if "servo_horn" in lowered:
        return "servo_horn"
    if lowered == "rotule_ball":
        return "ball"
    if "rotule_lever" in lowered:
        return "lever"
    if "rod" in lowered:
        return "rod"
    if lowered == "spacer":
        return "spacer"
    if lowered == "link":
        return "link"
    return "structural"


def structural_indices(geoms: dict[int, VisualGeom]) -> dict[int, tuple[int, ...]]:
    result: dict[int, tuple[int, ...]] = {}
    for finger, block in FINGER_BLOCKS.items():
        selected = tuple(
            index
            for index in block
            if index not in EXCLUDED_SHELL_INDICES
            and mesh_role(geoms[index].mesh_name) not in {"shell", "screw", "washer", "std_fastener"}
        )
        if len(selected) != 22:
            raise ValueError(f"Finger {finger} selected {len(selected)} structural visuals, expected 22")
        result[finger] = selected
    selected_cores = {index for values in result.values() for index in values if index in SOURCE_CORE_INDICES}
    if selected_cores != SOURCE_CORE_INDICES:
        raise ValueError(f"Missing source-specific proximal/distal core indices: {SOURCE_CORE_INDICES - selected_cores}")
    return result


def _quat_normalized(quat: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(quat))
    if norm == 0.0:
        raise ValueError("zero quaternion")
    if quat[0] < 0.0:
        quat = -quat
    return quat / norm


def _quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    out = np.empty(4, dtype=np.float64)
    mujoco.mju_mulQuat(out, a.astype(np.float64), b.astype(np.float64))
    return _quat_normalized(out)


def _quat_conj(quat: np.ndarray) -> np.ndarray:
    out = quat.copy()
    out[1:] *= -1.0
    return out


def _quat_rotate(quat: np.ndarray, vector: np.ndarray) -> np.ndarray:
    out = np.empty(3, dtype=np.float64)
    mujoco.mju_rotVecQuat(out, vector.astype(np.float64), quat.astype(np.float64))
    return out


def _rounded(values: np.ndarray | tuple[float, ...], digits: int = 10) -> list[float]:
    return [0.0 if math.isclose(float(value), 0.0, abs_tol=0.5 * 10 ** -digits) else round(float(value), digits) for value in values]


def _solve_keyframes(model: mujoco.MjModel, selected_indices: dict[int, tuple[int, ...]], geoms: dict[int, VisualGeom]) -> tuple[dict[str, dict[int, dict[str, list[float]]]], dict[str, object]]:
    data = mujoco.MjData(model)
    key_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "zero")
    if key_id < 0:
        raise ValueError("Expected a MuJoCo keyframe named 'zero'")
    model.opt.timestep = TIMESTEP_S
    actuator_ids = {name: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, name) for name in MOTOR_NAMES}
    joint_qpos = {
        name: int(model.jnt_qposadr[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)]) for name in MOTOR_NAMES
    }
    if any(identifier < 0 for identifier in actuator_ids.values()):
        raise ValueError(f"Missing actuator among {MOTOR_NAMES}")

    body_ids = {
        geom.body_name: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, geom.body_name)
        for geom in geoms.values()
    }
    if any(identifier < 0 for identifier in body_ids.values()):
        raise ValueError("Missing one or more MJCF visual bodies in compiled MuJoCo model")
    wrist_body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "r_wrist_interface")
    transforms_by_keyframe: dict[str, dict[int, dict[str, list[float]]]] = {}
    keyframe_reports: list[dict[str, object]] = []
    max_sep = 0.0
    max_motor_error = 0.0

    all_selected = tuple(index for values in selected_indices.values() for index in values)
    for keyframe_name, targets in KEYFRAMES.items():
        mujoco.mj_resetDataKeyframe(model, data, key_id)
        for finger in range(1, 5):
            data.ctrl[actuator_ids[f"finger{finger}_motor1"]] = targets["motor1"]
            data.ctrl[actuator_ids[f"finger{finger}_motor2"]] = targets["motor2"]
        for _ in range(STEP_COUNT):
            mujoco.mj_step(model, data)

        equality_sep = 0.0
        for equality_id in range(model.neq):
            if int(model.eq_objtype[equality_id]) != int(mujoco.mjtObj.mjOBJ_SITE):
                continue
            site1 = int(model.eq_obj1id[equality_id])
            site2 = int(model.eq_obj2id[equality_id])
            separation = float(np.linalg.norm(data.site_xpos[site1] - data.site_xpos[site2]))
            equality_sep = max(equality_sep, separation)
        motor_error = max(
            abs(float(data.qpos[qpos_address]) - float(data.ctrl[actuator_ids[name]]))
            for name, qpos_address in joint_qpos.items()
        )
        if equality_sep >= MAX_EQUALITY_SITE_PAIR_SEPARATION_M:
            raise ValueError(f"{keyframe_name} equality site-pair separation {equality_sep} exceeds tolerance")
        if motor_error >= MAX_MOTOR_TARGET_ERROR_RAD:
            raise ValueError(f"{keyframe_name} motor target error {motor_error} exceeds tolerance")
        max_sep = max(max_sep, equality_sep)
        max_motor_error = max(max_motor_error, motor_error)

        wrist_pos = data.xpos[wrist_body_id].copy()
        wrist_quat = _quat_normalized(data.xquat[wrist_body_id].copy())
        wrist_inv = _quat_conj(wrist_quat)
        frame_transforms: dict[int, dict[str, list[float]]] = {}
        for index in all_selected:
            geom = geoms[index]
            body_id = body_ids[geom.body_name]
            body_pos = data.xpos[body_id].copy()
            body_quat = _quat_normalized(data.xquat[body_id].copy())
            local_pos = np.asarray(geom.local_translate_m, dtype=np.float64)
            local_quat = _quat_normalized(np.asarray(geom.local_orient_wxyz, dtype=np.float64))
            world_pos = body_pos + _quat_rotate(body_quat, local_pos)
            world_quat = _quat_mul(body_quat, local_quat)
            wrist_local_pos = _quat_rotate(wrist_inv, world_pos - wrist_pos)
            wrist_local_quat = _quat_mul(wrist_inv, world_quat)
            frame_transforms[index] = {
                "translate_m": _rounded(wrist_local_pos),
                "orient_wxyz": _rounded(wrist_local_quat),
            }
        transforms_by_keyframe[keyframe_name] = frame_transforms
        keyframe_reports.append(
            {
                "name": keyframe_name,
                "targets_rad": dict(targets),
                "max_equality_site_pair_separation_m": equality_sep,
                "max_motor_target_error_rad": motor_error,
            }
        )

    solver = {
        "dt": TIMESTEP_S,
        "step_count": STEP_COUNT,
        "reset_keyframe": "zero",
        "composition": "body_xpos_xquat_composed_with_raw_xml_geom_local_pos_quat",
        "max_equality_site_pair_separation_m": max_sep,
        "max_motor_target_error_rad": max_motor_error,
        "keyframe_reports": keyframe_reports,
    }
    return transforms_by_keyframe, solver


def build_manifest(source_package_zip: Path, hand_distribution_zip: Path) -> dict[str, object]:
    package_sha = sha256_file(source_package_zip)
    hand_sha = sha256_file(hand_distribution_zip)
    if package_sha != SOURCE_PACKAGE_ZIP_SHA256:
        raise ValueError(f"Source package ZIP checksum mismatch: {package_sha}")
    if hand_sha != SOURCE_HAND_ZIP_SHA256:
        raise ValueError(f"Hand distribution ZIP checksum mismatch: {hand_sha}")

    with zipfile.ZipFile(source_package_zip) as package_archive:
        robot_xml = _read_zip_member(package_archive, exact=PACKAGE_ROBOT_XML_ENTRY)
        robot_xml_sha = hashlib.sha256(robot_xml).hexdigest()
        if robot_xml_sha != SOURCE_MJCF_SHA256:
            raise ValueError(f"Source MJCF checksum mismatch: {robot_xml_sha}")
        geoms = parse_robot_visual_geoms(robot_xml)
        selected_indices = structural_indices(geoms)
        with tempfile.TemporaryDirectory(prefix="amazinghand_mjcf_") as tmp:
            tmp_path = Path(tmp)
            _safe_extract_prefix(package_archive, PACKAGE_HAND_MJCF_PREFIX, tmp_path)
            model = mujoco.MjModel.from_xml_path(str(tmp_path / PACKAGE_HAND_MJCF_PREFIX / "scene.xml"))
            transforms_by_keyframe, solver = _solve_keyframes(model, selected_indices, geoms)

    with zipfile.ZipFile(hand_distribution_zip) as hand_archive:
        base_usda = _read_zip_member(hand_archive, suffix=DISTRIBUTION_BASE_USDA_SUFFIX).decode("utf-8")
    sources = parse_base_usda(base_usda)

    parts: list[dict[str, object]] = []
    for finger, indices in selected_indices.items():
        for index in indices:
            source = sources[index]
            geom = geoms[index]
            role = mesh_role(geom.mesh_name)
            parts.append(
                {
                    "finger": finger,
                    "role": role,
                    "mesh_role": role,
                    "mesh_name": geom.mesh_name,
                    "source_index": index,
                    "source_prim": source.name,
                    "instance_prim": source.instance_prim,
                    "body_name": geom.body_name,
                    "body_chain": list(geom.body_chain),
                    "raw_xml_geom_local": {
                        "translate_m": _rounded(geom.local_translate_m),
                        "orient_wxyz": _rounded(geom.local_orient_wxyz),
                    },
                    "source_usd_rest_transform": {
                        "translate_m": _rounded(source.translate_m),
                        "orient_wxyz": _rounded(source.orient_wxyz),
                    },
                    "transforms": {
                        keyframe_name: transforms_by_keyframe[keyframe_name][index]
                        for keyframe_name in KEYFRAMES
                    },
                }
            )

    return {
        "manifest_version": MANIFEST_VERSION,
        "coordinate_frame": "r_wrist_interface wrist-local, meters, quaternion-wxyz",
        "source_package_zip_sha256": package_sha,
        "source_mjcf_sha256": SOURCE_MJCF_SHA256,
        "source_hand_zip_sha256": hand_sha,
        "finger_count": 4,
        "parts_per_finger": 22,
        "structural_visual_part_count": len(parts),
        "excluded_shell_indices": sorted(EXCLUDED_SHELL_INDICES),
        "source_core_indices": sorted(SOURCE_CORE_INDICES),
        "finger_blocks": {str(finger): [block.start, block.stop] for finger, block in FINGER_BLOCKS.items()},
        "keyframes": [{"name": name, "targets_rad": targets} for name, targets in KEYFRAMES.items()],
        "solver": solver,
        "parts": parts,
    }


def write_manifest(manifest: dict[str, object], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate shell-free AmazingHand passive linkage keyframes.")
    parser.add_argument("--source-package-zip", type=Path, required=True)
    parser.add_argument("--hand-distribution-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    manifest = build_manifest(args.source_package_zip, args.hand_distribution_zip)
    write_manifest(manifest, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
