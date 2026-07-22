"""Build a deterministic, relocatable SuperArm + AmazingHand Isaac Sim package."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

ENTRYPOINT = PurePosixPath("usd/superarm_amazinghand/superarm_amazinghand.usda")
SCHEMA = "superarm.isaac_sim.usd_distribution/v1"
USD_ASSET_PATTERN = re.compile(r"@([^@]+)@")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
EXPECTED_ARM_DOF_COUNT = 5
EXPECTED_HAND_DOF_COUNT = 8
EXPECTED_PHYSICAL_DOF_COUNT = 13
EXPECTED_LOGICAL_ACTION_WIDTH = 6
EXPECTED_PASSIVE_FOLLOWER_COUNT = 88


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return value


def _require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    return path


def _validated_reports(runtime_report: Path, validator_report: Path) -> tuple[dict, dict]:
    runtime = _read_json(_require_file(runtime_report, "runtime validation report"))
    validator = _read_json(_require_file(validator_report, "strict validator report"))
    if runtime.get("status") != "PASS":
        raise ValueError("runtime validation report must be PASS")

    motion = runtime.get("motion")
    imported = runtime.get("import")
    cleanup = runtime.get("package_cleanup")
    if not all(isinstance(item, dict) for item in (motion, imported, cleanup)):
        raise ValueError("runtime validation report is missing import, motion, or cleanup data")
    if imported.get("dof_count") != EXPECTED_PHYSICAL_DOF_COUNT:
        raise ValueError("runtime validation report must prove 13 imported DOFs")
    if motion.get("physical_movable_joint_count") != EXPECTED_PHYSICAL_DOF_COUNT:
        raise ValueError("runtime validation report must prove 13 physical movable joints")
    if motion.get("logical_action_width") != EXPECTED_LOGICAL_ACTION_WIDTH:
        raise ValueError("runtime validation report must prove a six-value logical action")
    if motion.get("arm_motion_passed") is not True or motion.get("hand_motion_passed") is not True:
        raise ValueError("runtime validation report must prove arm and hand motion")
    if cleanup.get("restored_pristine_root_layer_after_runtime") is not True:
        raise ValueError("runtime validation report must prove clean root-layer restoration")

    verdict = validator.get("verdict")
    if not isinstance(verdict, dict):
        raise ValueError("strict validator report is missing its verdict")
    if verdict.get("passed") is not True or verdict.get("blocking_issue_count") != 0:
        raise ValueError("strict validator report must pass with zero blocking issues")
    if validator.get("articulation_root_count") != 1:
        raise ValueError("strict validator report must prove one articulation root")
    if validator.get("revolute_joint_count") != EXPECTED_PHYSICAL_DOF_COUNT:
        raise ValueError("strict validator report must prove 13 revolute joints")
    return runtime, validator


def _passive_follower_count(runtime_report: dict[str, Any]) -> int:
    passive = runtime_report.get("passive_linkage_visuals")
    if not isinstance(passive, dict):
        raise ValueError("runtime validation report is missing passive-linkage evidence")
    sequence = passive.get("grasp_sequence")
    if not isinstance(sequence, dict) or sequence.get("passed") is not True:
        raise ValueError("runtime validation report must prove passive-linkage grasp motion")
    count = sequence.get("visual_part_count")
    if count is None:
        snapshots = sequence.get("per_snapshot")
        if not isinstance(snapshots, list) or not snapshots:
            raise ValueError("passive-linkage evidence is missing snapshot part counts")
        counts = {snapshot.get("visual_part_count") for snapshot in snapshots if isinstance(snapshot, dict)}
        if len(counts) != 1:
            raise ValueError("passive-linkage snapshot part counts are inconsistent")
        count = counts.pop()
    if count != EXPECTED_PASSIVE_FOLLOWER_COUNT:
        raise ValueError("runtime validation report must prove 88 passive visual followers")
    return count


def _validate_distribution_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) or ".." in name:
        raise ValueError(f"unsafe distribution name: {name!r}")


def _validate_local_usd_references(source_asset_dir: Path) -> int:
    root = source_asset_dir.resolve()
    reference_count = 0
    for layer in sorted(source_asset_dir.rglob("*")):
        if not layer.is_file() or layer.suffix.lower() not in {".usd", ".usda"}:
            continue
        try:
            text = layer.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for raw_reference in USD_ASSET_PATTERN.findall(text):
            reference_count += 1
            reference = PurePosixPath(raw_reference)
            if reference.is_absolute() or ".." in reference.parts or "://" in raw_reference:
                raise ValueError(f"external USD asset reference in {layer}: {raw_reference}")
            target = (layer.parent / Path(*reference.parts)).resolve()
            try:
                target.relative_to(root)
            except ValueError as error:
                raise ValueError(f"external USD asset reference in {layer}: {raw_reference}") from error
            if not target.is_file():
                raise ValueError(f"unresolved USD asset reference in {layer}: {raw_reference}")
    return reference_count


def _collect_asset_files(source_asset_dir: Path) -> dict[str, bytes]:
    entrypoint = source_asset_dir / "superarm_amazinghand.usda"
    _require_file(entrypoint, "SuperArm USD entrypoint")
    reference_count = _validate_local_usd_references(source_asset_dir)
    if reference_count == 0:
        raise ValueError("SuperArm USD package must contain local composition references")

    files: dict[str, bytes] = {}
    for source in sorted(source_asset_dir.rglob("*")):
        if source.is_symlink():
            raise ValueError(f"distribution source must not contain symlinks: {source}")
        if not source.is_file():
            continue
        relative = source.relative_to(source_asset_dir).as_posix()
        files[f"usd/superarm_amazinghand/{relative}"] = source.read_bytes()
    return files


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _readme(
    *,
    distribution_name: str,
    source_commit: str,
    validation_run_id: str,
    runtime_report: dict[str, Any],
) -> bytes:
    visual_boundary = runtime_report.get("visual_boundary", "See validation/isaac-report.json.")
    return f"""# SuperArm + AmazingHand Isaac Sim USD distribution

