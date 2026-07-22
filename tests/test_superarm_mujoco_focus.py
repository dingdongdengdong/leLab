from __future__ import annotations

import subprocess
import sys

import pytest

CANONICAL_FEATURES = [
    "joint_rev_1.pos",
    "joint_rev_2.pos",
    "joint_rev_3.pos",
    "joint_rev_4.pos",
    "joint_rev_5.pos",
    "amazinghand_motion.pos",
]


class _FakeRuntime:
    connected = True

    def observe(self):
        return {
            "arm": {
                f"joint_rev_{index}": {"position": index / 10, "target": index / 10} for index in range(1, 6)
            },
            "hand": {
                f"finger{finger}_motor{motor}": {
                    "position": 0.50 if motor == 1 else -0.56,
                    "target": 0.0,
                }
                for finger in range(1, 5)
                for motor in range(1, 3)
            },
        }


class _FakeService:
    def __init__(self):
        self.runtime = None
        self.mode = None
        self.actions = []

    def start_session(self, mode, **kwargs):
        assert mode == "mujoco"
        self.mode = mode
        self.runtime = _FakeRuntime()
        return {"connected": True, "runtime": mode}

    def action(self, **kwargs):
        self.actions.append(kwargs)
        return {"accepted": True}

    def disconnect(self):
        self.runtime = None
        self.mode = None
        return {"connected": False}


def test_direct_mujoco_robot_keeps_policy_action_six_dimensional():
    from lelab.superarm.robot import SuperArmMujocoRobot, SuperArmMujocoRobotConfig

    fake = _FakeService()
    robot = SuperArmMujocoRobot(SuperArmMujocoRobotConfig(), runtime_service=fake)
    assert list(robot.action_features) == CANONICAL_FEATURES
    assert list(robot.observation_features) == CANONICAL_FEATURES

    robot.connect()
    sent = robot.send_action([0.1, -0.2, 0.3, -0.4, 0.5, 0.48])

    assert list(sent) == pytest.approx([0.1, -0.2, 0.3, -0.4, 0.5, 0.5])
    assert fake.actions[-1]["arm_rad"] == {
        "joint_rev_1": pytest.approx(0.1),
        "joint_rev_2": pytest.approx(-0.2),
        "joint_rev_3": pytest.approx(0.3),
        "joint_rev_4": pytest.approx(-0.4),
        "joint_rev_5": pytest.approx(0.5),
    }
    assert fake.actions[-1]["hand_deg"] == dict.fromkeys(["pointer", "middle", "ring", "thumb"], [55.0, 55.0])
    visualization = robot.get_visualization_joints()
    assert len(visualization) == 13
    for finger in range(1, 5):
        assert visualization[f"finger{finger}_motor1"] == pytest.approx(0.50)
        assert visualization[f"finger{finger}_motor2"] == pytest.approx(0.56)
    assert fake.runtime.observe()["hand"]["finger1_motor2"]["position"] == pytest.approx(-0.56)


def test_host_server_import_does_not_load_isaac_omniverse_modules():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import lelab.server; "
                "forbidden = [name for name in sys.modules "
                "if name == 'isaacsim' or name.startswith(('isaacsim.', 'omni.', 'pxr.'))]; "
                "assert forbidden == [], forbidden"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
