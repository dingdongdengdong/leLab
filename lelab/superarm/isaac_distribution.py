"""Validate and safely cache a SuperArm Isaac Sim USD distribution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from isaacsim_validation.contracts import ARM_JOINTS, HAND_JOINTS, PHYSICAL_JOINTS

DISTRIBUTION_SCHEMA = "superarm.isaac_sim.usd_distribution/v1"
EXPECTED_ROBOT_CONTRACT = {
    "articulation_root_count": 1,
    "arm_dof_count": 5,
    "hand_dof_count": 8,
    "physical_dof_count": 13,
    "logical_action_width": 6,
}
DEFAULT_MAX_MEMBERS = 2_048
DEFAULT_MAX_UNCOMPRESSED_BYTES = 1_073_741_824
DEFAULT_MAX_MEMBER_BYTES = 536_870_912
DEFAULT_MAX_COMPRESSION_RATIO = 500.0


@dataclass(frozen=True)
class IsaacDistribution:
    archive_sha256: str
    root: Path
    entrypoint: Path
    manifest: dict[str, Any]
    robot_contract: dict[str, int]


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _normalized_member(info: zipfile.ZipInfo) -> PurePosixPath:
    name = info.filename
    if not name or "\\" in name:
        raise ValueError(f"unsafe distribution member path: {name!r}")
    path = PurePosixPath(name)
    normalized = PurePosixPath(*[part for part in path.parts if part not in {"", "."}])
    if path.is_absolute() or ".." in path.parts or not normalized.parts:
        raise ValueError(f"unsafe distribution member path: {name!r}")
    mode = info.external_attr >> 16
    if stat.S_IFMT(mode) == stat.S_IFLNK:
        raise ValueError(f"distribution member must not be a symlink: {name}")
    if stat.S_IFMT(mode) not in {0, stat.S_IFREG}:
        raise ValueError(f"distribution member must be a regular file: {name}")
    return normalized


def _validated_members(
    archive: zipfile.ZipFile,
    *,
    max_members: int,
    max_uncompressed_bytes: int,
    max_member_bytes: int,
    max_compression_ratio: float,
) -> tuple[str, dict[str, zipfile.ZipInfo]]:
    infos = [info for info in archive.infolist() if not info.is_dir()]
    if not infos or len(infos) > max_members:
        raise ValueError(f"distribution member count must be within [1, {max_members}]")
    total = 0
    members: dict[str, zipfile.ZipInfo] = {}
    roots: set[str] = set()
    for info in infos:
        normalized = _normalized_member(info)
        name = normalized.as_posix()
        if name in members:
            raise ValueError(f"duplicate normalized distribution member: {name}")
        roots.add(normalized.parts[0])
        total += info.file_size
        if info.file_size > max_member_bytes:
            raise ValueError(f"distribution member exceeds uncompressed size limit: {name}")
        if total > max_uncompressed_bytes:
            raise ValueError("distribution exceeds total uncompressed size limit")
        if info.compress_size and info.file_size / info.compress_size > max_compression_ratio:
            raise ValueError(f"distribution member exceeds compression ratio limit: {name}")
        members[name] = info
    if len(roots) != 1:
        raise ValueError(f"distribution must contain exactly one root directory, got {sorted(roots)}")
    return roots.pop(), members


def _read_json_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, label: str) -> dict[str, Any]:
    try:
        value = json.loads(archive.read(info))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"distribution {label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"distribution {label} must be a JSON object")
    return value


def _checksum_map(body: bytes) -> dict[str, str]:
    try:
        lines = body.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("distribution SHA256SUMS must be UTF-8") from exc
    checksums: dict[str, str] = {}
    for line in lines:
        try:
            digest, relative = line.split("  ", 1)
        except ValueError as exc:
            raise ValueError("distribution SHA256SUMS contains a malformed line") from exc
        path = PurePosixPath(relative)
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or path.is_absolute()
            or ".." in path.parts
            or relative in checksums
        ):
            raise ValueError("distribution SHA256SUMS contains an unsafe or duplicate entry")
        checksums[relative] = digest
    return checksums


def _validate_manifest(manifest: dict[str, Any]) -> tuple[str, dict[str, int]]:
    if manifest.get("schema") != DISTRIBUTION_SCHEMA:
        raise ValueError(f"distribution schema must be {DISTRIBUTION_SCHEMA}")
    contract = manifest.get("robot_contract")
    if contract != EXPECTED_ROBOT_CONTRACT:
        raise ValueError(f"distribution robot contract mismatch: {contract!r}")
    joint_names = manifest.get("joint_names")
    if not isinstance(joint_names, dict):
        raise ValueError("distribution manifest is missing joint names")
    if set(joint_names.get("arm") or []) != set(ARM_JOINTS) or set(
        joint_names.get("hand") or []
    ) != set(HAND_JOINTS):
        raise ValueError("distribution joint names do not match the 13-joint contract")
    physical_order = joint_names.get("physical_order")
    if (
        not isinstance(physical_order, list)
        or len(physical_order) != len(set(physical_order))
        or set(physical_order) != set(PHYSICAL_JOINTS)
    ):
        raise ValueError("distribution physical joint names do not match the 13-joint contract")
    entrypoint = manifest.get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise ValueError("distribution manifest is missing its entrypoint")
    path = PurePosixPath(entrypoint)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("distribution manifest entrypoint is unsafe")
    return path.as_posix(), dict(contract)


def _verify_extracted(root: Path, checksums: dict[str, str]) -> bool:
    return all(
        (path := root / PurePosixPath(relative)).is_file()
        and _sha256_bytes(path.read_bytes()) == digest
        for relative, digest in checksums.items()
    )


def validate_and_extract_distribution(
    source_zip: str | Path,
    *,
    cache_root: str | Path | None = None,
    expected_sha256: str | None = None,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_uncompressed_bytes: int = DEFAULT_MAX_UNCOMPRESSED_BYTES,
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> IsaacDistribution:
    """Verify an archive and atomically extract it into a digest-keyed cache."""

    source = Path(source_zip).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"SuperArm Isaac distribution is missing: {source}")
    archive_sha256 = _sha256_bytes(source.read_bytes())
    if expected_sha256 is not None and archive_sha256.lower() != expected_sha256.lower():
        raise ValueError(
            f"distribution archive SHA256 mismatch: expected {expected_sha256}, got {archive_sha256}"
        )

    with zipfile.ZipFile(source) as archive:
        root_name, members = _validated_members(
            archive,
            max_members=max_members,
            max_uncompressed_bytes=max_uncompressed_bytes,
            max_member_bytes=max_member_bytes,
            max_compression_ratio=max_compression_ratio,
        )
        manifest_name = f"{root_name}/manifest.json"
        sums_name = f"{root_name}/SHA256SUMS"
        if manifest_name not in members or sums_name not in members:
            raise ValueError("distribution must contain manifest.json and SHA256SUMS")
        manifest = _read_json_member(archive, members[manifest_name], "manifest")
        entrypoint_relative, robot_contract = _validate_manifest(manifest)
        checksums = _checksum_map(archive.read(members[sums_name]))
        relative_members = {
            name.removeprefix(f"{root_name}/")
            for name in members
            if name != sums_name
        }
        if set(checksums) != relative_members:
            raise ValueError("distribution checksum inventory does not match archive members")
        for relative, expected in checksums.items():
            actual = _sha256_bytes(archive.read(members[f"{root_name}/{relative}"]))
            if actual != expected:
                raise ValueError(f"distribution checksum mismatch: {relative}")
        if entrypoint_relative not in relative_members:
            raise ValueError("distribution manifest entrypoint is missing from the archive")

        cache = Path(cache_root or Path.home() / ".cache/lelab/superarm_isaac").expanduser()
        cache.mkdir(parents=True, exist_ok=True)
        destination = cache / archive_sha256
        extracted_root = destination / root_name
        if destination.exists() and not _verify_extracted(extracted_root, checksums):
            shutil.rmtree(destination)
        if not destination.exists():
            temporary = Path(tempfile.mkdtemp(prefix=f".{archive_sha256}-", dir=cache))
            try:
                temporary_root = temporary / root_name
                for name, info in members.items():
                    relative = PurePosixPath(name).relative_to(root_name)
                    output = temporary_root / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(archive.read(info))
                if not _verify_extracted(temporary_root, checksums):
                    raise ValueError("distribution extracted checksum verification failed")
                try:
                    os.replace(temporary, destination)
                except FileExistsError:
                    if not _verify_extracted(extracted_root, checksums):
                        raise
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)

    entrypoint = extracted_root / PurePosixPath(entrypoint_relative)
    return IsaacDistribution(
        archive_sha256=archive_sha256,
        root=extracted_root,
        entrypoint=entrypoint,
        manifest=manifest,
        robot_contract=robot_contract,
    )
