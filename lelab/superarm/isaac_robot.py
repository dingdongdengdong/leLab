"""Isaac-backed LeRobot robot for the six-control SuperArm contract."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from lerobot.cameras.utils import make_cameras_from_configs
from lerobot.robots.config import RobotConfig
from lerobot.robots.robot import Robot

from .actions import CANONICAL_FEATURES, normalize_superarm_action
from .mapping import ARM_JOINTS, HAND_ACTUATORS
from .service import service


@RobotConfig.register_subclass("superarm_isaac")
@dataclass(kw_only=True)
class SuperArmIsaacRobotConfig(RobotConfig):
    distribution_zip: str
    expected_sha256: str | None = None
    bridge_mode: Literal["managed", "external"] = "managed"
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8765
    external_run_dir: str | None = None
    cameras: dict = field(default_factory=dict)


class SuperArmIsaacRobot(Robot):
    """Expose six policy values while Isaac owns 13 physical joints.

    The first five observations are measured arm positions. The sixth remains
    the last commanded fixed grasp code until a measured-grasp classifier is
    separately validated.
    """

    config_class = SuperArmIsaacRobotConfig
    name = "superarm_isaac"

    def __init__(self, config: SuperArmIsaacRobotConfig, *, runtime_service=None) -> None:
        super().__init__(config)
        self.config = config
        self.runtime_service = runtime_service or service
        self.cameras = make_cameras_from_configs(config.cameras)
        self._connected = False
        self._owns_session = False
        self._logical = [0.0] * len(CANONICAL_FEATURES)

    @property
    def action_features(self) -> dict[str, type]:
        return dict.fromkeys(CANONICAL_FEATURES, float)

    @property
    def observation_features(self) -> dict[str, type | tuple[int, int, int]]:
        features: dict[str, type | tuple[int, int, int]] = dict.fromkeys(
            CANONICAL_FEATURES, float
        )
        for name, camera in self.cameras.items():
            features[name] = (camera.height, camera.width, 3)
        return features

    @property
    def is_connected(self) -> bool:
        runtime = self.runtime_service.runtime
        return bool(
            self._connected
            and runtime
            and runtime.connected
            and all(camera.is_connected for camera in self.cameras.values())
        )

    @property
    def is_calibrated(self) -> bool:
        return True

    def connect(self, calibrate: bool = True) -> None:
        del calibrate
        if self.is_connected:
            return
        runtime = self.runtime_service.runtime
        if runtime and runtime.connected:
            if self.runtime_service.mode != "isaac_sim":
                raise RuntimeError("SuperArm is connected to a non-Isaac runtime")
        else:
            session_args = {
                "isaac_distribution_zip": self.config.distribution_zip,
                "isaac_bridge_mode": self.config.bridge_mode,
                "isaac_host": self.config.bridge_host,
                "isaac_port": self.config.bridge_port,
                "isaac_external_run_dir": self.config.external_run_dir,
            }
            if self.config.expected_sha256 is not None:
                session_args["isaac_expected_sha256"] = self.config.expected_sha256
            self.runtime_service.start_session("isaac_sim", **session_args)
            self._owns_session = True
        attempted_cameras = []
        try:
            for camera in self.cameras.values():
                attempted_cameras.append(camera)
                camera.connect()
        except Exception:
            for camera in reversed(attempted_cameras):
                with suppress(Exception):
                    camera.disconnect()
            if self._owns_session:
                with suppress(Exception):
                    self.runtime_service.disconnect()
            self._connected = False
            self._owns_session = False
            raise
        self._connected = True

    def calibrate(self) -> None:
        return None

    def configure(self) -> None:
        return None

    def get_observation(self) -> dict[str, Any]:
        if not self.is_connected:
            raise RuntimeError("SuperArm Isaac robot is disconnected")
        state = self.runtime_service.runtime.observe()
        arm = state.get("arm", {})
        values = [
            float(arm.get(name, {}).get("position", self._logical[index]))
            for index, name in enumerate(ARM_JOINTS)
        ]
        values.append(self._logical[-1])
        observation = dict(zip(CANONICAL_FEATURES, values, strict=True))
        for name, camera in self.cameras.items():
            observation[name] = camera.async_read()
        return observation

    def capture_observation(self) -> dict[str, Any]:
        observation = self.get_observation()
        state = np.asarray([observation[name] for name in CANONICAL_FEATURES], dtype=np.float32)
        result = {"observation.state": state}
        result.update({name: observation[name] for name in self.cameras})
        return result

    def send_action(self, action: list[float] | dict[str, float] | np.ndarray):
        if not self.is_connected:
            raise RuntimeError("SuperArm Isaac robot is disconnected")
        named = isinstance(action, dict)
        if isinstance(action, np.ndarray):
            action = action.reshape(-1).tolist()
        values = normalize_superarm_action(action)
        self.runtime_service.logical_action(values)
        self._logical = values
        if named:
            return dict(zip(CANONICAL_FEATURES, values, strict=True))
        return np.asarray(values, dtype=np.float32)

    def get_visualization_joints(self) -> dict[str, float]:
        runtime = self.runtime_service.runtime
        state = runtime.observe() if runtime else {}
        physical = {
            name: float(state.get("arm", {}).get(name, {}).get("position", self._logical[index]))
            for index, name in enumerate(ARM_JOINTS)
        }
        physical.update(
            {
                name: float(state.get("hand", {}).get(name, {}).get("position", 0.0))
                for name in HAND_ACTUATORS
            }
        )
        return physical

    def get_visualization_pose(self) -> None:
        return None

    def disconnect(self) -> None:
        cleanup_errors: list[Exception] = []
        for camera in self.cameras.values():
            if camera.is_connected:
                try:
                    camera.disconnect()
                except Exception as exc:
                    cleanup_errors.append(exc)
        if self._owns_session:
            try:
                self.runtime_service.disconnect()
            except Exception as exc:
                cleanup_errors.append(exc)
        self._connected = False
        self._owns_session = False
        if cleanup_errors:
            raise cleanup_errors[0]
