from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from isaacsim_validation.export_superarm_usd_distribution import export_distribution


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_inputs(root: Path) -> dict[str, Path]:
    asset = root / "asset"
    (asset / "payloads").mkdir(parents=True)
    (asset / "superarm_amazinghand.usda").write_text(
        '#usda 1.0\n(def Xform "superarm_amazinghand" (references = @./payloads/base.usda@) {})\n',
        encoding="utf-8",
    )
    (asset / "payloads" / "base.usda").write_text(
        '#usda 1.0\ndef Xform "superarm_amazinghand" {}\n',
        encoding="utf-8",
    )

    runtime_report = root / "isaac-report.json"
    runtime_report.write_text(
        json.dumps(
            {
                "status": "PASS",
                "isaac_sim_version": "6.0.0",
                "import": {
                    "dof_count": 13,
                    "dof_names": [
                        "joint_rev_1",
                        "joint_rev_2",
                        "joint_rev_3",
                        "joint_rev_4",
                        "joint_rev_5",
                        "finger1_motor1",
                        "finger2_motor1",
                        "finger3_motor1",
                        "finger4_motor1",
                        "finger1_motor2",
                        "finger2_motor2",
                        "finger3_motor2",
                        "finger4_motor2",
                    ],
                },
                "motion": {
                    "logical_action_width": 6,
                    "physical_movable_joint_count": 13,
                    "arm_motion_passed": True,
                    "hand_motion_passed": True,
                },
                "zip_hand_binding": {
                    "visual_mode": "frame_first_no_outer_shells",
                    "excluded_outer_shell_part_count": 8,
                },
                "passive_linkage_visuals": {"grasp_sequence": {"visual_part_count": 88, "passed": True}},
                "package_cleanup": {"restored_pristine_root_layer_after_runtime": True},
                "visual_boundary": "Visual-only passive linkage; no contact or hardware proof.",
            }
        ),
        encoding="utf-8",
    )
    validator_report = root / "asset-validator.json"
    validator_report.write_text(
        json.dumps(
            {
                "runtime": "nvcr.io/nvidia/isaac-sim:6.0.0",
                "articulation_root_count": 1,
                "revolute_joint_count": 13,
                "verdict": {"passed": True, "blocking_issue_count": 0},
            }
        ),
        encoding="utf-8",
    )
    preview = root / "preview.png"
    preview.write_bytes(b"PNG")
    visual_images = {}
    for name in ("whole", "open", "half-close", "close"):
        image = root / f"{name}.png"
        image.write_bytes(f"PNG:{name}".encode())
        visual_images[name] = image
    license_file = root / "LICENSE"
    license_file.write_text("project license\n", encoding="utf-8")
    hand_license = root / "LICENSE-AmazingHandControl"
    hand_license.write_text("hand license\n", encoding="utf-8")

    runtime_solver = root / "passive_linkage.py"
    runtime_solver.write_text("def solve_passive_linkage(measured): return ()\n", encoding="utf-8")
    runtime_usd = root / "passive_linkage_usd.py"
    runtime_usd.write_text(
        "\n".join(
            (
                "def author_passive_linkage_snapshot(*args): return {}",
                "def author_or_update_passive_linkage_runtime(*args): return {}",
                "",
            )
        ),
        encoding="utf-8",
    )
    runtime_data = root / "amazinghand_passive_linkage_keyframes.json"
    runtime_data.write_text('{"manifest_version": 1}\n', encoding="utf-8")

    return {
        "source_asset_dir": asset,
        "runtime_report": runtime_report,
        "validator_report": validator_report,
        "preview_image": preview,
        "visual_images": visual_images,
        "license_file": license_file,
        "hand_license_file": hand_license,
        "passive_solver": runtime_solver,
        "passive_usd": runtime_usd,
        "passive_manifest": runtime_data,
    }


