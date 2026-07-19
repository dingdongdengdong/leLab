"""AmazingHand naming, calibration, and upstream-compatible conversion."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

ARM_JOINTS = [f"joint_rev_{index}" for index in range(1, 6)]
UI_FINGERS = ["pointer", "middle", "ring", "thumb"]
UPSTREAM_FINGERS = ["ring", "middle", "pointer", "thumb"]
SERVO_IDS = {
    "pointer": (1, 2),
    "middle": (3, 4),
    "ring": (5, 6),
    "thumb": (7, 8),
}
MUJOCO_FINGERS = {
    "pointer": "finger1",
    "middle": "finger2",
    "ring": "finger3",
    "thumb": "finger4",
}
HAND_ACTUATORS = [
    f"finger{finger}_motor{motor}"
    for finger in range(1, 5)
    for motor in range(1, 3)
]

SERVO_MIN_DEG = -40.0
SERVO_MAX_DEG = 110.0
BASE_MIN_DEG = 0.0
SIDE_MIN_DEG = -40.0
SIDE_MAX_DEG = 40.0
ARM_MIN_RAD = -1.57
ARM_MAX_RAD = 1.57
HAND_ACTUATOR_MIN_RAD = -math.pi / 2
HAND_ACTUATOR_MAX_RAD = math.pi / 2
URDF_MOTOR_LIMITS = {
    1: (-0.05, 1.05),
    2: (0.0, 1.2),
}


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def degrees_to_mujoco(motor: int, degrees: float) -> float:
    """Apply calibrated UI degrees and the official MJCF joint directions.

    The second servo's mechanical direction is inverted. Driving both official
    MJCF hinges positive mostly abducts the linkage; motor 2 must be negative
    for the fingertip to flex toward the palm.
    """
    if motor == 1:
        radians = 0.05 + float(degrees) * (0.95 - 0.05) / 110.0
    elif motor == 2:
        radians = -(0.02 + float(degrees) * (1.10 - 0.02) / 110.0)
    else:
        raise ValueError(f"Unknown motor index: {motor}")
    return clamp(radians, HAND_ACTUATOR_MIN_RAD, HAND_ACTUATOR_MAX_RAD)


def degrees_to_hardware_radians(servo_id: int, degrees: float) -> float:
    """Retain AmazingHandControl's even-servo direction inversion."""
    value = math.radians(float(degrees))
    return -value if servo_id % 2 == 0 else value


def hardware_radians_to_degrees(servo_id: int, radians: Any) -> float:
    value = float(radians)
    degrees = math.degrees(value)
    return -degrees if servo_id % 2 == 0 else degrees


def named_hand_to_mujoco(hand_deg: dict[str, list[float]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for finger, values in hand_deg.items():
        prefix = MUJOCO_FINGERS[finger]
        result[f"{prefix}_motor1"] = degrees_to_mujoco(1, values[0])
        result[f"{prefix}_motor2"] = degrees_to_mujoco(2, values[1])
    return result


def mujoco_hand_to_urdf(positions: Mapping[str, float]) -> dict[str, float]:
    """Project raw AmazingHand qpos into the simplified showroom joint frames.

    The official MJCF flexes each second motor in the negative direction, while
    the two-link showroom URDF models the same curl with a positive joint value.
    This conversion is intentionally limited to visualization; runtime telemetry
    and the LeRobot action contract remain in MuJoCo coordinates.
    """
    projected: dict[str, float] = {}
    for name, raw_value in positions.items():
        value = float(raw_value)
        motor = 2 if name.endswith("_motor2") else 1
        if motor == 2:
            value = -value
        lower, upper = URDF_MOTOR_LIMITS[motor]
        projected[name] = clamp(value, lower, upper)
    return projected


def upstream_positions_to_named(positions: list[float]) -> dict[str, list[float]]:
    if len(positions) != 8:
        raise ValueError(f"Expected 8 upstream servo values, got {len(positions)}")
    return {
        finger: [float(positions[index * 2]), float(positions[index * 2 + 1])]
        for index, finger in enumerate(UPSTREAM_FINGERS)
    }


def named_to_upstream_positions(hand_deg: dict[str, list[float]]) -> list[float]:
    missing = [finger for finger in UPSTREAM_FINGERS if finger not in hand_deg]
    if missing:
        raise ValueError(f"Hand-only upstream export requires every finger: {missing}")
    return [float(value) for finger in UPSTREAM_FINGERS for value in hand_deg[finger]]


def estimate_current_ma(load_value: float | None) -> float | None:
    if load_value is None:
        return None
    magnitude = abs(float(load_value))
    percent = magnitude * 100.0 if magnitude <= 1.5 else magnitude / 10.23
    return round(clamp(percent, 0.0, 150.0) / 100.0 * 1200.0, 1)
