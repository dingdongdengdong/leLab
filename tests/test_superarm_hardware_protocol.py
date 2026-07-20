"""Offline protocol tests for the real SuperArm hardware boundary."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import yaml

from lelab.superarm.hardware import (
    DM4340P_LEROBOT_TYPE,
    SuperArmDm4340PAmazingHandConfig,
    SuperArmDm4340PAmazingHandRobot,
    arm_radians_to_openarm_degrees,
    openarm_degrees_to_arm_radians,
    validate_arm_joint_calibration,
    validate_arm_limits_and_gains,
    validate_dm4340p_arm_motors,
)
from lelab.superarm.mapping import AMAZINGHAND_CONTROL_SERVO_PAIRS, ARM_JOINTS, degrees_to_hardware_radians


def _dm4340p_mapping() -> dict[str, tuple[int, int, str]]:
    return {
        name: (index, index + 0x10, DM4340P_LEROBOT_TYPE) for index, name in enumerate(ARM_JOINTS, start=1)
    }


def _joint_calibration() -> dict[str, tuple[float, float]]:
    return dict.fromkeys(ARM_JOINTS, (1.0, 0.0))


def _joint_limits() -> dict[str, tuple[float, float]]:
    return dict.fromkeys(ARM_JOINTS, (-10.0, 10.0))


def test_amazinghandcontrol_servo_pair_order_and_direction_are_preserved() -> None:
    assert AMAZINGHAND_CONTROL_SERVO_PAIRS == [(5, 6), (3, 4), (1, 2), (7, 8)]
    assert degrees_to_hardware_radians(5, 55) == pytest.approx(math.radians(55))
    assert degrees_to_hardware_radians(6, 55) == pytest.approx(-math.radians(55))


@pytest.mark.parametrize("servo_id", [0, 9])
def test_amazinghand_protocol_rejects_unknown_servo_ids(servo_id: int) -> None:
    with pytest.raises(ValueError, match="servo ID"):
        degrees_to_hardware_radians(servo_id, 0)


def test_dm4340p_mapping_requires_custom_five_joint_can_ids() -> None:
    mapping = _dm4340p_mapping()
    validate_dm4340p_arm_motors(mapping)

    duplicate = dict(mapping)
    duplicate["joint_rev_5"] = (1, 0x15, DM4340P_LEROBOT_TYPE)
    with pytest.raises(ValueError, match="unique"):
        validate_dm4340p_arm_motors(duplicate)

    wrong_type = dict(mapping)
    wrong_type["joint_rev_1"] = (1, 0x11, "dm4310")
    with pytest.raises(ValueError, match="dm4340"):
        validate_dm4340p_arm_motors(wrong_type)

    overlap = dict(mapping)
    overlap["joint_rev_5"] = (5, 1, DM4340P_LEROBOT_TYPE)
    with pytest.raises(ValueError, match="not overlap"):
        validate_dm4340p_arm_motors(overlap)


def test_custom_arm_radians_round_trip_through_lerobot_openarm_degrees() -> None:
    source = {name: (index - 3) * 0.2 for index, name in enumerate(ARM_JOINTS, start=1)}
    calibration = {
        name: (-1.0 if index % 2 else 1.0, (index - 3) * 0.01)
        for index, name in enumerate(ARM_JOINTS, start=1)
    }
    validate_arm_joint_calibration(calibration)
    action = arm_radians_to_openarm_degrees(source, calibration)
    assert list(action) == [f"joint_{index}.pos" for index in range(1, 6)]
    restored = openarm_degrees_to_arm_radians(action, calibration)
    assert restored == pytest.approx(source)

    incomplete = dict(calibration)
    incomplete.pop("joint_rev_5")
    with pytest.raises(ValueError, match="exactly"):
        validate_arm_joint_calibration(incomplete)

    validate_arm_limits_and_gains(_joint_limits(), [10.0] * 5, [1.0] * 5)
    with pytest.raises(ValueError, match="measured"):
        validate_arm_limits_and_gains(_joint_limits(), [10.0] * 4, [1.0] * 5)


def test_hardware_example_cannot_connect_with_openarm_sample_ids() -> None:
    path = Path(__file__).parents[1] / "lelab/superarm/data/superarm_dm4340p_amazinghand.example.yaml"
    example = yaml.safe_load(path.read_text(encoding="utf-8"))
    with pytest.raises(ValueError, match="within"):
        validate_dm4340p_arm_motors(example["arm_motor_config"])


class _FakeArm:
    is_connected = True
    is_calibrated = True

    def __init__(self) -> None:
        self.actions: list[dict[str, float]] = []
        self.disconnected = False

    def send_action(self, action: dict[str, float]) -> None:
        self.actions.append(action)

    def get_observation(self) -> dict[str, float]:
        return {f"joint_{index}.pos": 0.0 for index in range(1, 6)}

    def disconnect(self) -> None:
        self.disconnected = True
        self.is_connected = False


class _FakeHand:
    connected = True

    def __init__(self) -> None:
        self.commands: list[tuple[dict[str, list[float]], dict[str, list[int]]]] = []
        self.closed = False

    def command(self, hand_deg: dict[str, list[float]], hand_speed: dict[str, list[int]]) -> None:
        self.commands.append((hand_deg, hand_speed))

    def observe(self) -> dict[str, dict[str, float]]:
        return {}

    def close(self) -> None:
        self.closed = True
        self.connected = False


class _ConnectableFakeArm(_FakeArm):
    is_connected = False

    def connect(self, calibrate: bool = True) -> None:
        del calibrate
        self.is_connected = True


class _FailingConnectHand(_FakeHand):
    connected = False

    def connect(self) -> None:
        raise RuntimeError("hand bus unavailable")


def test_combined_hardware_robot_routes_six_controls_to_two_protocols(tmp_path) -> None:
    config = SuperArmDm4340PAmazingHandConfig(
        arm_port="can0",
        arm_motor_config=_dm4340p_mapping(),
        arm_joint_calibration=_joint_calibration(),
        arm_joint_limits_deg=_joint_limits(),
        arm_position_kp=[10.0] * 5,
        arm_position_kd=[1.0] * 5,
        calibration_dir=tmp_path,
    )
    robot = SuperArmDm4340PAmazingHandRobot(config)
    arm = _FakeArm()
    hand = _FakeHand()
    robot._arm = arm
    robot._hand = hand

    assert list(robot.action_features) == [
        "joint_rev_1.pos",
        "joint_rev_2.pos",
        "joint_rev_3.pos",
        "joint_rev_4.pos",
        "joint_rev_5.pos",
        "amazinghand_motion.pos",
    ]
    robot.send_action([0.1, 0.2, 0.3, 0.4, 0.5, 1.0])
    assert set(arm.actions[0]) == {f"joint_{index}.pos" for index in range(1, 6)}
    assert hand.commands == [
        (
            {finger: [110.0, 110.0] for finger in ["pointer", "middle", "ring", "thumb"]},
            {finger: [3, 3] for finger in ["pointer", "middle", "ring", "thumb"]},
        )
    ]

    robot.disconnect()
    assert arm.disconnected is True
    assert hand.closed is True


def test_hand_connect_failure_disconnects_the_can_arm(tmp_path, monkeypatch) -> None:
    config = SuperArmDm4340PAmazingHandConfig(
        arm_port="can0",
        arm_motor_config=_dm4340p_mapping(),
        arm_joint_calibration=_joint_calibration(),
        arm_joint_limits_deg=_joint_limits(),
        arm_position_kp=[10.0] * 5,
        arm_position_kd=[1.0] * 5,
        calibration_dir=tmp_path,
    )
    robot = SuperArmDm4340PAmazingHandRobot(config)
    arm = _ConnectableFakeArm()
    hand = _FailingConnectHand()
    monkeypatch.setattr(robot, "_make_arm", lambda: arm)
    robot._hand = hand

    with pytest.raises(RuntimeError, match="hand bus"):
        robot.connect()

    assert arm.disconnected is True
    assert hand.closed is True
