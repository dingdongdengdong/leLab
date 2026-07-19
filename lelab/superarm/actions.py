"""Canonical six-control action mapping for SuperArm and AmazingHand."""

from __future__ import annotations

import math
from typing import Any

from .mapping import ARM_JOINTS, ARM_MAX_RAD, ARM_MIN_RAD, UI_FINGERS

MOTION_FEATURE = "amazinghand_motion.pos"
CANONICAL_FEATURES = [*[f"{name}.pos" for name in ARM_JOINTS], MOTION_FEATURE]
MOTION_DEGREES = {0.0: 0.0, 0.5: 55.0, 1.0: 110.0}


def resolve_motion_code(value: float) -> float:
    """Resolve a continuous grasp value to one of the three fixed hand motions."""
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("AmazingHand motion must be finite")
    return min(MOTION_DEGREES, key=lambda code: abs(code - value))


def normalize_superarm_action(action: list[float] | dict[str, float]) -> list[float]:
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
    values[:5] = [max(ARM_MIN_RAD, min(ARM_MAX_RAD, value)) for value in values[:5]]
    values[-1] = resolve_motion_code(values[-1])
    return values


def action_to_runtime_commands(values: list[float]) -> tuple[dict[str, float], dict[str, list[float]]]:
    """Expand the 6D action into MuJoCo arm targets and one fixed hand pose."""
    normalized = normalize_superarm_action(values)
    arm = dict(zip(ARM_JOINTS, normalized[:5], strict=True))
    hand_degrees = MOTION_DEGREES[normalized[-1]]
    hand = {finger: [hand_degrees, hand_degrees] for finger in UI_FINGERS}
    return arm, hand


def map_so101_action_to_superarm(
    action: dict[str, float],
    *,
    arm_mapping: list[dict[str, Any]],
    arm_limits: dict[str, dict[str, float]],
    gripper_feature: str = "gripper.pos",
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
        radians = float(item.get("sign", 1.0)) * math.radians(float(action[source])) + float(
            item.get("offset_rad", 0.0)
        )
        limit = arm_limits.get(target.removesuffix(".pos"))
        if limit:
            radians = max(float(limit["min"]), min(float(limit["max"]), radians))
        mapped[target] = radians
    if gripper_feature not in action:
        raise ValueError(f"SO101 action is missing required feature {gripper_feature!r}")
    mapped[MOTION_FEATURE] = resolve_motion_code(float(action[gripper_feature]) / 100.0)
    return dict(zip(CANONICAL_FEATURES, (mapped[name] for name in CANONICAL_FEATURES), strict=True))


class SO101ToSuperArmActionAdapter:
    def __init__(self, **mapping: Any) -> None:
        self.mapping = mapping

    def __call__(self, action: dict[str, float]) -> dict[str, float]:
        return map_so101_action_to_superarm(action, **self.mapping)
