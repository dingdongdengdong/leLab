from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path

import pytest

from lelab.superarm.isaac_distribution import (
    DISTRIBUTION_SCHEMA,
    validate_and_extract_distribution,
)


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _valid_distribution(
    path: Path,
    *,
    schema: str = DISTRIBUTION_SCHEMA,
    contract: dict[str, int] | None = None,
    hand_joint_names: list[str] | None = None,
) -> Path:
    root = "superarm_test_distribution"
    entrypoint = "usd/superarm_amazinghand/superarm_amazinghand.usda"
    files = {
        entrypoint: b'#usda 1.0\n(defaultPrim = "superarm_amazinghand")\n',
        "usd/superarm_amazinghand/payloads/base.usda": b'#usda 1.0\ndef Xform "robot" {}\n',
        "README.md": b"test distribution\n",
        **{
            f"validation/visuals/{name}.png": f"PNG:{name}".encode()
            for name in ("whole", "open", "half-close", "close")
        },
    }
    manifest = {
        "schema": schema,
        "name": root,
        "entrypoint": entrypoint,
        "robot_contract": contract
        or {
            "articulation_root_count": 1,
            "arm_dof_count": 5,
            "hand_dof_count": 8,
            "physical_dof_count": 13,
            "logical_action_width": 6,
        },
        "joint_names": {
            "arm": [f"joint_rev_{index}" for index in range(1, 6)],
            "hand": hand_joint_names
            or [
                f"finger{finger}_motor{motor}"
                for finger in range(1, 5)
                for motor in range(1, 3)
            ],
            "physical_order": [
                *[f"joint_rev_{index}" for index in range(1, 6)],
                *(hand_joint_names or [
                    f"finger{finger}_motor{motor}"
                    for finger in range(1, 5)
                    for motor in range(1, 3)
                ]),
            ],
        },
        "files": [
            {"path": name, "bytes": len(body), "sha256": _sha256(body)}
            for name, body in sorted(files.items())
        ],
        "visual_evidence": {
            name: {
                "path": f"validation/visuals/{name}.png",
                "bytes": len(files[f"validation/visuals/{name}.png"]),
                "sha256": _sha256(files[f"validation/visuals/{name}.png"]),
            }
            for name in ("whole", "open", "half-close", "close")
        },
    }
    files["manifest.json"] = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    files["SHA256SUMS"] = "".join(
        f"{_sha256(body)}  {name}\n" for name, body in sorted(files.items())
    ).encode()

    with zipfile.ZipFile(path, "w") as archive:
        for name, body in sorted(files.items()):
            info = zipfile.ZipInfo(f"{root}/{name}")
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, body)
    return path


def test_distribution_is_verified_and_extracted_by_archive_digest(tmp_path: Path):
    source = _valid_distribution(tmp_path / "distribution.zip")

    first = validate_and_extract_distribution(source, cache_root=tmp_path / "cache")
    second = validate_and_extract_distribution(source, cache_root=tmp_path / "cache")

    assert first == second
    assert first.archive_sha256 == _sha256(source.read_bytes())
    assert first.root.name == "superarm_test_distribution"
    assert first.entrypoint == first.root / "usd/superarm_amazinghand/superarm_amazinghand.usda"
    assert first.entrypoint.is_file()
    assert first.robot_contract["physical_dof_count"] == 13
    assert first.robot_contract["logical_action_width"] == 6


def test_distribution_can_require_a_trusted_archive_digest(tmp_path: Path):
    source = _valid_distribution(tmp_path / "distribution.zip")

    validate_and_extract_distribution(
        source,
        cache_root=tmp_path / "cache",
        expected_sha256=_sha256(source.read_bytes()),
    )
    with pytest.raises(ValueError, match="archive SHA256"):
        validate_and_extract_distribution(
            source,
            cache_root=tmp_path / "other-cache",
            expected_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    ("member_name", "error"),
    [
        ("../escape", "unsafe"),
        ("/absolute", "unsafe"),
        ("root\\windows", "unsafe"),
    ],
)
def test_distribution_rejects_unsafe_member_paths(tmp_path: Path, member_name: str, error: str):
    source = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(member_name, b"unsafe")

    with pytest.raises(ValueError, match=error):
        validate_and_extract_distribution(source, cache_root=tmp_path / "cache")


