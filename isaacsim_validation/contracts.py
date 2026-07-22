"""Shared 6D LeRobot to 13-joint URDF validation mapping."""

from __future__ import annotations

import math
from collections.abc import Sequence

ARM_JOINTS = tuple(f"joint_rev_{index}" for index in range(1, 6))
HAND_JOINTS = tuple(
    f"finger{finger}_motor{motor}" for finger in range(1, 5) for motor in range(1, 3)
)
FIXED_GRASP_DEGREES = {0.0: 0.0, 0.5: 55.0, 1.0: 110.0}


def resolve_grasp_code(value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("grasp value must be finite")
    if value < 0.25:
        return 0.0
    if value < 0.75:
        return 0.5
    return 1.0


def grasp_to_urdf_targets(value: float) -> dict[str, float]:
    code = resolve_grasp_code(value)
    degrees = FIXED_GRASP_DEGREES[code]
    motor1 = 0.05 + degrees * (0.95 - 0.05) / 110.0
    motor2 = 0.02 + degrees * (1.10 - 0.02) / 110.0
    return {
        f"finger{finger}_motor{motor}": motor1 if motor == 1 else motor2
        for finger in range(1, 5)
        for motor in range(1, 3)
    }


def expand_logical_action(action: Sequence[float]) -> dict[str, float]:
    if len(action) != 6:
        raise ValueError(f"expected six logical actions, got {len(action)}")
    values = [float(value) for value in action]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("logical action values must be finite")
    return {
        **dict(zip(ARM_JOINTS, values[:5], strict=True)),
        **grasp_to_urdf_targets(values[5]),
    }
