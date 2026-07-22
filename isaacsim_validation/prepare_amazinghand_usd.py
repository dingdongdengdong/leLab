"""Verify and stage the project-provided AmazingHand Isaac Sim USD distribution."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import textwrap
import zipfile
from pathlib import Path, PurePosixPath

AMAZINGHAND_USD_ENTRY = Path("usd/amazinghand_graspable/amazinghand_graspable.usda")
AMAZINGHAND_LEARNING_ENTRY = Path("usd/amazinghand_graspable/amazinghand_learning.usda")
EXPECTED_HAND_JOINTS = tuple(
    f"finger{finger}_motor{motor}" for finger in range(1, 5) for motor in range(1, 3)
)
REQUIRED_PACKAGE_FILES = frozenset(
    {
        AMAZINGHAND_USD_ENTRY.as_posix(),
        "usd/amazinghand_graspable/payloads/base.usda",
        "usd/amazinghand_graspable/payloads/robot.usda",
        "usd/amazinghand_graspable/payloads/Physics/physics.usda",
        "usd/amazinghand_graspable/payloads/Physics/physx.usda",
        "usd/amazinghand_graspable/payloads/geometries.usd",
        "usd/amazinghand_graspable/payloads/instances.usda",
        "manifest.json",
    }
)
VISUAL_SHELL = "amazinghand_visual_shell"
VISUAL_SHELL_JOINT = "wrist_to_amazinghand_visual_shell"
PROXIMAL_FRAME_REFERENCES = (
    "mjcf_052_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_proximal_5",
)
DISTAL_FRAME_REFERENCES = (
    "mjcf_044_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_distal_2",
)
EXCLUDED_OUTER_SHELL_REFERENCES = (
    "mjcf_051_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_proximal_shell_4",
    "mjcf_045_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_distal_shell_3",
)
HAND_DRIVE_STIFFNESS = 3.1415927


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe ZIP member: {name}")
    return path


def _remove_prim_block(text: str, name: str) -> str:
    match = re.search(
        rf'(?m)^[ \t]*(?:def|over)(?: [A-Za-z0-9_:]+)? "{re.escape(name)}"(?:[ \t]*\([^{{]*?\))?[ \t]*\n?[ \t]*\{{',
        text,
    )
    if match is None:
        raise ValueError(f"USD layer is missing expected prim block: {name}")
    brace_start = text.find("{", match.start())
    depth = 0
    for index in range(brace_start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                while end < len(text) and text[end] in " \t":
                    end += 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                return text[: match.start()] + text[end:]
    raise ValueError(f"USD prim block has unbalanced braces: {name}")


def _extract_prim_block(text: str, name: str) -> str:
    marker = re.search(
        rf'(?m)^[ \t]*(?:def|over)(?: [A-Za-z0-9_:]+)? "{re.escape(name)}"(?:[ \t]*\([^{{]*?\))?[ \t]*\n?[ \t]*\{{',
        text,
    )
    if marker is None:
        raise ValueError(f"USD layer is missing expected prim block: {name}")
    brace_start = text.find("{", marker.start())
    depth = 0
    for index in range(brace_start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[marker.start() : index + 1]
    raise ValueError(f"USD prim block has unbalanced braces: {name}")


def _remove_relationship_target(text: str, target: str) -> str:
    pattern = rf"(?m)^[ \t]*</{re.escape(target)}>,?[ \t]*\n"
    updated, count = re.subn(pattern, "", text)
    if count != 1:
        raise ValueError(f"expected one USD relationship target {target}, found {count}")
    return updated


def repair_hand_only_binding(package_dir: Path) -> dict[str, bool]:
    """Keep the detailed shell visual-only and remove its invalid rigid-body binding."""
    payloads = package_dir / "usd" / "amazinghand_graspable" / "payloads"
    physics_path = payloads / "Physics" / "physics.usda"
    robot_path = payloads / "robot.usda"
    physics = physics_path.read_text(encoding="utf-8")
    robot = robot_path.read_text(encoding="utf-8")

    physics = _remove_prim_block(physics, VISUAL_SHELL)
    physics = _remove_prim_block(physics, VISUAL_SHELL_JOINT)
    damping_line = "            float drive:angular:physics:damping = 0.08\n"
    stiffness_lines = (
        damping_line
        + f"            float drive:angular:physics:stiffness = {HAND_DRIVE_STIFFNESS}\n"
    )
    physics, drive_count = physics.replace(damping_line, stiffness_lines), physics.count(damping_line)
    if drive_count != len(EXPECTED_HAND_JOINTS):
        raise ValueError(f"expected eight AmazingHand angular drives, found {drive_count}")
    robot = _remove_relationship_target(
        robot,
        f"amazinghand_graspable/Geometry/r_wrist_interface/{VISUAL_SHELL}",
    )
    robot = _remove_relationship_target(
        robot,
        f"amazinghand_graspable/Physics/{VISUAL_SHELL_JOINT}",
    )
    robot = _remove_prim_block(robot, VISUAL_SHELL)
    robot = _remove_prim_block(robot, VISUAL_SHELL_JOINT)

    physics_path.write_text(physics, encoding="utf-8")
    robot_path.write_text(robot, encoding="utf-8")
    return {
        "removed_visual_shell_rigid_body": True,
        "removed_visual_shell_fixed_joint": True,
        "removed_visual_shell_robot_link": True,
        "removed_visual_shell_robot_joint": True,
        "authored_position_drive_stiffness": True,
    }


def _moving_visual_specs(finger: int) -> str:
    proximal = "\n\n".join(
        f'''def Xform "zip_proximal_{index}" (
    instanceable = true
    prepend references = @./instances.usda@</Instances/{reference}>
)
{{
}}'''
        for index, reference in enumerate(PROXIMAL_FRAME_REFERENCES, start=1)
    )
    distal = "\n\n".join(
        f'''def Xform "zip_distal_{index}_pose"
{{
    double3 xformOp:translate = (0, -0.058, 0)
    uniform token[] xformOpOrder = ["xformOp:translate"]

    def Xform "model" (
        instanceable = true
        prepend references = @./instances.usda@</Instances/{reference}>
    )
    {{
    }}
}}'''
        for index, reference in enumerate(DISTAL_FRAME_REFERENCES, start=1)
    )
    return f'''over "finger{finger}_proximal"
{{
{textwrap.indent(proximal, "    ")}

    over "finger{finger}_distal"
    {{
{textwrap.indent(distal, "        ")}
    }}
}}'''


def bind_learning_visuals(package_dir: Path) -> dict[str, int | str]:
    """Create a ZIP-geometry learning entry with visuals rigidly parented to hand links."""
    asset = package_dir / "usd" / "amazinghand_graspable"
    payloads = asset / "payloads"
    base = (payloads / "base.usda").read_text(encoding="utf-8")
    static_names = [
        name
        for name, number in re.findall(r'(?m)^\s*def Xform "(mjcf_(\d{3})_[^"]+)"', base)
        if int(number) <= 25
    ]
    if len(static_names) != 26:
        raise ValueError(f"expected 26 ZIP wrist/palm visual parts, found {len(static_names)}")
    static_parts = "\n\n".join(
        f'''def Xform "zip_static_{index:02d}" (
    instanceable = true
    prepend references = @./base.usda@</amazinghand_graspable/Geometry/r_wrist_interface/amazinghand_visual_shell/{name}>
)
{{
}}'''
        for index, name in enumerate(static_names)
    )
    finger_specs = "\n\n".join(_moving_visual_specs(finger) for finger in range(1, 5))
    overlay = f'''#usda 1.0
(
    defaultPrim = "amazinghand_graspable"
    kilogramsPerUnit = 1
    metersPerUnit = 1
    upAxis = "Z"
)

over "amazinghand_graspable"
{{
    over "Geometry"
    {{
        over "r_wrist_interface"
        {{
            over "amazinghand_visual_shell" (
                active = false
            )
            {{
            }}

            def Xform "zip_static_wrist_palm"
            {{
{textwrap.indent(static_parts, "                ")}
            }}

            over "palm"
            {{
{textwrap.indent(finger_specs, "                ")}
            }}
        }}
    }}
}}
'''
    (payloads / "learning_visuals.usda").write_text(overlay, encoding="utf-8")
    learning = '''#usda 1.0
(
    defaultPrim = "amazinghand_graspable"
    kilogramsPerUnit = 1
    metersPerUnit = 1
    subLayers = [
        @./payloads/learning_visuals.usda@,
        @./amazinghand_graspable.usda@,
    ]
    upAxis = "Z"
)
'''
    (package_dir / AMAZINGHAND_LEARNING_ENTRY).write_text(learning, encoding="utf-8")
    return {
        "entry_stage": AMAZINGHAND_LEARNING_ENTRY.as_posix(),
        "visual_mode": "frame_first_no_outer_shells",
        "static_visual_part_count": len(static_names),
        "moving_visual_part_count": 4
        * (len(PROXIMAL_FRAME_REFERENCES) + len(DISTAL_FRAME_REFERENCES)),
        "excluded_outer_shell_part_count": 4 * len(EXCLUDED_OUTER_SHELL_REFERENCES),
    }


def prepare_amazinghand_usd(
    source_zip: Path,
    output_dir: Path,
    *,
    expected_sha256: str,
) -> dict:
    """Extract the checked Isaac USD package without its validator-hostile preview stage."""
    source_zip = source_zip.expanduser().resolve()
    if not source_zip.is_file():
        raise FileNotFoundError(f"AmazingHand USD distribution does not exist: {source_zip}")
    actual_sha256 = _sha256(source_zip)
    if actual_sha256 != expected_sha256.lower():
        raise ValueError(
            "AmazingHand USD distribution checksum mismatch: "
            f"expected {expected_sha256.lower()}, got {actual_sha256}"
        )

    with zipfile.ZipFile(source_zip) as archive:
        members = [(info, _safe_member_path(info.filename)) for info in archive.infolist()]
        roots = {path.parts[0] for _, path in members if path.parts}
        if len(roots) != 1:
            raise ValueError(f"expected one distribution root, found {sorted(roots)}")
        root = next(iter(roots))
        relative_names = {
            PurePosixPath(*path.parts[1:]).as_posix()
            for _, path in members
            if len(path.parts) > 1 and path.name != ""
        }
        missing = sorted(REQUIRED_PACKAGE_FILES - relative_names)
        if missing:
            raise ValueError(f"AmazingHand USD distribution is missing required files: {missing}")

        manifest_member = f"{root}/manifest.json"
        source_manifest = json.loads(archive.read(manifest_member).decode("utf-8"))
        joint_names = source_manifest.get("joint_names")
        if joint_names != list(EXPECTED_HAND_JOINTS):
            raise ValueError(f"unexpected AmazingHand joint contract: {joint_names}")
        if source_manifest.get("entry_stage") not in (None, AMAZINGHAND_USD_ENTRY.as_posix()):
            raise ValueError(f"unexpected AmazingHand entry stage: {source_manifest.get('entry_stage')}")

        output_dir = output_dir.expanduser().resolve()
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)
        extracted_files: list[str] = []
        for info, member in members:
            if info.is_dir() or len(member.parts) <= 1:
                continue
            relative = Path(*member.parts[1:])
            if relative.as_posix() == "usd/amazinghand_graspable/preview_hand.usda":
                continue
            target = output_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as destination:
                shutil.copyfileobj(source, destination)
            extracted_files.append(relative.as_posix())

    repair = repair_hand_only_binding(output_dir)
    learning_visuals = bind_learning_visuals(output_dir)
    prepared_manifest = {
        "schema_version": 1,
        "source_kind": "isaac_sim_usd_distribution",
        "source_zip": str(source_zip),
        "source_zip_sha256": actual_sha256,
        "source_binding_status": source_manifest.get("simready_articulation_binding"),
        "prepared_binding_status": "hand_only_articulation_repaired",
        "combined_superarm_binding_status": "pending",
        "source_visual_validation": source_manifest.get("visual_validation"),
        "source_entry_stage": AMAZINGHAND_USD_ENTRY.as_posix(),
        "entry_stage": learning_visuals["entry_stage"],
        "hand_joint_names": joint_names,
        "preview_stage_excluded_from_asset_folder": True,
        "extracted_files": sorted(extracted_files),
        "repairs": repair,
        "learning_visuals": learning_visuals,
    }
    (output_dir / "prepared-manifest.json").write_text(
        json.dumps(prepared_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return prepared_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-zip", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    manifest = prepare_amazinghand_usd(
        args.source_zip,
        args.output_dir,
        expected_sha256=args.expected_sha256,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
