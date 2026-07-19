"""Direct MuJoCo-backed LeRobot robot for SuperArm and AmazingHand."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from lerobot.cameras.utils import make_cameras_from_configs
from lerobot.robots.config import RobotConfig
from lerobot.robots.robot import Robot

from .actions import CANONICAL_FEATURES, action_to_runtime_commands, normalize_superarm_action
from .mapping import ARM_JOINTS, HAND_ACTUATORS, mujoco_hand_to_urdf
from .service import service


@RobotConfig.register_subclass("superarm_mujoco")
@dataclass(kw_only=True)
class SuperArmMujocoRobotConfig(RobotConfig):
    model_path: str | None = None
    cameras: dict = field(default_factory=dict)


class SuperArmMujocoRobot(Robot):
    """Expose a 6D policy contract while retaining 13D physical visualization."""

    config_class = SuperArmMujocoRobotConfig
    name = "superarm_mujoco"

    def __init__(self, config: SuperArmMujocoRobotConfig, *, runtime_service=None) -> None:
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
        features: dict[str, type | tuple[int, int, int]] = dict.fromkeys(CANONICAL_FEATURES, float)
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
            if self.runtime_service.mode != "mujoco":
                raise RuntimeError("SuperArm is connected to a non-MuJoCo runtime")
        else:
            self.runtime_service.start_session("mujoco", model_path=self.config.model_path)
            self._owns_session = True
        for camera in self.cameras.values():
            camera.connect()
        self._connected = True

    def calibrate(self) -> None:
        return None

    def configure(self) -> None:
        return None

    def get_observation(self) -> dict[str, Any]:
        if not self.is_connected:
            raise RuntimeError("SuperArm MuJoCo robot is disconnected")
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
            raise RuntimeError("SuperArm MuJoCo robot is disconnected")
        named = isinstance(action, dict)
        if isinstance(action, np.ndarray):
            action = action.reshape(-1).tolist()
        values = normalize_superarm_action(action)
        arm, hand = action_to_runtime_commands(values)
        self.runtime_service.action(arm_rad=arm, hand_deg=hand, source="staged")
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
            mujoco_hand_to_urdf(
                {
                    name: float(state.get("hand", {}).get(name, {}).get("position", 0.0))
                    for name in HAND_ACTUATORS
                }
            )
        )
        return physical

    def disconnect(self) -> None:
        for camera in self.cameras.values():
            if camera.is_connected:
                camera.disconnect()
        if self._owns_session:
            self.runtime_service.disconnect()
        self._connected = False
        self._owns_session = False
