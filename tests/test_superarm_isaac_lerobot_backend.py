from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from isaacsim_validation.contracts import PHYSICAL_JOINTS
from lelab.superarm.actions import CANONICAL_FEATURES, action_to_isaac_targets


class FakeRuntime:
    supports_video = False
    supports_capture = True
    failure = None

    def __init__(self):
        self.connected = True
        self.last_targets = None
        self.positions = {
            name: (index + 1) / 100 for index, name in enumerate(PHYSICAL_JOINTS)
        }

    def observe(self):
        return {
            "runtime": "isaac_sim",
            "arm": {
                name: {"position": self.positions[name], "target": self.positions[name]}
                for name in PHYSICAL_JOINTS[:5]
            },
            "hand": {
                name: {"position": self.positions[name], "target": self.positions[name]}
                for name in PHYSICAL_JOINTS[5:]
            },
        }


class FakeService:
    def __init__(self, *, mode=None, connected=False):
        self.runtime = FakeRuntime() if connected else None
        self.mode = mode
        self.start_calls = []
        self.disconnect_calls = 0

    def start_session(self, mode, **kwargs):
        self.start_calls.append((mode, kwargs))
        self.mode = mode
        self.runtime = FakeRuntime()
        return {"connected": True, "runtime": mode}

    def logical_action(self, values):
        targets = action_to_isaac_targets(values)
        self.runtime.last_targets = targets
        return {"accepted": True, "logical_action": list(values)}

    def disconnect(self):
        self.disconnect_calls += 1
        self.runtime.connected = False
        self.runtime = None
        self.mode = None
        return {"connected": False}


def test_isaac_robot_keeps_policy_width_six_and_physical_targets_thirteen():
    from lelab.superarm.isaac_robot import SuperArmIsaacRobot, SuperArmIsaacRobotConfig

    service = FakeService()
    config = SuperArmIsaacRobotConfig(
        distribution_zip="/server/superarm.zip",
        bridge_mode="managed",
        bridge_host="127.0.0.1",
        bridge_port=8765,
    )
    robot = SuperArmIsaacRobot(config, runtime_service=service)

    assert list(robot.action_features) == CANONICAL_FEATURES
    assert list(robot.observation_features) == CANONICAL_FEATURES
    robot.connect()
    sent = robot.send_action([0.1, -0.1, 0.2, -0.2, 0.05, 1.0])

    assert sent.shape == (6,)
    assert len(service.runtime.last_targets) == 13
    assert service.runtime.last_targets["finger1_motor2"] == pytest.approx(1.10)
    assert service.start_calls == [
        (
            "isaac_sim",
            {
                "isaac_distribution_zip": "/server/superarm.zip",
                "isaac_bridge_mode": "managed",
                "isaac_host": "127.0.0.1",
                "isaac_port": 8765,
                "isaac_external_run_dir": None,
            },
        )
    ]
    with pytest.raises(ValueError, match="exactly 6"):
        robot.send_action([0.0] * 13)
    robot.disconnect()
    assert service.disconnect_calls == 1


def test_isaac_observation_uses_measured_arm_and_commanded_grasp_semantics():
    from lelab.superarm.isaac_robot import SuperArmIsaacRobot, SuperArmIsaacRobotConfig

    service = FakeService(mode="isaac_sim", connected=True)
    robot = SuperArmIsaacRobot(
        SuperArmIsaacRobotConfig(distribution_zip="/server/superarm.zip"),
        runtime_service=service,
    )
    robot.connect()
    robot.send_action(np.asarray([0.0, 0.0, 0.0, 0.0, 0.0, 0.6], dtype=np.float32))

    observation = robot.get_observation()
    assert [observation[name] for name in CANONICAL_FEATURES[:5]] == pytest.approx(
        [service.runtime.positions[name] for name in PHYSICAL_JOINTS[:5]]
    )
    assert observation[CANONICAL_FEATURES[-1]] == 0.5
    captured = robot.capture_observation()["observation.state"]
    assert captured.shape == (6,)
    assert captured[-1] == pytest.approx(0.5)

    visualization = robot.get_visualization_joints()
    assert list(visualization) == list(PHYSICAL_JOINTS)
    for name in PHYSICAL_JOINTS:
        assert visualization[name] == pytest.approx(service.runtime.positions[name])

    robot.disconnect()
    assert service.disconnect_calls == 0


def test_isaac_robot_rejects_an_active_non_isaac_session():
    from lelab.superarm.isaac_robot import SuperArmIsaacRobot, SuperArmIsaacRobotConfig

    service = FakeService(mode="mujoco", connected=True)
    robot = SuperArmIsaacRobot(
        SuperArmIsaacRobotConfig(distribution_zip="/server/superarm.zip"),
        runtime_service=service,
    )

    with pytest.raises(RuntimeError, match="non-Isaac"):
        robot.connect()

    assert service.start_calls == []


def test_isaac_yaml_keeps_the_canonical_six_and_thirteen_joint_contracts():
    config_path = (
        Path(__file__).resolve().parents[1] / "lelab" / "superarm" / "data" / "superarm_isaac.yaml"
    )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["_type"] == "superarm_isaac"
    assert config["joint_names"] == [name.removesuffix(".pos") for name in CANONICAL_FEATURES]
    assert config["physical_joint_names"] == list(PHYSICAL_JOINTS)
    assert config["isaac"] == {
        "bridge_mode": "managed",
        "host": "127.0.0.1",
        "port": 8765,
        "image": "nvcr.io/nvidia/isaac-sim:6.0.0",
        "distribution_env": "SUPERARM_ISAAC_DISTRIBUTION_ZIP",
    }