def test_distribution_rejects_symlink_members(tmp_path: Path):
    source = tmp_path / "symlink.zip"
    with zipfile.ZipFile(source, "w") as archive:
        info = zipfile.ZipInfo("root/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, b"target")

    with pytest.raises(ValueError, match="symlink"):
        validate_and_extract_distribution(source, cache_root=tmp_path / "cache")


def test_distribution_rejects_non_regular_members(tmp_path: Path):
    source = tmp_path / "device.zip"
    with zipfile.ZipFile(source, "w") as archive:
        info = zipfile.ZipInfo("root/device")
        info.create_system = 3
        info.external_attr = (stat.S_IFCHR | 0o600) << 16
        archive.writestr(info, b"device")

    with pytest.raises(ValueError, match="regular file"):
        validate_and_extract_distribution(source, cache_root=tmp_path / "cache")


def test_distribution_rejects_duplicate_normalized_members(tmp_path: Path):
    source = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("root/file", b"one")
        archive.writestr("root/./file", b"two")

    with pytest.raises(ValueError, match="duplicate"):
        validate_and_extract_distribution(source, cache_root=tmp_path / "cache")


def test_distribution_rejects_checksum_mismatch(tmp_path: Path):
    source = _valid_distribution(tmp_path / "distribution.zip")
    rewritten = tmp_path / "tampered.zip"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(rewritten, "w") as archive:
        for info in original.infolist():
            body = original.read(info)
            if info.filename.endswith("README.md"):
                body = b"tampered\n"
            archive.writestr(info, body)

    with pytest.raises(ValueError, match="checksum"):
        validate_and_extract_distribution(rewritten, cache_root=tmp_path / "cache")


def test_distribution_rejects_manifest_inventory_that_disagrees_with_sha256sums(
    tmp_path: Path,
):
    source = _valid_distribution(tmp_path / "distribution.zip")
    rewritten = tmp_path / "manifest-lies.zip"
    with zipfile.ZipFile(source) as original:
        root = original.namelist()[0].split("/", 1)[0]
        files = {
            info.filename.removeprefix(f"{root}/"): original.read(info)
            for info in original.infolist()
        }
    manifest = json.loads(files["manifest.json"])
    manifest["files"][0]["sha256"] = "0" * 64
    files["manifest.json"] = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    files["SHA256SUMS"] = "".join(
        f"{_sha256(body)}  {name}\n"
        for name, body in sorted(files.items())
        if name != "SHA256SUMS"
    ).encode()
    with zipfile.ZipFile(rewritten, "w") as archive:
        for name, body in sorted(files.items()):
            archive.writestr(f"{root}/{name}", body)

    with pytest.raises(ValueError, match="manifest file inventory"):
        validate_and_extract_distribution(rewritten, cache_root=tmp_path / "cache")


def test_distribution_rejects_self_consistent_archive_with_incomplete_visual_contract(
    tmp_path: Path,
):
    source = _valid_distribution(tmp_path / "distribution.zip")
    rewritten = tmp_path / "missing-visual-role.zip"
    with zipfile.ZipFile(source) as original:
        root = original.namelist()[0].split("/", 1)[0]
        files = {
            info.filename.removeprefix(f"{root}/"): original.read(info)
            for info in original.infolist()
        }
    manifest = json.loads(files["manifest.json"])
    del manifest["visual_evidence"]["close"]
    files["manifest.json"] = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    files["SHA256SUMS"] = "".join(
        f"{_sha256(body)}  {name}\n"
        for name, body in sorted(files.items())
        if name != "SHA256SUMS"
    ).encode()
    with zipfile.ZipFile(rewritten, "w") as archive:
        for name, body in sorted(files.items()):
            archive.writestr(f"{root}/{name}", body)

    with pytest.raises(ValueError, match="visual evidence"):
        validate_and_extract_distribution(rewritten, cache_root=tmp_path / "cache")


@pytest.mark.parametrize(
    ("schema", "contract", "error"),
    [
        ("wrong/v1", None, "schema"),
        (
            DISTRIBUTION_SCHEMA,
            {
                "articulation_root_count": 1,
                "arm_dof_count": 5,
                "hand_dof_count": 8,
                "physical_dof_count": 12,
                "logical_action_width": 6,
            },
            "contract",
        ),
    ],
)
def test_distribution_rejects_wrong_manifest_contract(
    tmp_path: Path,
    schema: str,
    contract: dict[str, int] | None,
    error: str,
):
    source = _valid_distribution(tmp_path / "distribution.zip", schema=schema, contract=contract)

    with pytest.raises(ValueError, match=error):
        validate_and_extract_distribution(source, cache_root=tmp_path / "cache")


def test_distribution_rejects_renamed_physical_joints(tmp_path: Path):
    hand = [
        f"finger{finger}_motor{motor}"
        for finger in range(1, 5)
        for motor in range(1, 3)
    ]
    hand[-1] = "finger4_wrong"
    source = _valid_distribution(tmp_path / "distribution.zip", hand_joint_names=hand)

    with pytest.raises(ValueError, match="joint names"):
        validate_and_extract_distribution(source, cache_root=tmp_path / "cache")


def test_distribution_rejects_excessive_uncompressed_size(tmp_path: Path):
    source = tmp_path / "oversized.zip"
    with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("root/huge", b"0" * 1_100_000)

    with pytest.raises(ValueError, match="uncompressed size|compression ratio"):
        validate_and_extract_distribution(
            source,
            cache_root=tmp_path / "cache",
            max_uncompressed_bytes=1_000_000,
        )


def test_distribution_rechecks_cached_files_before_reuse(tmp_path: Path):
    source = _valid_distribution(tmp_path / "distribution.zip")
    extracted = validate_and_extract_distribution(source, cache_root=tmp_path / "cache")
    extracted.entrypoint.write_text("corrupted", encoding="utf-8")

    repaired = validate_and_extract_distribution(source, cache_root=tmp_path / "cache")

    assert repaired.entrypoint.read_bytes().startswith(b"#usda 1.0")
