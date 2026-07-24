from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from isaacsim_validation.bridge_protocol import SCHEMA, ProtocolError, validate_request
from lelab.rl.config import DEFAULT_DISTRIBUTION_SHA256, ReinforcementLearningRequest
from lelab.rl.contracts import map_policy_action, reward_terms, state_vector
from lelab.rl.env import SuperArmIsaacPickLiftEnv, register_superarm_isaac_env
from lelab.rl.frame import read_frame, write_frame_atomic


def _state() -> dict:
    return {
        "joint_positions": [1, 2, 3, 4, 5],
        "joint_velocities": [6, 7, 8, 9, 10],
        "grasp_state": 0.5,
        "end_effector_xyz": [11, 12, 13],
        "cube_xyz": [14, 15, 16],
        "cube_linear_velocity_xyz": [17, 18, 19],
        "end_effector_to_cube_xyz": [20, 21, 22],
    }


def test_action_mapping_scales_clamps_and_maps_discrete_grasp() -> None:
    current = {f"joint_rev_{i}": value for i, value in enumerate([1.56, 0, 0, 0, -1.56], 1)}
    targets, grasp = map_policy_action([1, 0.5, -0.5, -2, 2, 1.6], current)
    assert targets["joint_rev_1"] == pytest.approx(1.57)
    assert targets["joint_rev_2"] == pytest.approx(0.02)
    assert targets["joint_rev_3"] == pytest.approx(-0.02)
    assert targets["joint_rev_4"] == pytest.approx(-0.04)
    assert targets["joint_rev_5"] == pytest.approx(-1.52)
    assert grasp == 1.0


def test_state_vector_has_exact_documented_order() -> None:
    assert state_vector(_state()).tolist() == [
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0.5, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22
    ]


def test_reward_terms_are_separate_and_bounded() -> None:
    terms = reward_terms(
        ee_to_cube_xyz=[0.03, 0.04, 0],
        cube_height=0.22,
        previous_cube_height=0.10,
        table_height=0.1,
        arm_action=[1, 0, 0, 0, 0],
        grasp_changed=True,
        success=True,
    )
    assert terms.distance == pytest.approx(-0.1)
    assert terms.lift_progress == pytest.approx(2.0)
    assert terms.success == 10.0
    assert terms.action == pytest.approx(-0.01)
    assert terms.grasp_change == -0.02


def test_frame_descriptor_is_root_bounded_and_image_is_256_rgb(tmp_path: Path) -> None:
    frame = np.zeros((256, 256, 3), dtype=np.uint8)
    path = write_frame_atomic(frame, tmp_path / "workspace.png")
    descriptor = {"path": path.name, "width": 256, "height": 256, "channels": 3, "sequence": 1}
    assert read_frame(descriptor, tmp_path).shape == (256, 256, 3)
    outside = tmp_path.parent / "outside.png"
    write_frame_atomic(frame, outside)
    descriptor["path"] = str(outside)
    with pytest.raises(ValueError, match="allowed root"):
        read_frame(descriptor, tmp_path)


def test_rl_protocol_validates_reset_and_atomic_step() -> None:
    token = "secret"
    reset = validate_request(
        {"schema": SCHEMA, "request_id": "1", "token": token, "op": "rl_reset", "seed": 42},
        expected_token=token,
    )
    assert reset == {"request_id": "1", "op": "rl_reset", "seed": 42, "max_steps": 150}
    step = validate_request(
        {
            "schema": SCHEMA,
            "request_id": "2",
            "token": token,
            "op": "rl_step",
            "arm_targets": {f"joint_rev_{i}": 0.0 for i in range(1, 6)},
            "grasp": 0.5,
        },
        expected_token=token,
    )
    assert step["grasp"] == 0.5
    with pytest.raises(ProtocolError):
        validate_request(
            {"schema": SCHEMA, "request_id": "3", "token": token, "op": "rl_step", "arm_targets": {}, "grasp": 0},
            expected_token=token,
        )