Package: `{distribution_name}`

Validated source commit: `{source_commit}`

Accepted Isaac run: `{validation_run_id}`

## Open the robot

1. Extract the ZIP without changing its internal directory layout.
2. Start Isaac Sim 6.0.0.
3. Open `usd/superarm_amazinghand/superarm_amazinghand.usda`.
4. Keep the root `Physics` variant set to `physx`.

All USD composition arcs are package-relative. The clean entrypoint is the reusable
robot asset; the large measured snapshot stages and runtime world state are deliberately
not included.

## Control contract

- One articulation root.
- 13 physical revolute DOFs: five SuperArm joints plus eight AmazingHand motor joints.
- Six logical LeRobot/VLA actions: five arm values plus one grasp scalar.
- The grasp scalar maps to the eight hand motor targets used by the project controller.

The physical joint order and validation evidence are recorded in `manifest.json` and
`validation/isaac-report.json`.

## Detailed hand linkage helper

The clean robot keeps the shell-free moving frame visuals. The 88 detailed structural
linkage pieces are visual-only followers, not extra physics bodies. They are intentionally
generated from measured hand angles instead of being baked into the reusable neutral asset.

The checked helper is included under `runtime/`:

```python
from runtime.passive_linkage import solve_passive_linkage
from runtime.passive_linkage_usd import author_passive_linkage_snapshot

poses = solve_passive_linkage(measured_hand_joint_positions)
contract = author_passive_linkage_snapshot(
    file_backed_snapshot_stage,
    "/superarm_amazinghand",
    poses,
    extracted_root / "usd/superarm_amazinghand/zip_hand_payloads/instances.usda",
)
```

`author_passive_linkage_snapshot` publishes a flattened, measured-state evidence stage;
it does not add closed-loop PhysX constraints to the reusable robot.

## Integrity and evidence

- Run `sha256sum -c SHA256SUMS` from this directory after extraction.
- `validation/asset-validator.json` is the accepted strict Isaac Sim validator result.
- `validation/passive_linkage_contact_sheet.png` is reviewed visual evidence, not a
  contact-force or grasp-retention result.
- Project and hand-source license texts are included at the distribution root.

## Proof boundary

{visual_boundary}

