"""Canonical six-control action mapping for SuperArm and AmazingHand."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import yaml

from isaacsim_validation.contracts import (
    FIXED_GRASP_DEGREES,
    PHYSICAL_JOINTS,
    expand_logical_action,
)

from .mapping import ARM_JOINTS, ARM_MAX_RAD, ARM_MIN_RAD, UI_FINGERS

MOTION_FEATURE = "amazinghand_motion.pos"
CANONICAL_FEATURES = [*[f"{name}.pos" for name in ARM_JOINTS], MOTION_FEATURE]
MOTION_DEGREES = FIXED_GRASP_DEGREES
SUPPORTED_CONTROL_CONFIG_TYPES = frozenset({"superarm_isaac", "isaacsim_rpo_arm"})


def resolve_motion_code(
    value: float,
    *,
    previous_code: float | None = None,
    hysteresis: float = 0.0,
) -> float:
    """Resolve a continuous grasp value to one of the three fixed hand motions."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("AmazingHand motion must be finite")
    if hysteresis < 0.0 or hysteresis >= 0.25:
        raise ValueError("AmazingHand motion hysteresis must be in [0.0, 0.25)")
    value = max(0.0, min(1.0, value))
    codes = tuple(sorted(MOTION_DEGREES))
    if previous_code in codes:
        index = codes.index(previous_code)
        lower = -math.inf if index == 0 else (codes[index - 1] + previous_code) / 2 - hysteresis
        upper = (
            math.inf
            if index == len(codes) - 1
            else (previous_code + codes[index + 1]) / 2 + hysteresis
        )
        if lower <= value <= upper:
            return float(previous_code)
    return float(min(codes, key=lambda code: abs(value - code)))


def validate_superarm_control_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate either LeLab's bundled schema or SuperArm's edited LeRobot schema."""
    if raw.get("_type") not in SUPPORTED_CONTROL_CONFIG_TYPES:
        raise ValueError(
            "SuperArm control config must use type superarm_isaac or isaacsim_rpo_arm"
        )
    expected_joint_names = [name.removesuffix(".pos") for name in CANONICAL_FEATURES]
    if raw.get("joint_names") != expected_joint_names:
        raise ValueError("SuperArm control config must define the canonical six controls")
    if raw.get("physical_joint_names") != list(PHYSICAL_JOINTS):
        raise ValueError("SuperArm control config must define the canonical 13 physical joints")
    arm_limits = raw.get("arm_limits")
    if not isinstance(arm_limits, dict) or set(arm_limits) != set(ARM_JOINTS):
        raise ValueError("SuperArm control config must define limits for all five arm joints")
    normalized_limits: dict[str, dict[str, float]] = {}
    for name in ARM_JOINTS:
        limit = arm_limits.get(name)
        if not isinstance(limit, dict):
            raise ValueError(f"SuperArm control config is missing limits for {name}")
        try:
            lower = float(limit["min"])
            upper = float(limit["max"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"SuperArm control limits are invalid for {name}") from exc
        if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
            raise ValueError(f"SuperArm control limits are invalid for {name}")
        normalized_limits[name] = {"min": lower, "max": upper}
    return {**raw, "arm_limits": normalized_limits}


def load_superarm_control_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("SuperArm control config must be a YAML object")
    return validate_superarm_control_config(raw)


def normalize_superarm_action(
    action: list[float] | dict[str, float],
    *,
    arm_limits: dict[str, dict[str, float]] | None = None,
) -> list[float]:
    """Validate and normalize five arm radians plus one fixed hand motion."""
    if isinstance(action, dict):
        if set(action) != set(CANONICAL_FEATURES):
            raise ValueError(f"SuperArm action must use features {CANONICAL_FEATURES}")
        values = [float(action[name]) for name in CANONICAL_FEATURES]
    else:
        values = [float(value) for value in action]
        if len(values) != len(CANONICAL_FEATURES):
            raise ValueError(f"SuperArm action must contain exactly 6 values, got {len(values)}")
    if not all(math.isfinite(value) for value in values):
        raise ValueError("SuperArm action values must be finite")
    values[:5] = [
        max(
            float((arm_limits or {}).get(name, {}).get("min", ARM_MIN_RAD)),
            min(
                float((arm_limits or {}).get(name, {}).get("max", ARM_MAX_RAD)),
                value,
            ),
        )
        for name, value in zip(ARM_JOINTS, values[:5], strict=True)
    ]
    values[-1] = resolve_motion_code(values[-1])
    return values


def action_to_runtime_commands(values: list[float]) -> tuple[dict[str, float], dict[str, list[float]]]:
    """Expand the 6D action into MuJoCo arm targets and one fixed hand pose."""
    normalized = normalize_superarm_action(values)
    arm = dict(zip(ARM_JOINTS, normalized[:5], strict=True))
    hand_degrees = MOTION_DEGREES[normalized[-1]]
    hand = {finger: [hand_degrees, hand_degrees] for finger in UI_FINGERS}
    return arm, hand


def action_to_isaac_targets(
    values: list[float],
    *,
    arm_limits: dict[str, dict[str, float]] | None = None,
) -> dict[str, float]:
    """Expand the canonical 6D action into 13 positive URDF/Isaac targets."""

    return expand_logical_action(
        normalize_superarm_action(values, arm_limits=arm_limits)
    )


def map_so101_action_to_superarm(
    action: dict[str, float],
    *,
    arm_mapping: list[dict[str, Any]],
    arm_limits: dict[str, dict[str, float]],
    gripper_feature: str = "gripper.pos",
    previous_motion_code: float | None = None,
    motion_hysteresis: float = 0.05,
) -> dict[str, float]:
    """Convert SO101 degrees and gripper percent into the canonical 6D action."""
    if len(arm_mapping) != 5:
        raise ValueError("SO101 arm mapping must contain exactly five entries")
    mapped: dict[str, float] = {}
    for item in arm_mapping:
        source = str(item["source"])
        target = str(item["target"])
        if source not in action:
            raise ValueError(f"SO101 action is missing required feature {source!r}")
        degrees = float(action[source])
        if not math.isfinite(degrees):
            raise ValueError(f"SO101 feature {source!r} must be finite")
        radians = float(item.get("sign", 1.0)) * math.radians(degrees) + float(
            item.get("offset_rad", 0.0)
        )
        limit = arm_limits.get(target.removesuffix(".pos"))
        if limit:
            radians = max(float(limit["min"]), min(float(limit["max"]), radians))
        mapped[target] = radians
    if gripper_feature not in action:
        raise ValueError(f"SO101 action is missing required feature {gripper_feature!r}")
    gripper = float(action[gripper_feature])
    if not math.isfinite(gripper):
        raise ValueError(f"SO101 feature {gripper_feature!r} must be finite")
    mapped[MOTION_FEATURE] = resolve_motion_code(
        gripper / 100.0,
        previous_code=previous_motion_code,
        hysteresis=motion_hysteresis,
    )
    return dict(zip(CANONICAL_FEATURES, (mapped[name] for name in CANONICAL_FEATURES), strict=True))


class SO101ToSuperArmActionAdapter:
    def __init__(self, **mapping: Any) -> None:
        self.mapping = mapping
        self.previous_motion_code: float | None = None

    def __call__(self, action: dict[str, float]) -> dict[str, float]:
        mapped = map_so101_action_to_superarm(
            action,
            previous_motion_code=self.previous_motion_code,
            **self.mapping,
        )
        self.previous_motion_code = mapped[MOTION_FEATURE]
        return mapped
