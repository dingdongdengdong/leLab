"""Gymnasium adapter for the LeLab-owned Isaac pick-and-lift task."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import gymnasium as gym
import numpy as np

from lelab.superarm.isaac_protocol import IsaacBridgeClient

from .contracts import ARM_JOINTS, IMAGE_SHAPE, map_policy_action, state_vector
from .frame import read_frame

ENV_ID = "gym_hil/SuperArmIsaacPickLift-v0"


class SuperArmIsaacPickLiftEnv(gym.Env):
    """Single-environment Isaac adapter used by LeRobot's unmodified actor."""

    metadata = {"render_modes": ["human", "rgb_array"], "render_fps": 10}

    def __init__(
        self,
        *,
        image_obs: bool = True,
        render_mode: str | None = None,
        use_gripper: bool = True,
        gripper_penalty: float = -0.02,
        client: IsaacBridgeClient | None = None,
        frame_root: str | Path | None = None,
        client_factory: Callable[..., IsaacBridgeClient] = IsaacBridgeClient,
    ) -> None:
        super().__init__()
        if not image_obs or not use_gripper:
            raise ValueError("SuperArm Isaac V1 requires workspace RGB and the AmazingHand grasp action")
        if render_mode not in {None, "human", "rgb_array"}:
            raise ValueError(f"unsupported render mode: {render_mode}")
        if abs(float(gripper_penalty) - -0.02) > 1e-9:
            raise ValueError("SuperArm Isaac uses the fixed -0.02 grasp-change penalty")

        self.render_mode = render_mode
        self.observation_space = gym.spaces.Dict(
            {
                "agent_pos": gym.spaces.Box(-np.inf, np.inf, shape=(23,), dtype=np.float32),
                "pixels": gym.spaces.Dict(
                    {"workspace": gym.spaces.Box(0, 255, shape=IMAGE_SHAPE, dtype=np.uint8)}
                ),
            }
        )
        # The sixth scalar is rounded to the categorical open/half/close index.
        self.action_space = gym.spaces.Box(
            low=np.asarray([-1.0] * 5 + [0.0], dtype=np.float32),
            high=np.asarray([1.0] * 5 + [2.0], dtype=np.float32),
            dtype=np.float32,
        )
        self._frame_root = Path(frame_root or os.environ["LELAB_RL_FRAME_ROOT"])
        self._client = client or client_factory(
            os.environ.get("LELAB_RL_BRIDGE_HOST", "127.0.0.1"),
            int(os.environ.get("LELAB_RL_BRIDGE_PORT", "8765")),
            token=os.environ["LELAB_RL_BRIDGE_TOKEN"],
            timeout_s=float(os.environ.get("LELAB_RL_BRIDGE_TIMEOUT_S", "10")),
        )
        self._client.connect()
        self._current_positions = dict.fromkeys(ARM_JOINTS, 0.0)
        self._last_frame: np.ndarray | None = None
        self._closed = False

    def _observation(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = state_vector(payload["state"])
        frame = read_frame(payload["frame"], self._frame_root)
        self._current_positions = dict(zip(ARM_JOINTS, state[:5], strict=True))
        self._last_frame = frame
        return {"agent_pos": state, "pixels": {"workspace": frame}}

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        del options
        super().reset(seed=seed)
        selected_seed = int(seed if seed is not None else self.np_random.integers(0, 2**32 - 1))
        payload = self._client.rl_reset(selected_seed)
        info = dict(payload.get("info") or {})
        info["is_intervention"] = False
        return self._observation(payload), info

    def step(self, action: np.ndarray) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        arm_targets, grasp = map_policy_action(action, self._current_positions)
        payload = self._client.rl_step(arm_targets, grasp)
        info = dict(payload.get("info") or {})
        info["is_intervention"] = False
        return (
            self._observation(payload),
            float(payload["reward"]),
            bool(payload["terminated"]),
            bool(payload["truncated"]),
            info,
        )

    def render(self) -> np.ndarray | None:
        return None if self._last_frame is None else self._last_frame.copy()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._client.close()


def register_superarm_isaac_env() -> None:
    if ENV_ID not in gym.registry:
        gym.register(id=ENV_ID, entry_point="lelab.rl.env:SuperArmIsaacPickLiftEnv")
