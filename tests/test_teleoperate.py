# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tests for lelab.teleoperate — request schema and status handlers."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


def test_teleoperate_request_rejects_missing_fields() -> None:
    from pydantic import ValidationError

    from lelab.teleoperate import TeleoperateRequest

    with pytest.raises(ValidationError):
        TeleoperateRequest()


def test_handle_teleoperation_status_returns_dict() -> None:
    from lelab.teleoperate import handle_teleoperation_status

    result = handle_teleoperation_status()
    assert isinstance(result, dict)


def test_handle_get_joint_positions_returns_dict_when_idle() -> None:
    from lelab.teleoperate import handle_get_joint_positions

    result = handle_get_joint_positions()
    assert isinstance(result, dict)


def test_get_joint_positions_from_robot_uses_provided_object() -> None:
    from lelab.teleoperate import get_joint_positions_from_robot
    from tests.mocks import FakeRobot

    robot = FakeRobot()
    robot.connect()
    positions = get_joint_positions_from_robot(robot)
    assert isinstance(positions, dict)


def test_start_teleoperation_reports_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A device that fails to connect must make the start handler return
    success=False (so the UI surfaces the error and doesn't navigate to an
    empty teleop screen) and reset state so a retry isn't blocked. Previously
    the connect ran in a worker thread and the handler always claimed success.
    """
    import lelab.teleoperate as teleop

    monkeypatch.setattr(teleop, "teleoperation_active", False)
    monkeypatch.setattr(teleop, "setup_calibration_files", lambda leader, follower: ("leader", "follower"))

    class _Bus:
        def connect(self) -> None:
            raise RuntimeError("serial port unavailable")

    class _Device:
        def __init__(self, config) -> None:
            self.bus = _Bus()
            self.cameras: dict = {}
            self.disconnected = False

        def disconnect(self) -> None:
            self.disconnected = True

    monkeypatch.setattr(teleop, "SO101Follower", _Device)
    monkeypatch.setattr(teleop, "SO101Leader", _Device)

    request = teleop.TeleoperateRequest(
        leader_port="COM_LEADER",
        follower_port="COM_FOLLOWER",
        leader_config="leader",
        follower_config="follower",
    )
    result = teleop.handle_start_teleoperation(request)

    assert result["success"] is False
    # The message must name the arm that failed (the follower connects first).
    assert "follower" in result["message"].lower()
    assert "COM_FOLLOWER" in result["message"]
    # State must be reset so the next attempt isn't blocked by the mutex.
    assert teleop.teleoperation_active is False


def test_start_teleoperation_disconnects_follower_when_leader_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The partial-connect path: if the follower connects but the leader then
    fails, the follower must be disconnected so its serial port is released.
    """
    import lelab.teleoperate as teleop

    monkeypatch.setattr(teleop, "teleoperation_active", False)
    monkeypatch.setattr(teleop, "setup_calibration_files", lambda leader, follower: ("leader", "follower"))

    class _OkBus:
        def connect(self) -> None:
            pass

    class _FailingBus:
        def connect(self) -> None:
            raise RuntimeError("leader offline")

    class _Follower:
        def __init__(self, config) -> None:
            self.bus = _OkBus()
            self.cameras: dict = {}
            self.disconnected = False

        def disconnect(self) -> None:
            self.disconnected = True

    class _Leader:
        def __init__(self, config) -> None:
            self.bus = _FailingBus()
            self.disconnected = False

        def disconnect(self) -> None:
            self.disconnected = True

    created: dict = {}
    monkeypatch.setattr(
        teleop, "SO101Follower", lambda config: created.setdefault("follower", _Follower(config))
    )
    monkeypatch.setattr(teleop, "SO101Leader", lambda config: created.setdefault("leader", _Leader(config)))

    request = teleop.TeleoperateRequest(
        leader_port="COM_LEADER",
        follower_port="COM_FOLLOWER",
        leader_config="leader",
        follower_config="follower",
    )
    result = teleop.handle_start_teleoperation(request)

    assert result["success"] is False
    assert "leader" in result["message"].lower()
    # The already-connected follower must have been cleaned up.
    assert created["follower"].disconnected is True
    assert teleop.teleoperation_active is False


