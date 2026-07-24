"""Pure RL task contracts shared by the Gym adapter and tests."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from lelab.superarm.mapping import ARM_MAX_RAD, ARM_MIN_RAD

ARM_JOINTS = tuple(f"joint_rev_{index}" for index in range(1, 6))
ACTION_SCALE_RAD = 0.04
GRASP_CODES = (0.0, 0.5, 1.0)
STATE_SIZE = 23
IMAGE_SHAPE = (256, 256, 3)


def map_policy_action(
    action: Sequence[float], current_positions: Mapping[str, float]
) -> tuple[dict[str, float], float]:
    """Map five normalized deltas plus a categorical grasp index."""

    array = np.asarray(action, dtype=np.float64)
    if array.shape != (6,) or not np.isfinite(array).all():
        raise ValueError("RL action must contain six finite values")
    missing = [name for name in ARM_JOINTS if name not in current_positions]
    if missing:
        raise ValueError(f"current arm state is missing joints: {missing}")

    deltas = np.clip(array[:5], -1.0, 1.0) * ACTION_SCALE_RAD
    targets: dict[str, float] = {}
    for index, name in enumerate(ARM_JOINTS):
        current = float(current_positions[name])
        if not math.isfinite(current):
            raise ValueError(f"current arm position is not finite for {name}")
        targets[name] = float(np.clip(current + deltas[index], ARM_MIN_RAD, ARM_MAX_RAD))

    grasp_index = int(np.clip(np.rint(array[5]), 0, len(GRASP_CODES) - 1))
    return targets, GRASP_CODES[grasp_index]


def state_vector(payload: Mapping[str, Any]) -> np.ndarray:
    """Return the fixed 23-value policy state in its documented order."""

    fields = (
        ("joint_positions", 5),
        ("joint_velocities", 5),
        ("grasp_state", 1),
        ("end_effector_xyz", 3),
        ("cube_xyz", 3),
        ("cube_linear_velocity_xyz", 3),
        ("end_effector_to_cube_xyz", 3),
    )
    values: list[float] = []
    for key, width in fields:
        raw = payload.get(key)
        if width == 1 and isinstance(raw, int | float) and not isinstance(raw, bool):
            section = [float(raw)]
        elif isinstance(raw, Sequence) and not isinstance(raw, str | bytes):
            section = [float(item) for item in raw]
        else:
            raise ValueError(f"RL state requires numeric {key}")
        if len(section) != width or not all(math.isfinite(item) for item in section):
            raise ValueError(f"RL state {key} must contain {width} finite values")
        values.extend(section)
    state = np.asarray(values, dtype=np.float32)
    if state.shape != (STATE_SIZE,):
        raise AssertionError("internal RL state ordering error")
    return state


def validate_rgb_frame(frame: np.ndarray) -> np.ndarray:
    array = np.asarray(frame)
    if array.shape != IMAGE_SHAPE or array.dtype != np.uint8:
        raise ValueError("workspace image must be uint8 with shape (256, 256, 3)")
    return array


@dataclass(frozen=True)
class RewardTerms:
    distance: float
    lift_progress: float
    success: float
    action: float
    grasp_change: float

    @property
    def total(self) -> float:
        return self.distance + self.lift_progress + self.success + self.action + self.grasp_change


def reward_terms(
    *,
    ee_to_cube_xyz: Sequence[float],
    cube_height: float,
    previous_cube_height: float,
    table_height: float,
    arm_action: Sequence[float],
    grasp_changed: bool,
    success: bool,
) -> RewardTerms:
    distance = float(np.linalg.norm(np.asarray(ee_to_cube_xyz, dtype=np.float64)))
    action = np.asarray(arm_action, dtype=np.float64)
    if action.shape != (5,) or not np.isfinite(action).all() or not math.isfinite(distance):
        raise ValueError("reward inputs must be finite and match the action contract")
    lift_delta = max(0.0, float(cube_height) - max(float(previous_cube_height), float(table_height)))
    return RewardTerms(
        distance=-2.0 * distance,
        lift_progress=min(2.0, 20.0 * lift_delta),
        success=10.0 if success else 0.0,
        action=-0.01 * float(np.square(action).sum()),
        grasp_change=-0.02 if grasp_changed else 0.0,
    )