This package does not prove real-hardware transport, torque/current tuning, contact
quality, grasp retention, or a trained ACT/VLA policy. Rounded outer hand shells remain
excluded for the current frame-first structural validation phase.
""".encode()


def _manifest(
    *,
    distribution_name: str,
    source_commit: str,
    source_branch: str,
    validation_run_id: str,
    created_utc: str,
    runtime_report: dict[str, Any],
    validator_report: dict[str, Any],
    files: dict[str, bytes],
) -> dict[str, Any]:
    imported = runtime_report["import"]
    dof_names = imported.get("dof_names")
    if not isinstance(dof_names, list) or len(dof_names) != EXPECTED_PHYSICAL_DOF_COUNT:
        raise ValueError("runtime validation report must contain all 13 DOF names")
    arm_names = [name for name in dof_names if isinstance(name, str) and name.startswith("joint_rev_")]
    hand_names = [name for name in dof_names if isinstance(name, str) and name.startswith("finger")]
    if len(arm_names) != EXPECTED_ARM_DOF_COUNT or len(hand_names) != EXPECTED_HAND_DOF_COUNT:
        raise ValueError("runtime validation report must identify five arm and eight hand DOFs")

    zip_binding = runtime_report.get("zip_hand_binding")
    if not isinstance(zip_binding, dict):
        raise ValueError("runtime validation report is missing the hand visual binding")
    if zip_binding.get("excluded_outer_shell_part_count") != 8:
        raise ValueError("runtime validation report must prove eight excluded outer shells")

    inventory = [
        {"bytes": len(body), "path": path, "sha256": _sha256_bytes(body)}
        for path, body in sorted(files.items())
    ]
    return {
        "schema": SCHEMA,
        "name": distribution_name,
        "created_utc": created_utc,
        "entrypoint": ENTRYPOINT.as_posix(),
        "source": {
            "branch": source_branch,
            "commit": source_commit,
            "validation_run_id": validation_run_id,
            "hand_source_zip_sha256": zip_binding.get("source_zip_sha256"),
        },
        "runtime": {
            "isaac_sim_version": runtime_report.get("isaac_sim_version"),
            "validator_runtime": validator_report.get("runtime"),
            "physics_variant": "physx",
        },
        "robot_contract": {
            "articulation_root_count": 1,
            "physical_dof_count": EXPECTED_PHYSICAL_DOF_COUNT,
            "arm_dof_count": EXPECTED_ARM_DOF_COUNT,
            "hand_dof_count": EXPECTED_HAND_DOF_COUNT,
            "logical_action_width": EXPECTED_LOGICAL_ACTION_WIDTH,
        },
        "joint_names": {"arm": arm_names, "hand": hand_names, "physical_order": dof_names},
        "visual_contract": {
            "mode": zip_binding.get("visual_mode"),
            "outer_shells_included": False,
            "passive_follower_count": _passive_follower_count(runtime_report),
            "passive_followers_are_physics_bodies": False,
            "passive_snapshot_helper_included": True,
            "clean_entrypoint_contains_runtime_snapshot_state": False,
        },
        "validation": {
            "runtime_status": runtime_report.get("status"),
            "arm_motion_passed": runtime_report["motion"].get("arm_motion_passed"),
            "hand_motion_passed": runtime_report["motion"].get("hand_motion_passed"),
            "strict_validator_passed": validator_report["verdict"].get("passed"),
            "blocking_issue_count": validator_report["verdict"].get("blocking_issue_count"),
            "clean_root_restored": runtime_report["package_cleanup"].get(
                "restored_pristine_root_layer_after_runtime"
            ),
        },
        "proof_boundary": runtime_report.get("visual_boundary"),
        "files": inventory,
    }


def _write_deterministic_zip(output_zip: Path, distribution_name: str, files: dict[str, bytes]) -> None:
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_zip.with_suffix(output_zip.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for relative, body in sorted(files.items()):
                info = zipfile.ZipInfo(f"{distribution_name}/{relative}", ZIP_TIMESTAMP)
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, body, compresslevel=9)
        temporary.replace(output_zip)
    finally:
        temporary.unlink(missing_ok=True)


def export_distribution(
    *,
    source_asset_dir: Path,
    runtime_report: Path,
    validator_report: Path,
    preview_image: Path,
    license_file: Path,
    hand_license_file: Path,
    passive_solver: Path,
    passive_usd: Path,
    passive_manifest: Path,
    output_zip: Path,
    distribution_name: str,
    source_commit: str,
    source_branch: str,
    validation_run_id: str,
    created_utc: str,
) -> dict[str, Any]:
    """Export validated inputs as a reproducible, single-root distribution ZIP."""

    _validate_distribution_name(distribution_name)
    if not source_asset_dir.is_dir():
        raise FileNotFoundError(f"SuperArm USD package does not exist: {source_asset_dir}")
    runtime, validator = _validated_reports(runtime_report, validator_report)

    files = _collect_asset_files(source_asset_dir)
    files.update(
        {
            "LICENSE": _require_file(license_file, "project license").read_bytes(),
            "LICENSE-AmazingHandControl": _require_file(
                hand_license_file, "AmazingHandControl license"
            ).read_bytes(),
            "runtime/__init__.py": b'"""SuperArm distribution runtime helpers."""\n',
            "runtime/passive_linkage.py": _require_file(
                passive_solver, "passive-linkage solver"
            ).read_bytes(),
            "runtime/passive_linkage_usd.py": _require_file(
                passive_usd, "passive-linkage USD helper"
            ).read_bytes(),
            "runtime/data/amazinghand_passive_linkage_keyframes.json": _require_file(
                passive_manifest, "passive-linkage keyframe manifest"
            ).read_bytes(),
            "validation/isaac-report.json": _require_file(
                runtime_report, "runtime validation report"
            ).read_bytes(),
            "validation/asset-validator.json": _require_file(
                validator_report, "strict validator report"
            ).read_bytes(),
            "validation/passive_linkage_contact_sheet.png": _require_file(
                preview_image, "reviewed passive-linkage contact sheet"
            ).read_bytes(),
        }
    )
    files["README.md"] = _readme(
        distribution_name=distribution_name,
        source_commit=source_commit,
        validation_run_id=validation_run_id,
        runtime_report=runtime,
    )
    manifest = _manifest(
        distribution_name=distribution_name,
        source_commit=source_commit,
        source_branch=source_branch,
        validation_run_id=validation_run_id,
        created_utc=created_utc,
        runtime_report=runtime,
        validator_report=validator,
        files=files,
    )
    files["manifest.json"] = _json_bytes(manifest)
    files["SHA256SUMS"] = "".join(
        f"{_sha256_bytes(body)}  {path}\n" for path, body in sorted(files.items())
    ).encode()

    _write_deterministic_zip(output_zip, distribution_name, files)
    return {
        "archive": str(output_zip),
        "archive_bytes": output_zip.stat().st_size,
        "archive_sha256": _sha256_bytes(output_zip.read_bytes()),
        "distribution_name": distribution_name,
        "entrypoint": ENTRYPOINT.as_posix(),
        "file_count": len(files),
        "manifest": manifest,
    }


def _git_value(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a verified SuperArm + AmazingHand Isaac Sim USD distribution."
    )
    parser.add_argument("--source-asset-dir", type=Path, required=True)
    parser.add_argument("--runtime-report", type=Path, required=True)
    parser.add_argument("--validator-report", type=Path, required=True)
    parser.add_argument("--preview-image", type=Path, required=True)
    parser.add_argument("--license-file", type=Path, default=Path("LICENSE"))
    parser.add_argument("--hand-license-file", type=Path, required=True)
    parser.add_argument("--passive-solver", type=Path, default=Path(__file__).with_name("passive_linkage.py"))
    parser.add_argument(
        "--passive-usd", type=Path, default=Path(__file__).with_name("passive_linkage_usd.py")
    )
    parser.add_argument(
        "--passive-manifest",
        type=Path,
        default=Path(__file__).with_name("data") / "amazinghand_passive_linkage_keyframes.json",
    )
    parser.add_argument("--output-zip", type=Path, required=True)
    parser.add_argument("--distribution-name", required=True)
    parser.add_argument("--validation-run-id", required=True)
    parser.add_argument("--source-commit", default=None)
    parser.add_argument("--source-branch", default=None)
    parser.add_argument("--created-utc", default=None)
    args = parser.parse_args()

    result = export_distribution(
        source_asset_dir=args.source_asset_dir,
        runtime_report=args.runtime_report,
        validator_report=args.validator_report,
        preview_image=args.preview_image,
        license_file=args.license_file,
        hand_license_file=args.hand_license_file,
        passive_solver=args.passive_solver,
        passive_usd=args.passive_usd,
        passive_manifest=args.passive_manifest,
        output_zip=args.output_zip,
        distribution_name=args.distribution_name,
        source_commit=args.source_commit or _git_value("rev-parse", "HEAD"),
        source_branch=args.source_branch or _git_value("branch", "--show-current"),
        validation_run_id=args.validation_run_id,
        created_utc=args.created_utc or datetime.now(UTC).replace(microsecond=0).isoformat(),
    )
    print(json.dumps({key: value for key, value in result.items() if key != "manifest"}, indent=2))


if __name__ == "__main__":
    main()