def test_isaacsim_backend_uses_custom_robot_and_action_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lelab.teleoperate as teleop

    monkeypatch.setattr(teleop, "teleoperation_active", False)

    class _IsaacRobot:
        def __init__(self) -> None:
            self.connected = False
            self.last_action = None

        def connect(self) -> None:
            self.connected = True

        def disconnect(self) -> None:
            self.connected = False

        def get_observation(self) -> dict[str, float]:
            return {
                "right_arm_pitch_joint.pos": 0.1,
                "right_arm_roll_joint.pos": -0.2,
                "right_arm_yaw_joint.pos": 0.3,
                "right_elbow_pitch_joint.pos": -0.4,
                "right_elbow_yaw_joint.pos": 0.5,
                "amazinghand_grasp.pos": 1.0,
            }

        def send_action(self, action):
            self.last_action = action
            return action

    created = _IsaacRobot()
    monkeypatch.setattr(teleop, "_create_isaacsim_rpo_arm_robot", lambda request: created)

    request = teleop.TeleoperateRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="unused",
        follower_config="isaacsim_test/lerobot/rpo_arm_isaacsim.yaml",
        robot_backend="isaacsim_rpo_arm",
    )
    result = teleop.handle_start_teleoperation(request)

    assert result["success"] is True
    assert result["robot_backend"] == "isaacsim_rpo_arm"
    assert result["joint_positions"]["right_arm_pitch_joint"] == 0.1

    action = [0.2, -0.2, 0.3, -0.3, 0.4, 1.0]
    action_result = teleop.handle_send_joint_action(teleop.JointActionRequest(action=action))
    assert action_result["success"] is True
    assert action_result["sent_action"] == action

    stop_result = teleop.handle_stop_teleoperation()
    assert stop_result["success"] is True


def test_isaacsim_backend_default_config_is_roboparty_v2_right_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    import lelab.teleoperate as teleop

    request = teleop.TeleoperateRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="unused",
        follower_config="",
        robot_backend="isaacsim_rpo_arm",
        superarm_ws_path="/workspaces/superarm_ws",
    )

    robot = teleop._create_isaacsim_rpo_arm_robot(request)

    assert robot.config.joint_names == [
        "right_arm_pitch_joint",
        "right_arm_roll_joint",
        "right_arm_yaw_joint",
        "right_elbow_pitch_joint",
        "right_elbow_yaw_joint",
        "amazinghand_grasp",
    ]



def test_isaacsim_backend_ignores_manual_leader_yaml_metadata(tmp_path: Path) -> None:
    import lelab.teleoperate as teleop

    superarm_ws = tmp_path / "superarm_ws"
    lerobot_dir = superarm_ws / "isaacsim_test" / "lerobot"
    lerobot_dir.mkdir(parents=True)
    source_shim = Path(__file__).resolve().parents[3] / "isaacsim_test" / "lerobot" / "isaacsim_rpo_arm_robot.py"
    shutil.copy2(source_shim, lerobot_dir / "isaacsim_rpo_arm_robot.py")
    config_path = lerobot_dir / "amazinghand_isaacsim_hand_only.yaml"
    config_path.write_text(
        "\n".join([
            "_type: isaacsim_rpo_arm",
            "joint_names: [finger1_motor1, finger1_motor2]",
            "allow_custom_joint_names: true",
            "manual_leader:",
            "  kind: amazinghand",
            "  slider_min: 0.0",
        ]),
        encoding="utf-8",
    )

    request = teleop.TeleoperateRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="unused",
        follower_config=str(config_path),
        isaacsim_config=str(config_path),
        robot_backend="isaacsim_rpo_arm",
        superarm_ws_path=str(superarm_ws),
    )

    robot = teleop._create_isaacsim_rpo_arm_robot(request)

    assert robot.config.joint_names == ["finger1_motor1", "finger1_motor2"]
    assert robot.config.allow_custom_joint_names is True

def test_start_teleoperation_accepts_isaacsim_config_without_so101_calibration(monkeypatch: pytest.MonkeyPatch) -> None:
    import lelab.teleoperate as teleop

    monkeypatch.setattr(teleop, "teleoperation_active", False)

    class _IsaacRobot:
        def connect(self) -> None:
            pass

        def disconnect(self) -> None:
            pass

        def get_observation(self) -> dict[str, float]:
            return {
                "joint_rev_1.pos": 0.0,
                "joint_rev_2.pos": 0.0,
                "joint_rev_3.pos": 0.0,
                "joint_rev_4.pos": 0.0,
                "joint_rev_5.pos": 0.0,
            }

        def send_action(self, action):
            return action

    captured = {}

    def fake_create(request):
        captured["robot_backend"] = request.robot_backend
        captured["follower_config"] = request.follower_config
        captured["isaacsim_config"] = request.isaacsim_config
        return _IsaacRobot()

    def fail_so101_setup(*_args, **_kwargs):
        raise AssertionError("SO101 calibration setup must not run for isaacsim_rpo_arm")

    monkeypatch.setattr(teleop, "_create_isaacsim_rpo_arm_robot", fake_create)
    monkeypatch.setattr(teleop, "setup_calibration_files", fail_so101_setup)

    request = teleop.TeleoperateRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="unused",
        follower_config="unused",
        robot_backend="isaacsim_rpo_arm",
        isaacsim_config="/workspaces/superarm_ws/isaacsim_test/lerobot/source_arm_isaacsim_arm_only.yaml",
        superarm_ws_path="/workspaces/superarm_ws",
    )
    result = teleop.handle_start_teleoperation(request)

    assert result["success"] is True
    assert captured == {
        "robot_backend": "isaacsim_rpo_arm",
        "follower_config": "unused",
        "isaacsim_config": "/workspaces/superarm_ws/isaacsim_test/lerobot/source_arm_isaacsim_arm_only.yaml",
    }

    assert teleop.handle_stop_teleoperation()["success"] is True
