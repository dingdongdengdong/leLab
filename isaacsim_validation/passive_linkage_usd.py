"""USD authoring boundary for AmazingHand passive-linkage snapshot visuals.

The pure planning helpers in this module are CI-safe and have no USD runtime
dependency.  The actual USD authoring function imports pxr lazily so default
tests can validate the contract without Isaac Sim installed.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

try:
    from .passive_linkage import (
        EXPECTED_PART_COUNT,
        EXPECTED_PARTS_PER_FINGER,
        PassiveVisualPose,
    )
except ImportError:  # Isaac executes this module from its script directory.
    from passive_linkage import (  # type: ignore[no-redef]
        EXPECTED_PART_COUNT,
        EXPECTED_PARTS_PER_FINGER,
        PassiveVisualPose,
    )

FRAME_FIRST_CORE_REF_NAMES = ("zip_proximal_1", "zip_distal_1")
PASSIVE_VISUAL_ROOT_NAME = "passive_linkage_visuals"
PASSIVE_VISUAL_MODE = "frame_plus_passive_linkage_no_shells"
FORBIDDEN_SHELL_FRAGMENTS = ("proximal_shell", "distal_shell")
FORBIDDEN_PHYSICS_MARKERS = (
    "Physics",
    "RigidBody",
    "Collision",
    "Collider",
    "Mass",
    "Joint",
)
ASSET_PATH_PATTERN = re.compile(r"@([^@]+)@")


def build_passive_linkage_author_plan(poses: Sequence[PassiveVisualPose]) -> dict[str, Any]:
    """Build a deterministic, pure-data authoring plan for passive visuals."""

    if len(poses) != EXPECTED_PART_COUNT:
        raise ValueError(f"expected {EXPECTED_PART_COUNT} passive visual poses, found {len(poses)}")

    parts: list[dict[str, Any]] = []
    per_finger: Counter[int] = Counter()
    for pose in poses:
        if not pose.instance_prim.startswith("/Instances/"):
            raise ValueError(f"passive visual must reference exact /Instances prim: {pose.instance_prim}")
        if any(fragment in pose.source_prim for fragment in FORBIDDEN_SHELL_FRAGMENTS):
            raise ValueError(f"passive visual source includes an excluded shell: {pose.source_prim}")
        if any(fragment in pose.instance_prim for fragment in FORBIDDEN_SHELL_FRAGMENTS):
            raise ValueError(f"passive visual reference includes an excluded shell: {pose.instance_prim}")

        per_finger[pose.finger] += 1
        parts.append(
            {
                "finger": pose.finger,
                "source_index": pose.source_index,
                "source_prim": pose.source_prim,
                "xform_path": (
                    "/r_wrist_interface/"
                    f"{PASSIVE_VISUAL_ROOT_NAME}/finger{pose.finger}/part_{pose.source_index:03d}"
                ),
                "reference_prim": pose.instance_prim,
                "translate": pose.translate,
                "orient": pose.orient,
            }
        )

    parts_per_finger = dict(sorted(per_finger.items()))
    if parts_per_finger != dict.fromkeys(range(1, 5), EXPECTED_PARTS_PER_FINGER):
        raise ValueError(f"expected {EXPECTED_PARTS_PER_FINGER} parts per finger: {parts_per_finger}")
    xform_paths = [part["xform_path"] for part in parts]
    if len(xform_paths) != len(set(xform_paths)):
        raise ValueError("passive visual xform paths must be unique")

    return {
        "mode": PASSIVE_VISUAL_MODE,
        "visual_part_count": len(parts),
        "source_structural_pose_count": len(parts),
        "parts_per_finger": parts_per_finger,
        "deactivated_frame_first_core_ref_count": 8,
        "excluded_shell_visual_count": 0,
        "added_rigid_body_count": 0,
        "added_mass_count": 0,
        "added_collider_count": 0,
        "added_joint_count": 0,
        "parts": parts,
    }


def author_passive_linkage_snapshot(
    snapshot_stage,
    robot_root: str,
    poses: Sequence[PassiveVisualPose],
    instances_usda: Path,
) -> dict[str, Any]:
    """Add visual-only follower refs, flatten the snapshot, and return its contract."""

    from pxr import Gf, Sdf, Usd, UsdGeom

    instances_usda = instances_usda.resolve()
    if not instances_usda.is_file():
        raise FileNotFoundError(f"passive linkage instances.usda is missing: {instances_usda}")

    root_layer_real_path = snapshot_stage.GetRootLayer().realPath
    if not root_layer_real_path:
        raise RuntimeError("passive linkage snapshots must be file-backed USD stages")
    root_layer_path = Path(root_layer_real_path)

    plan = build_passive_linkage_author_plan(poses)
    deactivated = _deactivate_frame_first_core_refs(snapshot_stage, robot_root)
    wrist = _unique_named_prim(snapshot_stage, robot_root, "r_wrist_interface")
    passive_root = UsdGeom.Xform.Define(
        snapshot_stage,
        wrist.GetPath().AppendChild(PASSIVE_VISUAL_ROOT_NAME),
    )

    for pose, part in zip(poses, plan["parts"], strict=True):
        finger_root = UsdGeom.Xform.Define(
            snapshot_stage,
            passive_root.GetPath().AppendChild(f"finger{pose.finger}"),
        )
        prim = UsdGeom.Xform.Define(
            snapshot_stage,
            finger_root.GetPath().AppendChild(f"part_{pose.source_index:03d}"),
        ).GetPrim()
        xformable = UsdGeom.Xformable(prim)
        xformable.AddTranslateOp(precision=UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*pose.translate))
        w, x, y, z = pose.orient
        xformable.AddOrientOp(precision=UsdGeom.XformOp.PrecisionDouble).Set(Gf.Quatd(w, Gf.Vec3d(x, y, z)))
        _set_custom_attr(prim, "passive_source_index", _sdf_value_type(Sdf, "Int"), pose.source_index)
        _set_custom_attr(prim, "passive_source_prim", _sdf_value_type(Sdf, "String"), pose.source_prim)
        _set_custom_attr(
            prim, "passive_reference_prim", _sdf_value_type(Sdf, "String"), part["reference_prim"]
        )
        prim.GetReferences().AddReference(
            str(instances_usda),
            Sdf.Path(part["reference_prim"]),
        )
        prim.SetInstanceable(True)

    flattened = snapshot_stage.Flatten()
    temporary_path = root_layer_path.with_name(root_layer_path.name + ".passive_linkage.tmp.usda")
    try:
        flattened.Export(str(temporary_path))
        flattened_text = temporary_path.read_text(encoding="utf-8")
        validate_no_source_path_leaks(flattened_text, instances_usda)
        flattened_stage = Usd.Stage.Open(str(temporary_path))
        if flattened_stage is None:
            raise RuntimeError(f"could not reopen flattened passive-linkage snapshot: {temporary_path}")
        contract = _validate_flattened_snapshot(flattened_stage, robot_root)
        if deactivated != 8:
            raise RuntimeError(f"expected to deactivate 8 frame-first core refs, deactivated {deactivated}")
        os.replace(temporary_path, root_layer_path)
        return {
            **plan,
            **contract,
            "deactivated_frame_first_core_ref_count": deactivated,
            "flattened_snapshot": str(root_layer_path),
            "published_absolute_instance_refs": False,
            "published_external_asset_refs": False,
        }
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def validate_no_source_path_leaks(snapshot_text: str, instances_usda: Path) -> None:
    """Reject external source paths while allowing asset-free flattened prototype refs."""

    instances_usda = instances_usda.resolve()
    denied_plain_paths = (
        str(instances_usda),
        str(instances_usda.parent),
    )
    for denied_path in denied_plain_paths:
        if denied_path and denied_path in snapshot_text:
            raise RuntimeError(f"external source asset path leak in flattened snapshot: {denied_path}")
    if "/tmp/" in snapshot_text:
        raise RuntimeError("external source asset path leak in flattened snapshot: /tmp/")

    for asset_path in ASSET_PATH_PATTERN.findall(snapshot_text):
        raise RuntimeError(f"external source asset path leak in flattened snapshot: {asset_path}")


def _set_custom_attr(prim, name: str, value_type, value) -> None:
    if not hasattr(prim, "CreateAttribute"):
        return
    prim.CreateAttribute(name, value_type, custom=True).Set(value)


def _sdf_value_type(sdf_module, name: str):
    value_types = getattr(sdf_module, "ValueTypeNames", None)
    return getattr(value_types, name, None) if value_types is not None else None


def _deactivate_frame_first_core_refs(stage, robot_root: str) -> int:
    matches = [
        prim for prim in _iter_prims_under(stage, robot_root) if prim.GetName() in FRAME_FIRST_CORE_REF_NAMES
    ]
    if len(matches) != 8:
        raise RuntimeError(f"expected eight frame-first core refs to deactivate, found {len(matches)}")
    for prim in matches:
        prim.SetActive(False)
    return len(matches)


def _validate_flattened_snapshot(stage, robot_root: str) -> dict[str, Any]:
    passive_root = _unique_named_prim(stage, robot_root, PASSIVE_VISUAL_ROOT_NAME)
    prefix = str(passive_root.GetPath()).rstrip("/") + "/"
    passive_prims = [
        prim
        for prim in stage.Traverse()
        if str(prim.GetPath()) == str(passive_root.GetPath()) or str(prim.GetPath()).startswith(prefix)
    ]
    part_prims = [
        prim
        for prim in passive_prims
        if prim.GetName().startswith("part_") and str(prim.GetPath()).count("/") >= 5
    ]
    shell_visuals = [
        prim
        for prim in passive_prims
        if any(fragment in prim.GetName() for fragment in FORBIDDEN_SHELL_FRAGMENTS)
    ]
    physics_prims = [prim for prim in passive_prims if _prim_has_forbidden_physics_marker(prim)]
    finger_counts = Counter(
        int(str(prim.GetPath()).split(f"/{PASSIVE_VISUAL_ROOT_NAME}/finger", 1)[1].split("/", 1)[0])
        for prim in part_prims
    )
    if len(part_prims) != EXPECTED_PART_COUNT:
        raise RuntimeError(
            f"expected {EXPECTED_PART_COUNT} flattened passive visual parts, found {len(part_prims)}"
        )
    if dict(sorted(finger_counts.items())) != dict.fromkeys(range(1, 5), EXPECTED_PARTS_PER_FINGER):
        raise RuntimeError(f"unexpected flattened passive visual parts per finger: {dict(finger_counts)}")
    if shell_visuals:
        raise RuntimeError(f"flattened passive linkage includes excluded shell visuals: {shell_visuals!r}")
    if physics_prims:
        raise RuntimeError(f"flattened passive linkage includes physics-authored prims: {physics_prims!r}")
    return {
        "validated_visual_part_count": len(part_prims),
        "validated_parts_per_finger": dict(sorted(finger_counts.items())),
        "validated_shell_visual_count": 0,
        "validated_added_physics_prim_count": 0,
    }


def _prim_has_forbidden_physics_marker(prim) -> bool:
    markers = [str(prim.GetTypeName()), *(str(schema) for schema in prim.GetAppliedSchemas())]
    return any(
        marker and forbidden in marker for marker in markers for forbidden in FORBIDDEN_PHYSICS_MARKERS
    )


def _unique_named_prim(stage, robot_root: str, name: str):
    matches = [prim for prim in _iter_prims_under(stage, robot_root) if prim.GetName() == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one combined-robot prim named {name!r}, found {len(matches)}")
    return matches[0]


def _iter_prims_under(stage, robot_root: str):
    prefix = robot_root.rstrip("/") + "/"
    for prim in stage.Traverse():
        path = str(prim.GetPath())
        if path == robot_root or path.startswith(prefix):
            yield prim
