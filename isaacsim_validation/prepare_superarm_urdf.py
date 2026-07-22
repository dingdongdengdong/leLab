"""Build a self-contained, checksum-recorded SuperArm URDF package."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

EXPECTED_ARM_JOINTS = tuple(f"joint_rev_{index}" for index in range(1, 6))
EXPECTED_HAND_JOINTS = tuple(
    f"finger{finger}_motor{motor}" for finger in range(1, 5) for motor in range(1, 3)
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_mesh(source_urdf: Path, filename: str) -> Path:
    raw = filename.removeprefix("file://")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = source_urdf.parent / candidate
    return candidate.resolve()


def prepare_package(
    source_urdf: Path,
    output_dir: Path,
    source_root: Path | None = None,
    *,
    profile: str = "raw",
) -> dict:
    source_urdf = source_urdf.expanduser().resolve()
    if not source_urdf.is_file():
        raise FileNotFoundError(f"source URDF does not exist: {source_urdf}")

    allowed_root = (source_root or source_urdf.parent).expanduser().resolve()
    if not source_urdf.is_relative_to(allowed_root):
        raise ValueError(f"source URDF escapes allowed source root: {source_urdf}")

    tree = ET.parse(source_urdf)
    root = tree.getroot()
    if root.tag != "robot" or root.get("name") != "superarm_amazinghand":
        raise ValueError("expected robot name 'superarm_amazinghand'")
    if profile not in {"raw", "served"}:
        raise ValueError("profile must be 'raw' or 'served'")
    if profile == "served":
        from lelab.superarm.showroom import (
            align_amazinghand_attachment,
            align_joint5_urdf,
            remove_amazinghand_visuals,
        )

        align_joint5_urdf(root)
        align_amazinghand_attachment(root)
        remove_amazinghand_visuals(root)

    joint_types = {joint.get("name"): joint.get("type") for joint in root.findall("joint")}
    missing_joints = [
        name for name in (*EXPECTED_ARM_JOINTS, *EXPECTED_HAND_JOINTS) if name not in joint_types
    ]
    if missing_joints:
        raise ValueError(f"URDF is missing required movable joints: {missing_joints}")

    meshes = root.findall(".//mesh")
    if not meshes:
        raise ValueError("URDF has no mesh references")

    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    mesh_dir = output_dir / "meshes"
    mesh_dir.mkdir(parents=True)

    packaged: dict[Path, str] = {}
    assets: list[dict] = []
    for mesh in meshes:
        filename = mesh.get("filename")
        if not filename:
            raise ValueError("mesh reference has no filename")
        source = _resolve_mesh(source_urdf, filename)
        if not source.is_relative_to(allowed_root):
            raise ValueError(f"mesh escapes allowed source root: {filename}")
        if not source.is_file():
            raise FileNotFoundError(f"referenced mesh does not exist: {source}")

        packaged_name = packaged.get(source)
        if packaged_name is None:
            packaged_name = f"{len(packaged):03d}_{source.name}"
            target = mesh_dir / packaged_name
            shutil.copyfile(source, target)
            packaged[source] = packaged_name
            assets.append(
                {
                    "source": str(source),
                    "packaged": f"meshes/{packaged_name}",
                    "bytes": target.stat().st_size,
                    "sha256": _sha256(target),
                }
            )
        mesh.set("filename", f"meshes/{packaged_name}")

    ET.indent(tree, space="  ")
    packaged_urdf = output_dir / "superarm_amazinghand.urdf"
    tree.write(packaged_urdf, encoding="utf-8", xml_declaration=True)

    link_count = len(root.findall("link"))
    movable_joints = [name for name, kind in joint_types.items() if kind != "fixed"]
    manifest = {
        "schema_version": 1,
        "profile": profile,
        "robot": root.get("name"),
        "source_urdf": str(source_urdf),
        "source_root": str(allowed_root),
        "source_urdf_sha256": _sha256(source_urdf),
        "packaged_urdf": str(packaged_urdf),
        "packaged_urdf_sha256": _sha256(packaged_urdf),
        "link_count": link_count,
        "joint_count": len(joint_types),
        "movable_joints": movable_joints,
        "arm_joints": list(EXPECTED_ARM_JOINTS),
        "hand_joints": list(EXPECTED_HAND_JOINTS),
        "mesh_reference_count": len(meshes),
        "unique_mesh_count": len(assets),
        "assets": assets,
    }
    (output_dir / "asset-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-urdf", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--profile", choices=("raw", "served"), default="raw")
    args = parser.parse_args()
    manifest = prepare_package(
        args.source_urdf,
        args.output_dir,
        args.source_root,
        profile=args.profile,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