def test_request_defaults_and_resource_validation(tmp_path: Path) -> None:
    request = ReinforcementLearningRequest(distribution_zip=str(tmp_path / "robot.zip"))
    assert request.distribution_sha256 == DEFAULT_DISTRIBUTION_SHA256
    assert request.episode_length_steps == 150
    with pytest.raises(ValueError, match="confirmed passive/no-shell V3"):
        ReinforcementLearningRequest(
            distribution_zip=str(tmp_path / "robot.zip"),
            distribution_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="different"):
        ReinforcementLearningRequest(
            distribution_zip=str(tmp_path / "robot.zip"), learner_port=50051, bridge_port=50051
        )


class _FakeClient:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.closed = False
        self.reset_seeds: list[int] = []
        self.steps: list[tuple[dict[str, float], float]] = []

    def connect(self) -> dict:
        return {}

    def _payload(self) -> dict:
        write_frame_atomic(np.zeros((256, 256, 3), dtype=np.uint8), self.root / "workspace.png")
        return {
            "state": {
                "joint_positions": [0] * 5,
                "joint_velocities": [0] * 5,
                "grasp_state": 0,
                "end_effector_xyz": [0] * 3,
                "cube_xyz": [0] * 3,
                "cube_linear_velocity_xyz": [0] * 3,
                "end_effector_to_cube_xyz": [0] * 3,
            },
            "frame": {"path": "workspace.png", "width": 256, "height": 256, "channels": 3, "sequence": 1},
            "reward": 0.0,
            "terminated": False,
            "truncated": False,
            "info": {},
        }

    def rl_reset(self, seed: int, max_steps: int = 150) -> dict:
        assert max_steps == 150
        self.reset_seeds.append(seed)
        return self._payload()

    def rl_step(self, targets: dict[str, float], grasp: float) -> dict:
        self.steps.append((targets, grasp))
        return self._payload()

    def close(self) -> None:
        self.closed = True


def test_gym_adapter_is_deterministic_and_never_intervenes(tmp_path: Path) -> None:
    register_superarm_isaac_env()
    client = _FakeClient(tmp_path)
    env = SuperArmIsaacPickLiftEnv(client=client, frame_root=tmp_path)
    first, info = env.reset(seed=7)
    second, _ = env.reset(seed=7)
    assert client.reset_seeds == [7, 7]
    assert np.array_equal(first["observation.state"], second["observation.state"])
    assert first["observation.image.workspace"].shape == (256, 256, 3)
    assert info["is_intervention"] is False
    _obs, _reward, _terminated, _truncated, step_info = env.step(np.zeros(6, dtype=np.float32))
    assert step_info["is_intervention"] is False
    env.close()
    assert client.closed


def test_generated_config_decodes_as_upstream_lerobot_hilserl(tmp_path):
    from lelab.rl.runtime_config import write_lerobot_config
    from lerobot.rl.train_rl import TrainRLServerPipelineConfig

    request = ReinforcementLearningRequest(distribution_zip=str(tmp_path / "robot.zip"))
    path = write_lerobot_config(request, tmp_path / "run")
    decoded = TrainRLServerPipelineConfig.from_pretrained(path, cli_args=[])
    assert decoded.policy.online_step_before_learning == 100
    assert decoded.policy.actor_learner_config.learner_port == 50051
    assert decoded.env.task == "SuperArmIsaacPickLift-v0"


def test_rl_readiness_requires_verified_isaac_image_and_x11_display(tmp_path, monkeypatch):
    from lelab.rl import readiness

    distribution = tmp_path / "robot.zip"
    distribution.write_bytes(b"distribution")
    monkeypatch.setattr(readiness, "DEFAULT_DISTRIBUTION_SHA256", "unused")
    monkeypatch.setattr(readiness.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(readiness.importlib.util, "find_spec", lambda _name: object())
    inspected = []

    def fake_run(command, **_kwargs):
        inspected.append(command)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(readiness.subprocess, "run", fake_run)
    monkeypatch.setattr(readiness, "_display_socket_available", lambda display: display == ":100")

    result = readiness.check_rl_readiness(
        str(distribution),
        learner_port=50051,
        bridge_port=8765,
        display=":100",
    )

    assert inspected == [["docker", "image", "inspect", "nvcr.io/nvidia/isaac-sim:6.0.1"]]
    assert result["checks"]["isaac_sim_6_0_1_image"] is True
    assert result["checks"]["rl_x11_display"] is True
    assert result["distribution_zip"] == str(distribution)
