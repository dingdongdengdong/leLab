"""LeRobot teleoperator adapters for the six-control SuperArm follower."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Any

from lerobot.teleoperators.config import TeleoperatorConfig
from lerobot.teleoperators.teleoperator import Teleoperator

from .superarm.actions import CANONICAL_FEATURES, SO101ToSuperArmActionAdapter, resolve_motion_code


class _ManualActionSource:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._action = dict.fromkeys(CANONICAL_FEATURES, 0.0)

    def set(self, action: list[float] | dict[str, float]) -> dict[str, float]:
        if isinstance(action, dict):
            if set(action) != set(CANONICAL_FEATURES):
                raise ValueError(f"Manual recording action must use features {CANONICAL_FEATURES}")
            values = [float(action[name]) for name in CANONICAL_FEATURES]
        else:
            if len(action) != len(CANONICAL_FEATURES):
                raise ValueError(f"Manual recording action must contain exactly 6 values, got {len(action)}")
            values = [float(value) for value in action]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Manual recording action values must be finite")
        values[:5] = [max(-math.pi, min(math.pi, value)) for value in values[:5]]
        values[-1] = resolve_motion_code(values[-1])
        resolved = dict(zip(CANONICAL_FEATURES, values, strict=True))
        with self._lock:
            self._action = resolved
        return dict(resolved)

    def get(self) -> dict[str, float]:
        with self._lock:
            return dict(self._action)


_manual_action_source = _ManualActionSource()


def set_manual_recording_action(action: list[float] | dict[str, float]) -> dict[str, float]:
    return _manual_action_source.set(action)


@TeleoperatorConfig.register_subclass("superarm_input")
@dataclass(kw_only=True)
class SuperArmTeleoperatorConfig(TeleoperatorConfig):
    source: str = "manual"
    port: str = "unused"
    leader_id: str = "superarm_leader"
    arm_mapping: list[dict[str, Any]] = field(default_factory=list)
    arm_limits: dict[str, dict[str, float]] = field(default_factory=dict)
    gripper_feature: str = "gripper.pos"
    motion_hysteresis: float = 0.05


class SuperArmTeleoperator(Teleoperator):
    config_class = SuperArmTeleoperatorConfig
    name = "superarm_input"

    def __init__(self, config: SuperArmTeleoperatorConfig) -> None:
        super().__init__(config)
        if config.source not in {"manual", "so101"}:
            raise ValueError("SuperArm input source must be 'manual' or 'so101'")
        self.config = config
        self._leader = None
        self._adapter = None
        self._is_connected = False

    @property
    def action_features(self) -> dict[str, type]:
        return dict.fromkeys(CANONICAL_FEATURES, float)

    @property
    def feedback_features(self) -> dict:
        return {}

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    @property
    def is_calibrated(self) -> bool:
        return True if self._leader is None else self._leader.is_calibrated

    def connect(self, calibrate: bool = True) -> None:
        if self._is_connected:
            return
        if self.config.source == "so101":
            from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig

            self._leader = SO101Leader(
                SO101LeaderConfig(
                    port=self.config.port,
                    id=self.config.leader_id,
                    use_degrees=True,
                )
            )
            self._adapter = SO101ToSuperArmActionAdapter(
                arm_mapping=self.config.arm_mapping,
                arm_limits=self.config.arm_limits,
                gripper_feature=self.config.gripper_feature,
                motion_hysteresis=self.config.motion_hysteresis,
            )
            self._leader.connect(calibrate=calibrate)
        self._is_connected = True

    def calibrate(self) -> None:
        if self._leader is not None:
            self._leader.calibrate()

    def configure(self) -> None:
        return None

    def get_action(self) -> dict[str, float]:
        if not self._is_connected:
            raise RuntimeError("SuperArm teleoperator is not connected")
        if self._leader is None:
            return _manual_action_source.get()
        return self._adapter(self._leader.get_action())

    def send_feedback(self, feedback: dict[str, Any]) -> None:
        del feedback

    def disconnect(self) -> None:
        if self._leader is not None and self._leader.is_connected:
            self._leader.disconnect()
        self._is_connected = False
