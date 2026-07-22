"""Pure AmazingHand passive-linkage follower pose solver.

This module intentionally has no MuJoCo or USD dependency.  It consumes the
committed, checksum-locked keyframe manifest generated offline from the original
closed-loop hand and interpolates visual-only follower poses from measured Isaac
motor angles.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path(__file__).with_name("data") / "amazinghand_passive_linkage_keyframes.json"
EXPECTED_FINGER_COUNT = 4
EXPECTED_PARTS_PER_FINGER = 22
EXPECTED_PART_COUNT = EXPECTED_FINGER_COUNT * EXPECTED_PARTS_PER_FINGER
MAX_EQUALITY_SITE_PAIR_SEPARATION_M = 1e-6
MAX_MOTOR_TARGET_ERROR_RAD = 1e-4
KEYFRAME_NAMES = ("open", "half_close", "close")


@dataclass(frozen=True, slots=True)
class PassiveVisualPose:
    """Immutable wrist-local transform for one shell-free passive visual part."""

    finger: int
    source_index: int
    source_prim: str
    instance_prim: str
    mesh_role: str
    translate: tuple[float, float, float]
    orient: tuple[float, float, float, float]


def finger_closedness(motor1: float, motor2: float) -> float:
    """Return clamped closedness in [0, 1] from one finger's measured motor pair."""

    first = (motor1 - 0.05) / 0.90
    second = (motor2 - 0.02) / 1.08
    return min(1.0, max(0.0, (first + second) / 2.0))


def load_passive_linkage_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Load and strictly validate a passive-linkage keyframe manifest."""

    manifest = json.loads(path.read_text())
    validate_passive_linkage_manifest(manifest)
    return manifest


def validate_passive_linkage_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate the pure-solver contract for the committed keyframe manifest."""

    if manifest.get("manifest_version") != 1:
        raise ValueError("manifest_version must be 1")
    if manifest.get("finger_count") != EXPECTED_FINGER_COUNT:
        raise ValueError("finger_count must be 4")
    if manifest.get("parts_per_finger") != EXPECTED_PARTS_PER_FINGER:
        raise ValueError("parts_per_finger must be 22")
    if manifest.get("structural_visual_part_count") != EXPECTED_PART_COUNT:
        raise ValueError("structural_visual_part_count must be 88")

    solver = _mapping(manifest.get("solver"), "solver")
    max_separation = _finite_number(
        solver.get("max_equality_site_pair_separation_m"),
        "solver.max_equality_site_pair_separation_m",
    )
    if max_separation >= MAX_EQUALITY_SITE_PAIR_SEPARATION_M:
        raise ValueError(
            f"solver equality-site separation must be below {MAX_EQUALITY_SITE_PAIR_SEPARATION_M:g} m"
        )
    max_motor_error = _finite_number(
        solver.get("max_motor_target_error_rad"),
        "solver.max_motor_target_error_rad",
    )
    if max_motor_error >= MAX_MOTOR_TARGET_ERROR_RAD:
        raise ValueError(f"solver motor target error must be below {MAX_MOTOR_TARGET_ERROR_RAD:g} rad")

    keyframe_names = [
        _mapping(keyframe, "keyframes[]").get("name")
        for keyframe in _sequence(manifest.get("keyframes"), "keyframes")
    ]
    if keyframe_names != list(KEYFRAME_NAMES):
        raise ValueError(f"keyframes must be {list(KEYFRAME_NAMES)!r}")

    parts = _sequence(manifest.get("parts"), "parts")
    if len(parts) != EXPECTED_PART_COUNT:
        raise ValueError(f"parts must contain {EXPECTED_PART_COUNT} entries")

    seen_source_prims: set[str] = set()
    per_finger = dict.fromkeys(range(1, EXPECTED_FINGER_COUNT + 1), 0)
    for index, raw_part in enumerate(parts):
        part = _mapping(raw_part, f"parts[{index}]")
        finger = part.get("finger")
        if finger not in per_finger:
            raise ValueError(f"parts[{index}].finger must be one of 1..4")
        per_finger[finger] += 1

        source_prim = _string(part.get("source_prim"), f"parts[{index}].source_prim")
        if source_prim in seen_source_prims:
            raise ValueError(f"Duplicate source_prim: {source_prim}")
        seen_source_prims.add(source_prim)
        _string(part.get("instance_prim"), f"parts[{index}].instance_prim")
        _finite_number(part.get("source_index"), f"parts[{index}].source_index")

        _validate_optional_transform(part, "source_usd_rest_transform", f"parts[{index}]")
        _validate_optional_transform(
            part.get("raw_xml_geom_local", {}), None, f"parts[{index}].raw_xml_geom_local"
        )

        transforms = _mapping(part.get("transforms"), f"parts[{index}].transforms")
        if set(transforms) != set(KEYFRAME_NAMES):
            raise ValueError(f"parts[{index}].transforms must contain {KEYFRAME_NAMES!r}")
        for keyframe_name in KEYFRAME_NAMES:
            _validate_transform(
                _mapping(transforms.get(keyframe_name), f"parts[{index}].transforms.{keyframe_name}"),
                f"parts[{index}].transforms.{keyframe_name}",
            )

    if any(count != EXPECTED_PARTS_PER_FINGER for count in per_finger.values()):
        raise ValueError(f"Each finger must contain {EXPECTED_PARTS_PER_FINGER} parts: {per_finger}")


