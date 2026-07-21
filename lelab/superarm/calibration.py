"""Torque-disabled, web-driven calibration session for the five-joint SuperArm arm."""

from __future__ import annotations

import threading
from typing import Any

from .hardware import DM4340P_LEROBOT_TYPE, validate_dm4340p_arm_motors
from .mapping import ARM_JOINTS


class SuperArmCalibrationSession:
    """Wrap LeRobot's Damiao bus without using OpenArm's CLI ``input`` flow.

    The session deliberately never calls ``OpenArmFollower.connect`` because
    that method enables torque.  It opens the configured bus, immediately
    disables torque, then only reads positions until the operator ends it.
    """

    def __init__(self) -> None:
        self._arm: Any = None
        self._lock = threading.RLock()
        self._active = False
        self._zero_captured = False
        self._ranges: dict[str, dict[str, float]] = {}
        self._message = "Ready to start torque-disabled SuperArm calibration."
        self._error: str | None = None

    @staticmethod
    def _make_arm(arm_port: str, mapping: dict[str, tuple[int, int, str]]) -> Any:
        try:
            from lerobot.robots.openarm_follower import OpenArmFollower, OpenArmFollowerConfig
        except ImportError as exc:
            raise RuntimeError("Install LeRobot OpenArm support before calibrating SuperArm") from exc
        config = OpenArmFollowerConfig(
            port=arm_port,
            can_interface="socketcan",
            use_can_fd=True,
            can_bitrate=1_000_000,
            can_data_bitrate=5_000_000,
            motor_config={f"joint_{index}": mapping[name] for index, name in enumerate(ARM_JOINTS, 1)},
            joint_limits={f"joint_{index}": (-180.0, 180.0) for index in range(1, 6)},
            position_kp=[0.0] * 5,
            position_kd=[0.0] * 5,
            max_relative_target=0.0,
        )
        return OpenArmFollower(config)

    def start(self, arm_port: str, arm_motor_config: dict[str, tuple[int, int]]) -> dict[str, Any]:
        with self._lock:
            if self._active:
                raise RuntimeError("SuperArm calibration is already active")
            mapping = {
                name: (send_id, receive_id, DM4340P_LEROBOT_TYPE)
                for name, (send_id, receive_id) in arm_motor_config.items()
            }
            validate_dm4340p_arm_motors(mapping)
            arm = self._make_arm(arm_port, mapping)
            try:
                arm.bus.connect()
                arm.bus.disable_torque()
                positions = arm.bus.sync_read("Present_Position")
            except Exception:
                if arm.bus.is_connected:
                    arm.bus.disconnect(disable_torque=True)
                raise
            self._arm = arm
            self._active = True
            self._zero_captured = False
            self._ranges = {
                name: {"min": float(position), "max": float(position), "current": float(position)}
                for name, position in positions.items()
            }
            self._message = "Torque is disabled. Position the arm at its reference pose, then capture zero."
            self._error = None
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self._active and self._arm:
                try:
                    positions = self._arm.bus.sync_read("Present_Position")
                    for name, position in positions.items():
                        current = float(position)
                        entry = self._ranges.setdefault(
                            name, {"min": current, "max": current, "current": current}
                        )
                        entry["min"] = min(entry["min"], current)
                        entry["max"] = max(entry["max"], current)
                        entry["current"] = current
                except Exception as exc:
                    self._error = str(exc)
            return {
                "calibration_active": self._active,
                "torque_enabled": False,
                "zero_captured": self._zero_captured,
                "message": self._message,
                "error": self._error,
                "recorded_ranges": dict(self._ranges),
            }

    def capture_zero(self) -> dict[str, Any]:
        with self._lock:
            if not self._active or not self._arm:
                raise RuntimeError("Start SuperArm calibration before capturing the zero pose")
            # Damiao's zero command is persistent; it remains an operator-triggered
            # calibration action, with torque disabled before and after the write.
            self._arm.bus.disable_torque()
            self._arm.bus.set_zero_position()
            self._zero_captured = True
            self._message = (
                "Zero captured with torque disabled. Move each joint manually through its safe range."
            )
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self._arm and self._arm.bus.is_connected:
                self._arm.bus.disconnect(disable_torque=True)
            self._arm = None
            self._active = False
            self._message = "Calibration connection closed and torque disabled."
            return self.status()


superarm_calibration = SuperArmCalibrationSession()