def test_export_is_relocatable_complete_and_deterministic(tmp_path: Path):
    inputs = _write_inputs(tmp_path)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    kwargs = {
        **inputs,
        "distribution_name": "superarm_amazinghand_isaac_sim_usd_distribution_20260722",
        "source_commit": "0cb323ef6a3ee121774035b698d8298f64270274",
        "source_branch": "feature/isaacsim-superarm-urdf-validation",
        "validation_run_id": "20260722T070208Z-combined-zip-passive-linkage-r3",
        "created_utc": "2026-07-22T08:00:00Z",
    }

    first_result = export_distribution(output_zip=first, **kwargs)
    second_result = export_distribution(output_zip=second, **kwargs)

    assert first.read_bytes() == second.read_bytes()
    assert first_result["archive_sha256"] == second_result["archive_sha256"]
    assert first_result["archive_sha256"] == _sha256(first.read_bytes())

    root = kwargs["distribution_name"]
    with zipfile.ZipFile(first) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert names == sorted(names)
        assert all(name.startswith(f"{root}/") for name in names)
        assert all(".." not in Path(name).parts for name in names)
        expected = {
            f"{root}/README.md",
            f"{root}/manifest.json",
            f"{root}/SHA256SUMS",
            f"{root}/LICENSE",
            f"{root}/LICENSE-AmazingHandControl",
            f"{root}/usd/superarm_amazinghand/superarm_amazinghand.usda",
            f"{root}/usd/superarm_amazinghand/payloads/base.usda",
            f"{root}/python/superarm_isaac_runtime/__init__.py",
            f"{root}/python/superarm_isaac_runtime/passive_linkage.py",
            f"{root}/python/superarm_isaac_runtime/passive_linkage_usd.py",
            f"{root}/python/superarm_isaac_runtime/data/amazinghand_passive_linkage_keyframes.json",
            f"{root}/validation/isaac-report.json",
            f"{root}/validation/asset-validator.json",
            f"{root}/validation/passive_linkage_contact_sheet.png",
            f"{root}/validation/visuals/whole.png",
            f"{root}/validation/visuals/open.png",
            f"{root}/validation/visuals/half-close.png",
            f"{root}/validation/visuals/close.png",
        }
        assert expected <= set(names)

        manifest = json.loads(archive.read(f"{root}/manifest.json"))
        assert manifest["schema"] == "superarm.isaac_sim.usd_distribution/v2"
        assert manifest["entrypoint"] == "usd/superarm_amazinghand/superarm_amazinghand.usda"
        assert manifest["runtime"]["isaac_sim_version"] == "6.0.0"
        assert manifest["robot_contract"] == {
            "articulation_root_count": 1,
            "arm_dof_count": 5,
            "hand_dof_count": 8,
            "logical_action_width": 6,
            "physical_dof_count": 13,
        }
        assert manifest["validation"]["runtime_status"] == "PASS"
        assert manifest["validation"]["strict_validator_passed"] is True
        assert manifest["visual_contract"]["outer_shells_included"] is False
        assert manifest["visual_contract"]["passive_follower_count"] == 88
        assert manifest["visual_contract"]["profile"] == (
            "superarm_isaac60_passive_linkage_no_shell/v1"
        )
        assert manifest["visual_contract"]["runtime"] == {
            "instances": "usd/superarm_amazinghand/zip_hand_payloads/instances.usda",
            "keyframes": (
                "python/superarm_isaac_runtime/data/"
                "amazinghand_passive_linkage_keyframes.json"
            ),
            "package": "superarm_isaac_runtime",
            "python_root": "python",
            "solver": "superarm_isaac_runtime.passive_linkage:solve_passive_linkage",
            "usd_author": (
                "superarm_isaac_runtime.passive_linkage_usd:"
                "author_or_update_passive_linkage_runtime"
            ),
        }
        assert manifest["grasp_contract"] == {
            "full_close_simulation_only": True,
            "real_hardware_max_code": 0.5,
            "real_hardware_max_pose": "half-close",
            "simulation_codes": [0.0, 0.5, 1.0],
        }
        assert not any(name.startswith(f"{root}/runtime/") for name in names)
        for name, source in inputs["visual_images"].items():
            entry = manifest["visual_evidence"][name]
            assert entry == {
                "bytes": source.stat().st_size,
                "path": f"validation/visuals/{name}.png",
                "sha256": _sha256(source.read_bytes()),
            }

        checksums = archive.read(f"{root}/SHA256SUMS").decode().splitlines()
        checksum_by_name = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in checksums}
        checked_names = {name.removeprefix(f"{root}/") for name in names}
        checked_names.remove("SHA256SUMS")
        assert set(checksum_by_name) == checked_names
        for relative_name, digest in checksum_by_name.items():
            assert _sha256(archive.read(f"{root}/{relative_name}")) == digest


def test_export_rejects_external_usd_asset_reference(tmp_path: Path):
    inputs = _write_inputs(tmp_path)
    (inputs["source_asset_dir"] / "superarm_amazinghand.usda").write_text(
        '#usda 1.0\ndef Xform "robot" (references = @/tmp/external.usd@) {}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="external USD asset reference"):
        export_distribution(
            output_zip=tmp_path / "unsafe.zip",
            distribution_name="superarm_distribution",
            source_commit="0cb323e",
            source_branch="feature/test",
            validation_run_id="test-run",
            created_utc="2026-07-22T08:00:00Z",
            **inputs,
        )


def test_export_rejects_unproven_validation_reports(tmp_path: Path):
    inputs = _write_inputs(tmp_path)
    inputs["runtime_report"].write_text('{"status": "FAIL"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="runtime validation report must be PASS"):
        export_distribution(
            output_zip=tmp_path / "failed.zip",
            distribution_name="superarm_distribution",
            source_commit="0cb323e",
            source_branch="feature/test",
            validation_run_id="test-run",
            created_utc="2026-07-22T08:00:00Z",
            **inputs,
        )