def solve_passive_linkage(measured: Mapping[str, float]) -> tuple[PassiveVisualPose, ...]:
    """Interpolate checked closed-loop visual keyframes from measured Isaac angles."""

    manifest = load_passive_linkage_manifest()
    closedness_by_finger = {
        finger: finger_closedness(
            _measured_joint(measured, f"finger{finger}_motor1"),
            _measured_joint(measured, f"finger{finger}_motor2"),
        )
        for finger in range(1, EXPECTED_FINGER_COUNT + 1)
    }

    poses: list[PassiveVisualPose] = []
    for part in manifest["parts"]:
        finger = int(part["finger"])
        transforms = part["transforms"]
        translate, orient = _interpolate_transforms(transforms, closedness_by_finger[finger])
        poses.append(
            PassiveVisualPose(
                finger=finger,
                source_index=int(part["source_index"]),
                source_prim=str(part["source_prim"]),
                instance_prim=str(part["instance_prim"]),
                mesh_role=str(part["mesh_role"]),
                translate=translate,
                orient=orient,
            )
        )
    return tuple(poses)


def _interpolate_transforms(
    transforms: Mapping[str, Mapping[str, object]], closedness: float
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    if closedness <= 0.0:
        transform = transforms["open"]
        return tuple(transform["translate_m"]), tuple(transform["orient_wxyz"])  # type: ignore[return-value]
    if closedness == 0.5:
        transform = transforms["half_close"]
        return tuple(transform["translate_m"]), tuple(transform["orient_wxyz"])  # type: ignore[return-value]
    if closedness >= 1.0:
        transform = transforms["close"]
        return tuple(transform["translate_m"]), tuple(transform["orient_wxyz"])  # type: ignore[return-value]

    if closedness < 0.5:
        start = transforms["open"]
        end = transforms["half_close"]
        alpha = closedness / 0.5
    else:
        start = transforms["half_close"]
        end = transforms["close"]
        alpha = (closedness - 0.5) / 0.5

    translate = _lerp3(tuple(start["translate_m"]), tuple(end["translate_m"]), alpha)  # type: ignore[arg-type]
    orient = _slerp(
        tuple(start["orient_wxyz"]),  # type: ignore[arg-type]
        tuple(end["orient_wxyz"]),  # type: ignore[arg-type]
        alpha,
    )
    return translate, orient


def _measured_joint(measured: Mapping[str, float], name: str) -> float:
    if name not in measured:
        raise ValueError(f"Missing measured joint: {name}")
    value = measured[name]
    if not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"Non-finite measured joint: {name}")
    return float(value)


def _lerp3(
    a: tuple[float, float, float], b: tuple[float, float, float], t: float
) -> tuple[float, float, float]:
    return tuple(a_i + (b_i - a_i) * t for a_i, b_i in zip(a, b, strict=True))  # type: ignore[return-value]


def _slerp(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float], t: float
) -> tuple[float, float, float, float]:
    qa = _normalized(a)
    qb = _normalized(b)
    dot = sum(a_i * b_i for a_i, b_i in zip(qa, qb, strict=True))
    if dot < 0.0:
        qb = tuple(-v for v in qb)  # type: ignore[assignment]
        dot = -dot
    dot = min(1.0, max(-1.0, dot))

    if dot > 0.9995:
        return _normalized(tuple(a_i + (b_i - a_i) * t for a_i, b_i in zip(qa, qb, strict=True)))

    theta_0 = math.acos(dot)
    sin_theta_0 = math.sin(theta_0)
    theta = theta_0 * t
    sin_theta = math.sin(theta)
    scale_a = math.cos(theta) - dot * sin_theta / sin_theta_0
    scale_b = sin_theta / sin_theta_0
    return _normalized(tuple(scale_a * a_i + scale_b * b_i for a_i, b_i in zip(qa, qb, strict=True)))


def _normalized(values: tuple[float, ...]) -> tuple[float, ...]:
    length = math.sqrt(sum(value * value for value in values))
    if not math.isfinite(length) or length == 0.0:
        raise ValueError("Cannot normalize quaternion")
    return tuple(value / length for value in values)


def _validate_optional_transform(container: Mapping[str, Any], key: str | None, label: str) -> None:
    if key is None:
        if container:
            _validate_transform(container, label)
        return
    value = container.get(key)
    if value is not None:
        _validate_transform(_mapping(value, f"{label}.{key}"), f"{label}.{key}")


def _validate_transform(transform: Mapping[str, Any], label: str) -> None:
    try:
        _tuple_of_finite_numbers(transform.get("translate_m"), 3, f"{label}.translate_m")
    except ValueError as exc:
        if "must be finite" in str(exc):
            raise ValueError(f"Non-finite translation in {label}") from exc
        raise
    orient = _tuple_of_finite_numbers(transform.get("orient_wxyz"), 4, f"{label}.orient_wxyz")
    norm = math.sqrt(sum(value * value for value in orient))
    if not math.isclose(norm, 1.0, abs_tol=1e-6):
        raise ValueError(f"Non-normalized quaternion in {label}: norm={norm}")


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _finite_number(value: Any, label: str) -> float:
    if not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _tuple_of_finite_numbers(value: Any, length: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, list | tuple) or len(value) != length:
        raise ValueError(f"{label} must contain {length} numbers")
    values = tuple(_finite_number(item, f"{label}[]") for item in value)
    if label.endswith("translate_m") and not all(math.isfinite(item) for item in values):
        raise ValueError(f"Non-finite translation in {label}")
    return values
