"""Real SuperArm hardware adapter: Damiao CAN arm plus AmazingHand serial hand.

This module deliberately does not reuse the MuJoCo runtime or the SO-101
Feetech bus.  It composes LeRobot's OpenArm/Damiao CAN implementation with the
AmazingHandControl-compatible SCS0009 serial transport, while presenting the
same five-arm-plus-one-fixed-grasp action space used by simulation and VLA
recording.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from lerobot.robots.config import RobotConfig
from lerobot.robots.robot import Robot

from .actions import (
    CANONICAL_FEATURES,
    action_to_runtime_commands,
    normalize_superarm_action,
    resolve_motion_code,
)
from .mapping import ARM_JOINTS, ARM_MAX_RAD, ARM_MIN_RAD, SERVO_SPEED_MAX, SERVO_SPEED_MIN
from .transports import SerialAmazingHandTransport

DM4340P_LEROBOT_TYPE = "dm4340"
REAL_HARDWARE_MAX_GRASP_CODE = 0.5


def validate_dm4340p_arm_motors(motors: dict[str, tuple[int, int, str]]) -> None:
    """Reject incomplete or ambiguous CAN mappings before any torque enable.

    LeRobot represents both DM4340 and DM4340P with its ``dm4340`` motor type.
    IDs are intentionally required from the physical robot configuration: the
    OpenArm defaults cannot safely be assumed for this custom five-joint arm.
    """
    if set(motors) != set(ARM_JOINTS):
        raise ValueError(f"DM4340P mapping must define exactly {ARM_JOINTS}")
    send_ids: set[int] = set()
    receive_ids: set[int] = set()
    for name, config in motors.items():
        if len(config) != 3:
            raise ValueError(f"{name} must use (send_can_id, receive_can_id, motor_type)")
        send_id, receive_id, motor_type = config
        if motor_type != DM4340P_LEROBOT_TYPE:
            raise ValueError(f"{name} must use LeRobot motor type {DM4340P_LEROBOT_TYPE!r}")
        if not 1 <= int(send_id) <= 0x7FF or not 1 <= int(receive_id) <= 0x7FF:
            raise ValueError(f"{name} CAN IDs must be within [1, 0x7ff]")
        send_ids.add(int(send_id))
        receive_ids.add(int(receive_id))
    if len(send_ids) != len(ARM_JOINTS) or len(receive_ids) != len(ARM_JOINTS):
        raise ValueError("DM4340P CAN send and receive IDs must each be unique")
    if send_ids & receive_ids:
        raise ValueError("DM4340P CAN send and receive ID sets must not overlap")


def validate_arm_joint_calibration(calibration: dict[str, tuple[float, float]]) -> None:
    """Require explicit direction and zero offsets for every custom arm joint."""
    if set(calibration) != set(ARM_JOINTS):
        raise ValueError(f"Arm calibration must define exactly {ARM_JOINTS}")
    for name, values in calibration.items():
        if len(values) != 2:
            raise ValueError(f"{name} calibration must use (direction, zero_offset_rad)")
        direction, offset_rad = float(values[0]), float(values[1])
        if direction not in {-1.0, 1.0} or not math.isfinite(offset_rad):
            raise ValueError(f"{name} direction must be -1 or 1 and zero offset must be finite")


def validate_arm_limits_and_gains(
    limits: dict[str, tuple[float, float]], position_kp: list[float], position_kd: list[float]
) -> None:
    """Require measured limits and MIT gains instead of inheriting OpenArm values."""
    if set(limits) != set(ARM_JOINTS):
        raise ValueError(f"Arm joint limits must define exactly {ARM_JOINTS}")
    for name, values in limits.items():
        if len(values) != 2 or not all(math.isfinite(float(value)) for value in values):
            raise ValueError(f"{name} limits must contain two finite degrees")
        if not float(values[0]) < float(values[1]):
            raise ValueError(f"{name} lower limit must be less than its upper limit")
    if len(position_kp) != len(ARM_JOINTS) or len(position_kd) != len(ARM_JOINTS):
        raise ValueError("DM4340P position_kp and position_kd must each contain five measured values")
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in [*position_kp, *position_kd]):
        raise ValueError("DM4340P gains must be finite non-negative values")


def arm_radians_to_openarm_degrees(
    arm_rad: dict[str, float], calibration: dict[str, tuple[float, float]] | None = None
) -> dict[str, float]:
    """Map the custom names/units to LeRobot OpenArm follower motor names."""
    unknown = set(arm_rad) - set(ARM_JOINTS)
    if unknown:
        raise ValueError(f"Unknown SuperArm joints: {sorted(unknown)}")
    if calibration is not None:
        validate_arm_joint_calibration(calibration)
    return {
        f"joint_{index}.pos": math.degrees(
            (
                calibration[name][0] * max(ARM_MIN_RAD, min(ARM_MAX_RAD, float(arm_rad[name])))
                + calibration[name][1]
            )
            if calibration is not None
            else max(ARM_MIN_RAD, min(ARM_MAX_RAD, float(arm_rad[name])))
        )
        for index, name in enumerate(ARM_JOINTS, start=1)
    }


def openarm_degrees_to_arm_radians(
    observation: dict[str, Any], calibration: dict[str, tuple[float, float]] | None = None
) -> dict[str, float]:
    """Convert LeRobot OpenArm follower position feedback to SuperArm radians."""
    if calibration is not None:
        validate_arm_joint_calibration(calibration)
    return {
        name: max(
            ARM_MIN_RAD,
            min(
                ARM_MAX_RAD,
                (
                    calibration[name][0]
                    * (math.radians(float(observation.get(f"joint_{index}.pos", 0.0))) - calibration[name][1])
                    if calibration is not None
                    else math.radians(float(observation.get(f"joint_{index}.pos", 0.0)))
                ),
            ),
        )
        for index, name in enumerate(ARM_JOINTS, start=1)
    }


@RobotConfig.register_subclass("superarm_dm4340p_amazinghand")
@dataclass(kw_only=True)
class SuperArmDm4340PAmazingHandConfig(RobotConfig):
    """Configuration for the real hardware adapter.

    ``arm_motor_config`` has no default on purpose: it must be copied from
    the discovered physical CAN IDs, not from a different OpenArm build.
    """

    arm_port: str
    hand_port: str = "/dev/ttyACM0"
    arm_motor_config: dict[str, tuple[int, int, str]] = field(default_factory=dict)
    arm_joint_calibration: dict[str, tuple[float, float]] = field(default_factory=dict)
    arm_can_interface: str = "socketcan"
    arm_use_can_fd: bool = True
    arm_can_bitrate: int = 1_000_000
    arm_can_data_bitrate: int = 5_000_000
    arm_joint_limits_deg: dict[str, tuple[float, float]] = field(default_factory=dict)
    arm_position_kp: list[float] = field(default_factory=list)
    arm_position_kd: list[float] = field(default_factory=list)
    hand_baudrate: int = 1_000_000
    hand_speed: int = 3
    hand_max_motion_code: float = REAL_HARDWARE_MAX_GRASP_CODE


class SuperArmDm4340PAmazingHandRobot(Robot):
    """LeRobot real-hardware robot with a 6D policy action and two protocols."""

    config_class = SuperArmDm4340PAmazingHandConfig
    name = "superarm_dm4340p_amazinghand"

    def __init__(self, config: SuperArmDm4340PAmazingHandConfig) -> None:
        super().__init__(config)
        if (
            not math.isfinite(config.hand_max_motion_code)
            or not 0.0 <= config.hand_max_motion_code <= REAL_HARDWARE_MAX_GRASP_CODE
        ):
            raise ValueError(
                "real AmazingHand hand_max_motion_code must be within [0.0, 0.5]"
            )
        self.config = config
        self._arm: Any = None
        self._hand = SerialAmazingHandTransport(config.hand_port, config.hand_baudrate)
        self._logical = [0.0] * len(CANONICAL_FEATURES)

    @property
    def action_features(self) -> dict[str, type]:
        return dict.fromkeys(CANONICAL_FEATURES, float)

    @property
    def observation_features(self) -> dict[str, type]:
        return dict.fromkeys(CANONICAL_FEATURES, float)

    @property
    def is_connected(self) -> bool:
        return bool(self._arm and self._arm.is_connected and self._hand.connected)

    @property
    def is_calibrated(self) -> bool:
        return bool(self._arm and self._arm.is_calibrated)

    def _make_arm(self) -> Any:
        validate_dm4340p_arm_motors(self.config.arm_motor_config)
        validate_arm_joint_calibration(self.config.arm_joint_calibration)
        validate_arm_limits_and_gains(
            self.config.arm_joint_limits_deg,
            self.config.arm_position_kp,
            self.config.arm_position_kd,
        )
        if not SERVO_SPEED_MIN <= self.config.hand_speed <= SERVO_SPEED_MAX:
            raise ValueError(f"AmazingHand speed must be in [{SERVO_SPEED_MIN}, {SERVO_SPEED_MAX}]")
        try:
            from lerobot.robots.openarm_follower import OpenArmFollower, OpenArmFollowerConfig
        except ImportError as exc:
            raise RuntimeError(
                "Install the LeRobot openarms extra (including python-can) before using DM4340P hardware."
            ) from exc
        motor_config = {
            f"joint_{index}": self.config.arm_motor_config[name]
            for index, name in enumerate(ARM_JOINTS, start=1)
        }
        limits = {
            f"joint_{index}": self.config.arm_joint_limits_deg[name]
            for index, name in enumerate(ARM_JOINTS, start=1)
        }
        return OpenArmFollower(
            OpenArmFollowerConfig(
                port=self.config.arm_port,
                can_interface=self.config.arm_can_interface,
                use_can_fd=self.config.arm_use_can_fd,
                can_bitrate=self.config.arm_can_bitrate,
                can_data_bitrate=self.config.arm_can_data_bitrate,
                motor_config=motor_config,
                joint_limits=limits,
                position_kp=self.config.arm_position_kp,
                position_kd=self.config.arm_position_kd,
                max_relative_target=5.0,
            )
        )

    def connect(self, calibrate: bool = True) -> None:
        if self.is_connected:
            return
        arm = self._make_arm()
        try:
            arm.connect(calibrate=calibrate)
            self._hand.connect()
        except Exception:
            if arm.is_connected:
                arm.disconnect()
            self._hand.close()
            raise
        self._arm = arm

    def calibrate(self) -> None:
        if not self._arm:
            raise RuntimeError("Connect the DM4340P arm before calibration")
        self._arm.calibrate()

    def configure(self) -> None:
        if not self._arm:
            raise RuntimeError("Connect the DM4340P arm before configuration")
        self._arm.configure()

    def get_observation(self) -> dict[str, float]:
        if not self.is_connected:
            raise RuntimeError("SuperArm DM4340P + AmazingHand hardware is disconnected")
        arm = openarm_degrees_to_arm_radians(self._arm.get_observation(), self.config.arm_joint_calibration)
        hand = self._hand.observe()
        positions = [float(entry["position"]) for entry in hand.values() if "position" in entry]
        if positions:
            self._logical[-1] = resolve_motion_code(sum(positions) / len(positions) / 110.0)
        return dict(
            zip(CANONICAL_FEATURES, [*(arm[name] for name in ARM_JOINTS), self._logical[-1]], strict=True)
        )

    def send_action(self, action: list[float] | dict[str, float] | np.ndarray):
        if not self.is_connected:
            raise RuntimeError("SuperArm DM4340P + AmazingHand hardware is disconnected")
        named = isinstance(action, dict)
        if isinstance(action, np.ndarray):
            action = action.reshape(-1).tolist()
        values = normalize_superarm_action(action)
        values[-1] = min(values[-1], self.config.hand_max_motion_code)
        arm_rad, hand_deg = action_to_runtime_commands(values)
        self._arm.send_action(arm_radians_to_openarm_degrees(arm_rad, self.config.arm_joint_calibration))
        try:
            self._hand.command(
                hand_deg, {finger: [self.config.hand_speed, self.config.hand_speed] for finger in hand_deg}
            )
        except Exception:
            # One logical action must not leave a torque-enabled CAN arm active
            # after its paired AmazingHand command failed.
            self.disconnect()
            raise
        self._logical = values
        if named:
            return dict(zip(CANONICAL_FEATURES, values, strict=True))
        return np.asarray(values, dtype=np.float32)

    def disconnect(self) -> None:
        try:
            self._hand.close()
        finally:
            if self._arm and self._arm.is_connected:
                self._arm.disconnect()
            self._arm = None
