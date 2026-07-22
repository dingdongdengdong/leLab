"""Bind visuals from the supplied AmazingHand USD into one SuperArm articulation."""

from __future__ import annotations

import json
import re
import shutil
import textwrap
from pathlib import Path

try:
    from .prepare_amazinghand_usd import (
        DISTAL_FRAME_REFERENCES,
        EXCLUDED_OUTER_SHELL_REFERENCES,
        PROXIMAL_FRAME_REFERENCES,
    )
except ImportError:  # Isaac executes this module from its script directory.
    from prepare_amazinghand_usd import (
        DISTAL_FRAME_REFERENCES,
        EXCLUDED_OUTER_SHELL_REFERENCES,
        PROXIMAL_FRAME_REFERENCES,
    )

VISUAL_PAYLOAD_FILES = ("base.usda", "robot.usda", "instances.usda", "geometries.usd")
HAND_FRAME_REFERENCE_COMMIT = "0e53b0dfadaae3234d14fb5830108ae931734d0c"


def static_visual_names(prepared_package: Path) -> tuple[str, ...]:
    """Return the 26 wrist/palm visual prims from the checked ZIP package."""
    base = (
        prepared_package
        / "usd"
        / "amazinghand_graspable"
        / "payloads"
        / "base.usda"
    ).read_text(encoding="utf-8")
    names = tuple(
        name
        for name, number in re.findall(r'(?m)^\s*def Xform "(mjcf_(\d{3})_[^"]+)"', base)
        if int(number) <= 25
    )
    if len(names) != 26:
        raise ValueError(f"expected 26 AmazingHand wrist/palm visuals, found {len(names)}")
    return names


def visual_reference_contract(prepared_package: Path) -> dict:
    """Describe the ZIP-derived geometry that will be parented to physical links."""
    return {
        "static_wrist_palm": list(static_visual_names(prepared_package)),
        "proximal": list(PROXIMAL_FRAME_REFERENCES),
        "distal": list(DISTAL_FRAME_REFERENCES),
        "excluded_outer_shells": list(EXCLUDED_OUTER_SHELL_REFERENCES),
        "visual_mode": "frame_first_no_outer_shells",
        "frame_reference_commit": HAND_FRAME_REFERENCE_COMMIT,
        "frame_reference_model": "isaac_open_chain_four_finger_two_link",
        "finger_count": 4,
        "static_visual_part_count": 26,
        "moving_visual_part_count": 8,
        "excluded_outer_shell_part_count": 8,
    }


def _unique_named_prim(stage, robot_root: str, name: str):
    prefix = robot_root.rstrip("/") + "/"
    matches = [
        prim
        for prim in stage.Traverse()
        if prim.GetName() == name and str(prim.GetPath()).startswith(prefix)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one combined-robot prim named {name!r}, found {len(matches)}")
    return matches[0]


def bind_zip_hand_visuals(stage, robot_root: str, prepared_package: Path) -> dict:
    """Author ZIP visual references under the existing 13-DOF SuperArm hand links.

    The imported ``zip_learning`` URDF owns all rigid bodies, collisions, and
    joints. This function adds visual references only; it never references the
    hand USD's articulation or Physics payloads, so the final robot keeps one
    articulation root.
    """
    from pxr import Sdf, UsdGeom

    prepared_package = prepared_package.resolve()
    manifest = json.loads(
        (prepared_package / "prepared-manifest.json").read_text(encoding="utf-8")
    )
    source_payloads = prepared_package / "usd" / "amazinghand_graspable" / "payloads"
    root_layer = Path(stage.GetRootLayer().realPath)
    target_payloads = root_layer.parent / "zip_hand_payloads"
    if target_payloads.exists():
        shutil.rmtree(target_payloads)
    target_payloads.mkdir(parents=True)
    for name in VISUAL_PAYLOAD_FILES:
        source = source_payloads / name
        if not source.is_file():
            raise FileNotFoundError(f"prepared AmazingHand visual payload is missing: {source}")
        shutil.copyfile(source, target_payloads / name)

    distal_definitions = "\n\n".join(
        f'''def Xform "distal_{index}" (
    instanceable = true
    prepend references = @./instances.usda@</Instances/{source_name}>
)
{{
    double3 xformOp:translate = (0, -0.058, 0)
    uniform token[] xformOpOrder = ["xformOp:translate"]
}}'''
        for index, source_name in enumerate(DISTAL_FRAME_REFERENCES, start=1)
    )
    (target_payloads / "distal_visuals.usda").write_text(
        f'''#usda 1.0
(
    metersPerUnit = 1
    upAxis = "Z"
)

def Scope "DistalVisuals"
{{
{textwrap.indent(distal_definitions, "    ")}
}}
''',
        encoding="utf-8",
    )

    wrist = _unique_named_prim(stage, robot_root, "r_wrist_interface")
    static_group = UsdGeom.Xform.Define(stage, wrist.GetPath().AppendChild("zip_static_wrist_palm"))
    base_reference = "./zip_hand_payloads/base.usda"
    for index, source_name in enumerate(static_visual_names(prepared_package)):
        prim = UsdGeom.Xform.Define(
            stage,
            static_group.GetPath().AppendChild(f"zip_static_{index:02d}"),
        ).GetPrim()
        prim.GetReferences().AddReference(
            base_reference,
            Sdf.Path(
                "/amazinghand_graspable/Geometry/r_wrist_interface/"
                f"amazinghand_visual_shell/{source_name}"
            ),
        )
        prim.SetInstanceable(True)

    instance_reference = "./zip_hand_payloads/instances.usda"
    for finger in range(1, 5):
        proximal_link = _unique_named_prim(stage, robot_root, f"finger{finger}_proximal")
        distal_link = _unique_named_prim(stage, robot_root, f"finger{finger}_distal")
        for index, source_name in enumerate(PROXIMAL_FRAME_REFERENCES, start=1):
            prim = UsdGeom.Xform.Define(
                stage,
                proximal_link.GetPath().AppendChild(f"zip_proximal_{index}"),
            ).GetPrim()
            prim.GetReferences().AddReference(
                instance_reference,
                Sdf.Path(f"/Instances/{source_name}"),
            )
            prim.SetInstanceable(True)
        for index, _source_name in enumerate(DISTAL_FRAME_REFERENCES, start=1):
            prim = UsdGeom.Xform.Define(
                stage,
                distal_link.GetPath().AppendChild(f"zip_distal_{index}"),
            ).GetPrim()
            prim.GetReferences().AddReference(
                "./zip_hand_payloads/distal_visuals.usda",
                Sdf.Path(f"/DistalVisuals/distal_{index}"),
            )
            prim.SetInstanceable(True)

    stage.GetRootLayer().Save()
    contract = visual_reference_contract(prepared_package)
    return {
        **contract,
        "source_kind": "supplied_isaac_sim_usd_distribution",
        "source_zip": manifest["source_zip"],
        "source_zip_sha256": manifest["source_zip_sha256"],
        "copied_visual_payloads": list(VISUAL_PAYLOAD_FILES),
        "generated_visual_payloads": ["distal_visuals.usda"],
        "physics_source": "combined_superarm_zip_learning_urdf_import",
        "articulation_imported_from_hand_usd": False,
    }
