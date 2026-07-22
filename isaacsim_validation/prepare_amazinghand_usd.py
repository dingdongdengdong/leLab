"""Verify and stage the project-provided AmazingHand Isaac Sim USD distribution."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath

AMAZINGHAND_USD_ENTRY = Path("usd/amazinghand_graspable/amazinghand_graspable.usda")
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

    prepared_manifest = {
        "schema_version": 1,
        "source_kind": "isaac_sim_usd_distribution",
        "source_zip": str(source_zip),
        "source_zip_sha256": actual_sha256,
        "source_binding_status": source_manifest.get("simready_articulation_binding"),
        "source_visual_validation": source_manifest.get("visual_validation"),
        "entry_stage": AMAZINGHAND_USD_ENTRY.as_posix(),
        "hand_joint_names": joint_names,
        "preview_stage_excluded_from_asset_folder": True,
        "extracted_files": sorted(extracted_files),
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
