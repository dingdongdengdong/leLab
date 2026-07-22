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

EXPECTED_SOURCE_MJCF_SHA256 = "d21366e7c9a1f5debe04b8abb5ea1ade7fade42e493e09d003f5db196548b098"
EXPECTED_SOURCE_HAND_ZIP_SHA256 = "3230fb5ad2c8e50a843a14553ef17a587f40428abd63a025483c34f1c8e3d377"
EXPECTED_SOURCE_PACKAGE_ZIP_SHA256 = "c10c91ac240ac18893ab0a102e2ac6f9aa8a6a2e75c738fe6209f2d50a122b4a"
EXPECTED_EXCLUDED_SHELL_INDICES = (45, 51, 78, 85, 114, 115, 144, 152)
EXPECTED_STRUCTURAL_SOURCE_INDICES = (
    26,
    28,
    29,
    30,
    31,
    32,
    33,
    35,
    37,
    39,
    41,
    44,
    46,
    49,
    50,
    52,
    53,
    54,
    56,
    57,
    58,
    59,
    60,
    62,
    63,
    64,
    65,
    66,
    67,
    69,
    71,
    72,
    75,
    76,
    80,
    81,
    84,
    86,
    88,
    89,
    90,
    91,
    92,
    93,
    94,
    95,
    97,
    98,
    99,
    100,
    101,
    102,
    104,
    106,
    109,
    112,
    113,
    117,
    118,
    120,
    121,
    122,
    124,
    125,
    126,
    127,
    128,
    129,
    131,
    132,
    133,
    134,
    135,
    136,
    139,
    140,
    142,
    146,
    147,
    150,
    151,
    153,
    156,
    157,
    158,
    159,
    160,
    161,
)
EXPECTED_STRUCTURAL_SOURCE_PRIMS = (
    "mjcf_026_custom_servo_horn_rotule_ball_0",
    "mjcf_028_custom_servo_horn_custom_servo_horn_2",
    "mjcf_029_ball_link_rotule_lever_0",
    "mjcf_030_ball_link_rotule_lever_1",
    "mjcf_031_ball_link_m2_rod_l18_2",
    "mjcf_032_ball_link_spacer_3",
    "mjcf_033_rotule_ball_link_0",
    "mjcf_035_rotule_ball_rotule_ball_2",
    "mjcf_037_rotule_ball_rotule_ball_4",
    "mjcf_039_std00333_plast_tcb_torx_2_5x8__configuration_copy_of_default_gimbal_1",
    "mjcf_041_std00333_plast_tcb_torx_2_5x8__configuration_copy_of_default_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_3",
    "mjcf_044_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_distal_2",
    "mjcf_046_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_4",
    "mjcf_049_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_2",
    "mjcf_050_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_3",
    "mjcf_052_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_proximal_5",
    "mjcf_053_rotule_ball_2_custom_servo_horn_0",
    "mjcf_054_rotule_ball_2_rotule_ball_1",
    "mjcf_056_m2_rod_l18_rotule_lever_0",
    "mjcf_057_m2_rod_l18_spacer_1",
    "mjcf_058_m2_rod_l18_m2_rod_l18_2",
    "mjcf_059_m2_rod_l18_rotule_lever_3",
    "mjcf_060_custom_servo_horn_2_rotule_ball_0",
    "mjcf_062_custom_servo_horn_2_custom_servo_horn_2",
    "mjcf_063_ball_link_2_rotule_lever_0",
    "mjcf_064_ball_link_2_rotule_lever_1",
    "mjcf_065_ball_link_2_m2_rod_l18_2",
    "mjcf_066_ball_link_2_spacer_3",
    "mjcf_067_rotule_ball_3_rotule_ball_0",
    "mjcf_069_rotule_ball_3_link_2",
    "mjcf_071_rotule_ball_3_rotule_ball_4",
    "mjcf_072_std00333_plast_tcb_torx_2_5x8__configuration_copy_of_default_2_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_0",
    "mjcf_075_std00333_plast_tcb_torx_2_5x8__configuration_copy_of_default_2_gimbal_3",
    "mjcf_076_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_2_distal_0",
    "mjcf_080_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_2_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_4",
    "mjcf_081_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_2_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_0",
    "mjcf_084_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_2_proximal_3",
    "mjcf_086_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_2_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_5",
    "mjcf_088_rotule_ball_4_rotule_ball_1",
    "mjcf_089_rotule_ball_4_custom_servo_horn_2",
    "mjcf_090_ball_link_3_rotule_lever_0",
    "mjcf_091_ball_link_3_rotule_lever_1",
    "mjcf_092_ball_link_3_m2_rod_l18_2",
    "mjcf_093_ball_link_3_spacer_3",
    "mjcf_094_custom_servo_horn_3_rotule_ball_0",
    "mjcf_095_custom_servo_horn_3_custom_servo_horn_1",
    "mjcf_097_ball_link_4_rotule_lever_0",
    "mjcf_098_ball_link_4_rotule_lever_1",
    "mjcf_099_ball_link_4_m2_rod_l18_2",
    "mjcf_100_ball_link_4_spacer_3",
    "mjcf_101_rotule_ball_5_rotule_ball_0",
    "mjcf_102_rotule_ball_5_rotule_ball_1",
    "mjcf_104_rotule_ball_5_link_3",
    "mjcf_106_std00333_plast_tcb_torx_2_5x8__configuration_copy_of_default_3_gimbal_0",
    "mjcf_109_std00333_plast_tcb_torx_2_5x8__configuration_copy_of_default_3_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_3",
    "mjcf_112_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_3_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_2",
    "mjcf_113_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_3_distal_3",
    "mjcf_117_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_3_proximal_2",
    "mjcf_118_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_3_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_3",
    "mjcf_120_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_3_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_5",
    "mjcf_121_rotule_ball_6_rotule_ball_0",
    "mjcf_122_rotule_ball_6_custom_servo_horn_1",
    "mjcf_124_ball_link_5_rotule_lever_0",
    "mjcf_125_ball_link_5_rotule_lever_1",
    "mjcf_126_ball_link_5_m2_rod_l18_2",
    "mjcf_127_ball_link_5_spacer_3",
    "mjcf_128_custom_servo_horn_4_rotule_ball_0",
    "mjcf_129_custom_servo_horn_4_custom_servo_horn_1",
    "mjcf_131_ball_link_6_rotule_lever_0",
    "mjcf_132_ball_link_6_rotule_lever_1",
    "mjcf_133_ball_link_6_m2_rod_l18_2",
    "mjcf_134_ball_link_6_spacer_3",
    "mjcf_135_rotule_ball_7_rotule_ball_0",
    "mjcf_136_rotule_ball_7_link_1",
    "mjcf_139_rotule_ball_7_rotule_ball_4",
    "mjcf_140_std00333_plast_tcb_torx_2_5x8__configuration_copy_of_default_4_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_0",
    "mjcf_142_std00333_plast_tcb_torx_2_5x8__configuration_copy_of_default_4_gimbal_2",
    "mjcf_146_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_4_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_2",
    "mjcf_147_parallel_pin_2_x_10__fee063fca0c8b40e46bbc4ffff61d999_4_distal_3",
    "mjcf_150_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_4_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_1",
    "mjcf_151_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_4_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_2",
    "mjcf_153_parallel_pin_2_x_16__da4b7ddbe9d803fe3fbc70f2e822b99b_4_proximal_4",
    "mjcf_156_rotule_ball_8_rotule_ball_1",
    "mjcf_157_rotule_ball_8_custom_servo_horn_2",
    "mjcf_158_ball_link_7_rotule_lever_0",
    "mjcf_159_ball_link_7_rotule_lever_1",
    "mjcf_160_ball_link_7_m2_rod_l18_2",
    "mjcf_161_ball_link_7_spacer_3",
)
FORBIDDEN_SOURCE_NAME_FRAGMENTS = ("proximal_shell", "distal_shell")
FORBIDDEN_DECORATIVE_ROLES = frozenset({"screw", "washer", "std_fastener"})


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

    _require_exact_field(manifest, "source_mjcf_sha256", EXPECTED_SOURCE_MJCF_SHA256)
    _require_exact_field(manifest, "source_hand_zip_sha256", EXPECTED_SOURCE_HAND_ZIP_SHA256)
    _require_exact_field(manifest, "source_package_zip_sha256", EXPECTED_SOURCE_PACKAGE_ZIP_SHA256)
    excluded_shell_indices = tuple(
        _integer(item, "excluded_shell_indices[]", noun="excluded_shell_indices")
        for item in _sequence(manifest.get("excluded_shell_indices"), "excluded_shell_indices")
    )
    if excluded_shell_indices != EXPECTED_EXCLUDED_SHELL_INDICES:
        raise ValueError("excluded_shell_indices must match the locked shell-free manifest contract")

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

    seen_source_indices: set[int] = set()
    seen_source_prims: set[str] = set()
    actual_source_indices: list[int] = []
    actual_source_prims: list[str] = []
    per_finger = dict.fromkeys(range(1, EXPECTED_FINGER_COUNT + 1), 0)
    for index, raw_part in enumerate(parts):
        part = _mapping(raw_part, f"parts[{index}]")
        finger = _integer(part.get("finger"), f"parts[{index}].finger", noun="finger")
        if finger not in per_finger:
            raise ValueError(f"parts[{index}].finger must be one of 1..4")
        per_finger[finger] += 1

        source_index = _integer(part.get("source_index"), f"parts[{index}].source_index")
        if source_index in seen_source_indices:
            raise ValueError(f"Duplicate source_index: {source_index}")
        seen_source_indices.add(source_index)
        actual_source_indices.append(source_index)

        source_prim = _string(part.get("source_prim"), f"parts[{index}].source_prim")
        if any(fragment in source_prim for fragment in FORBIDDEN_SOURCE_NAME_FRAGMENTS):
            raise ValueError(f"Forbidden shell visual source_prim: {source_prim}")
        if source_prim in seen_source_prims:
            raise ValueError(f"Duplicate source_prim: {source_prim}")
        seen_source_prims.add(source_prim)
        actual_source_prims.append(source_prim)

        _string(part.get("instance_prim"), f"parts[{index}].instance_prim")
        _validate_visual_role(part, "mesh_role", index)
        _validate_visual_role(part, "role", index)

        _validate_optional_transform(part, "source_usd_rest_transform", f"parts[{index}]")
        raw_xml_geom_local = _mapping(part.get("raw_xml_geom_local"), f"parts[{index}].raw_xml_geom_local")
        _validate_optional_transform(raw_xml_geom_local, None, f"parts[{index}].raw_xml_geom_local")

        transforms = _mapping(part.get("transforms"), f"parts[{index}].transforms")
        if set(transforms) != set(KEYFRAME_NAMES):
            raise ValueError(f"parts[{index}].transforms must contain {KEYFRAME_NAMES!r}")
        for keyframe_name in KEYFRAME_NAMES:
            _validate_transform(
                _mapping(transforms.get(keyframe_name), f"parts[{index}].transforms.{keyframe_name}"),
                f"parts[{index}].transforms.{keyframe_name}",
            )

    if tuple(actual_source_indices) != EXPECTED_STRUCTURAL_SOURCE_INDICES:
        raise ValueError("source_index allowlist mismatch for structural passive visuals")
    if tuple(actual_source_prims) != EXPECTED_STRUCTURAL_SOURCE_PRIMS:
        raise ValueError("source_prim allowlist mismatch for structural passive visuals")
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
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
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
        _validate_transform(_mapping(container, label), label)
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


def _require_exact_field(manifest: Mapping[str, Any], key: str, expected: str) -> None:
    if manifest.get(key) != expected:
        raise ValueError(f"{key} must match the locked passive-linkage provenance")


def _validate_visual_role(part: Mapping[str, Any], key: str, index: int) -> None:
    role = _string(part.get(key), f"parts[{index}].{key}")
    if role in FORBIDDEN_DECORATIVE_ROLES:
        raise ValueError(f"Forbidden decorative role in parts[{index}].{key}: {role}")


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


def _integer(value: Any, label: str, *, noun: str = "source_index") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} {noun} must be an integer")
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
