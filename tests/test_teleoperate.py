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


def test_get_joint_positions_uses_physical_viewer_state_for_combined_robot() -> None:
    from lelab.teleoperate import get_joint_positions_from_robot

    class _CombinedRobot:
        def get_visualization_joints(self) -> dict[str, float]:
            return {"joint_rev_1": 0.25, "finger1_motor1": 0.95}

        def get_observation(self) -> dict[str, float]:
            raise AssertionError("logical policy state must not drive the physical viewer")

    assert get_joint_positions_from_robot(_CombinedRobot()) == {
        "joint_rev_1": 0.25,
        "finger1_motor1": 0.95,
    }


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


def test_superarm_mujoco_backend_uses_custom_robot_and_action_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lelab.teleoperate as teleop

    monkeypatch.setattr(teleop, "teleoperation_active", False)

    class _MujocoRobot:
        def __init__(self) -> None:
            self.connected = False
            self.last_action = None

        def connect(self) -> None:
            self.connected = True

        def disconnect(self) -> None:
            self.connected = False

        def get_visualization_joints(self) -> dict[str, float]:
            return {
                "joint_rev_1": 0.1,
                "joint_rev_2": -0.2,
                "joint_rev_3": 0.3,
                "joint_rev_4": -0.4,
                "joint_rev_5": 0.5,
                **{f"finger{finger}_motor{motor}": 0.0 for finger in range(1, 5) for motor in range(1, 3)},
            }

        def send_action(self, action):
            self.last_action = action
            return action

    created = _MujocoRobot()
    monkeypatch.setattr(teleop, "_create_superarm_mujoco_robot", lambda request: created)

    request = teleop.TeleoperateRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="unused",
        follower_config="unused",
        robot_backend="superarm_mujoco",
        mujoco_model_path="/tmp/superarm.xml",
    )
    result = teleop.handle_start_teleoperation(request)

    assert result["success"] is True
    assert result["robot_backend"] == "superarm_mujoco"
    assert result["joint_positions"]["joint_rev_1"] == 0.1

    action = [0.2, -0.2, 0.3, -0.3, 0.4, 1.0]
    action_result = teleop.handle_send_joint_action(teleop.JointActionRequest(action=action))
    assert action_result["success"] is True
    assert action_result["sent_action"] == action

    assert teleop.handle_stop_teleoperation()["success"] is True


def test_superarm_mujoco_backend_skips_so101_calibration(monkeypatch: pytest.MonkeyPatch) -> None:
    import lelab.teleoperate as teleop

    monkeypatch.setattr(teleop, "teleoperation_active", False)

    class _Robot:
        def connect(self) -> None: pass
        def disconnect(self) -> None: pass
        def get_visualization_joints(self) -> dict[str, float]: return {"joint_rev_1": 0.0}
        def send_action(self, action): return action

    captured = {}

    def fake_create(request):
        captured["robot_backend"] = request.robot_backend
        captured["mujoco_model_path"] = request.mujoco_model_path
        return _Robot()

    def fail_so101_setup(*_args, **_kwargs):
        raise AssertionError("SO101 calibration setup must not run for superarm_mujoco")

    monkeypatch.setattr(teleop, "_create_superarm_mujoco_robot", fake_create)
    monkeypatch.setattr(teleop, "setup_calibration_files", fail_so101_setup)

    request = teleop.TeleoperateRequest(
        leader_port="unused", follower_port="unused", leader_config="unused", follower_config="unused",
        robot_backend="superarm_mujoco", mujoco_model_path="/tmp/superarm.xml",
    )
    result = teleop.handle_start_teleoperation(request)

    assert result["success"] is True
    assert captured == {"robot_backend": "superarm_mujoco", "mujoco_model_path": "/tmp/superarm.xml"}
    assert teleop.handle_stop_teleoperation()["success"] is True
