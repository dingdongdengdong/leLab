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
"""Tests for lelab.record — request schemas and handler entry points."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_recording_request_rejects_missing_required_fields() -> None:
    from pydantic import ValidationError

    from lelab.record import RecordingRequest

    with pytest.raises(ValidationError):
        RecordingRequest()


def test_recording_status_handler_exposes_state_fields() -> None:
    from lelab.record import handle_recording_status

    result = handle_recording_status()
    assert isinstance(result, dict)
    # Pinning the exact keys so a rename in handle_recording_status surfaces here.
    assert "recording_active" in result
    assert "current_phase" in result
    assert "session_ended" in result
    assert "available_controls" in result


def test_handle_stop_recording_when_idle_returns_dict(tmp_lerobot_home) -> None:
    from lelab.record import handle_stop_recording

    result = handle_stop_recording()
    assert isinstance(result, dict)


def test_create_record_config_pins_dshow_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, recording must use the DSHOW backend so a camera_index opens
    the same device /available-cameras enumerated (via pygrabber, DSHOW order).
    """
    import lelab.record as record
    from lerobot.cameras.configs import Cv2Backends

    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(record, "setup_calibration_files", lambda leader, follower: ("leader", "follower"))

    request = record.RecordingRequest(
        leader_port="COM_LEADER",
        follower_port="COM_FOLLOWER",
        leader_config="leader",
        follower_config="follower",
        dataset_repo_id="user/dataset",
        single_task="pick up the cube",
        cameras={"wrist": {"type": "opencv", "camera_index": 0, "width": 640, "height": 480, "fps": 30}},
    )

    config = record.create_record_config(request)
    assert config.robot.cameras["wrist"].backend == Cv2Backends.DSHOW


def test_build_camera_configs_uses_default_backend_when_unset() -> None:
    from lelab.record import _build_camera_configs
    from lerobot.cameras.configs import Cv2Backends

    cameras = {"cam": {"type": "opencv", "camera_index": 0, "width": 640, "height": 480, "fps": 30}}
    configs = _build_camera_configs(cameras, Cv2Backends.AVFOUNDATION)

    assert configs["cam"].backend == Cv2Backends.AVFOUNDATION
    assert configs["cam"].fourcc is None
    assert configs["cam"].index_or_path == 0


def test_build_camera_configs_passes_fourcc_through() -> None:
    from lelab.record import _build_camera_configs
    from lerobot.cameras.configs import Cv2Backends

    cameras = {"cam": {"type": "opencv", "camera_index": 0, "fourcc": "MJPG"}}
    configs = _build_camera_configs(cameras, Cv2Backends.ANY)

    assert configs["cam"].fourcc == "MJPG"


def test_build_camera_configs_explicit_backend_overrides_default() -> None:
    from lelab.record import _build_camera_configs
    from lerobot.cameras.configs import Cv2Backends

    cameras = {"cam": {"type": "opencv", "camera_index": 0, "backend": "V4L2"}}
    configs = _build_camera_configs(cameras, Cv2Backends.AVFOUNDATION)

    assert configs["cam"].backend == Cv2Backends.V4L2


def test_build_camera_configs_invalid_backend_raises() -> None:
    from lelab.record import _build_camera_configs
    from lerobot.cameras.configs import Cv2Backends

    cameras = {"cam": {"type": "opencv", "camera_index": 0, "backend": "NOPE"}}
    with pytest.raises(KeyError):
        _build_camera_configs(cameras, Cv2Backends.ANY)


def test_build_camera_configs_skips_non_opencv_type() -> None:
    from lelab.record import _build_camera_configs
    from lerobot.cameras.configs import Cv2Backends

    cameras = {"cam": {"type": "realsense", "camera_index": 0}}
    configs = _build_camera_configs(cameras, Cv2Backends.ANY)

    assert configs == {}


def test_create_record_config_uses_six_control_superarm_robot_and_manual_teleoperator() -> None:
    import lelab.record as record

    workspace = Path(__file__).resolve().parents[1]
    config_path = workspace / "lelab/superarm/data/superarm_mujoco.yaml"
    request = record.RecordingRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="manual",
        follower_config=str(config_path),
        superarm_config=str(config_path),
        superarm_asset_root=str(workspace),
        mujoco_model_path="/tmp/superarm_amazinghand.xml",
        robot_backend="superarm_mujoco",
        input_mode="manual",
        dataset_repo_id="local/superarm_test",
        single_task="test the fixed grasp",
        video=False,
        cameras={
            "wrist": {
                "type": "opencv",
                "camera_index": 0,
                "width": 64,
                "height": 64,
                "fps": 30,
            }
        },
    )

    config = record.create_record_config(request)

    from lerobot.robots import make_robot_from_config
    from lerobot.utils.feature_utils import hw_to_dataset_features

    robot = make_robot_from_config(config.robot)
    assert list(robot.action_features) == [
        "joint_rev_1.pos",
        "joint_rev_2.pos",
        "joint_rev_3.pos",
        "joint_rev_4.pos",
        "joint_rev_5.pos",
        "amazinghand_motion.pos",
    ]
    assert config.teleop.type == "superarm_input"
    assert config.teleop.source == "manual"
    action_feature = hw_to_dataset_features(robot.action_features, "action", False)["action"]
    observation_feature = hw_to_dataset_features(robot.observation_features, "observation", False)[
        "observation.state"
    ]
    assert action_feature["shape"] == (6,)
    assert observation_feature["shape"] == (6,)
    assert action_feature["names"] == list(robot.action_features)
    dataset_observation_features = hw_to_dataset_features(robot.observation_features, "observation", False)
    assert dataset_observation_features["observation.images.wrist"]["shape"] == (64, 64, 3)


def test_create_record_config_wires_so101_leader_to_superarm_mapping() -> None:
    import lelab.record as record

    workspace = Path(__file__).resolve().parents[1]
    config_path = workspace / "lelab/superarm/data/superarm_mujoco.yaml"
    request = record.RecordingRequest(
        leader_port="/dev/ttyACM0",
        follower_port="unused",
        leader_config="my_calibrated_so101",
        follower_config=str(config_path),
        superarm_config=str(config_path),
        superarm_asset_root=str(workspace),
        robot_backend="superarm_mujoco",
        input_mode="so101",
        dataset_repo_id="local/superarm_so101",
        single_task="test SO101 leader mapping",
        video=False,
    )

    config = record.create_record_config(request)

    assert config.teleop.type == "superarm_input"
    assert config.teleop.source == "so101"
    assert config.teleop.port == "/dev/ttyACM0"
    assert config.teleop.leader_id == "my_calibrated_so101"
    assert [item["target"] for item in config.teleop.arm_mapping] == [
        f"joint_rev_{index}.pos" for index in range(1, 6)
    ]
    assert config.teleop.gripper_feature == "gripper.pos"


def test_create_record_config_uses_six_control_isaac_robot() -> None:
    import lelab.record as record

    workspace = Path(__file__).resolve().parents[1]
    config_path = workspace / "lelab/superarm/data/superarm_isaac.yaml"
    request = record.RecordingRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="manual",
        follower_config=str(config_path),
        superarm_config=str(config_path),
        superarm_asset_root=str(workspace),
        robot_backend="superarm_isaac",
        input_mode="manual",
        isaac_distribution_zip="/server/superarm.zip",
        isaac_bridge_mode="managed",
        isaac_host="127.0.0.1",
        isaac_port=8765,
        dataset_repo_id="local/superarm_isaac_test",
        single_task="test Isaac fixed grasp",
        video=False,
    )

    config = record.create_record_config(request)

    from lerobot.robots import make_robot_from_config
    from lerobot.utils.feature_utils import hw_to_dataset_features

    assert config.robot.type == "superarm_isaac"
    assert config.robot.distribution_zip == "/server/superarm.zip"
    robot = make_robot_from_config(config.robot)
    action_feature = hw_to_dataset_features(robot.action_features, "action", False)["action"]
    observation_feature = hw_to_dataset_features(robot.observation_features, "observation", False)[
        "observation.state"
    ]
    assert action_feature["shape"] == (6,)
    assert observation_feature["shape"] == (6,)
    assert config.teleop.type == "superarm_input"
    assert config.teleop.source == "manual"


def test_create_record_config_uses_server_isaac_distribution_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import lelab.record as record

    workspace = Path(__file__).resolve().parents[1]
    config_path = workspace / "lelab/superarm/data/superarm_isaac.yaml"
    monkeypatch.setenv("SUPERARM_ISAAC_DISTRIBUTION_ZIP", "/server/default-superarm.zip")
    request = record.RecordingRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="manual",
        follower_config=str(config_path),
        superarm_config=str(config_path),
        robot_backend="superarm_isaac",
        input_mode="manual",
        dataset_repo_id="local/superarm_isaac_default",
        single_task="test server default distribution",
        video=False,
    )

    config = record.create_record_config(request)

    assert config.robot.distribution_zip == "/server/default-superarm.zip"


def test_act_policy_constructs_with_superarm_six_dimensional_head_and_camera() -> None:
    from lerobot.configs.types import FeatureType, PolicyFeature
    from lerobot.policies.act.configuration_act import ACTConfig
    from lerobot.policies.act.modeling_act import ACTPolicy

    config = ACTConfig(
        input_features={
            "observation.state": PolicyFeature(type=FeatureType.STATE, shape=(6,)),
            "observation.images.wrist": PolicyFeature(type=FeatureType.VISUAL, shape=(3, 64, 64)),
        },
        output_features={
            "action": PolicyFeature(type=FeatureType.ACTION, shape=(6,)),
        },
        device="cpu",
        push_to_hub=False,
        pretrained_backbone_weights=None,
        chunk_size=5,
        n_action_steps=1,
        dim_model=64,
        n_heads=4,
        dim_feedforward=128,
        n_encoder_layers=1,
        n_decoder_layers=1,
        use_vae=False,
    )

    config.validate_features()
    policy = ACTPolicy(config)

    assert config.output_features["action"].shape == (6,)
    assert policy.config.output_features["action"].shape == (6,)


def test_create_record_config_keeps_so101_mapping_before_dataset_boundary() -> None:
    import lelab.record as record

    workspace = Path(__file__).resolve().parents[1]
    config_path = workspace / "lelab/superarm/data/superarm_mujoco.yaml"
    request = record.RecordingRequest(
        leader_port="/dev/ttyACM0",
        follower_port="unused",
        leader_config="leader_calibration",
        follower_config=str(config_path),
        superarm_config=str(config_path),
        superarm_asset_root=str(workspace),
        mujoco_model_path="/tmp/superarm_amazinghand.xml",
        robot_backend="superarm_mujoco",
        input_mode="so101",
        dataset_repo_id="local/superarm_so101_test",
        single_task="test SO101 mapping",
        video=False,
    )

    config = record.create_record_config(request)

    assert config.teleop.source == "so101"
    assert config.teleop.port == "/dev/ttyACM0"
    assert len(config.teleop.arm_mapping) == 5
    assert config.teleop.gripper_feature == "gripper.pos"


def test_manual_superarm_teleoperator_quantizes_sixth_action() -> None:
    from lelab.superarm_teleoperator import (
        SuperArmTeleoperator,
        SuperArmTeleoperatorConfig,
        set_manual_recording_action,
    )

    teleop = SuperArmTeleoperator(SuperArmTeleoperatorConfig(id="test", source="manual"))
    teleop.connect()
    resolved = set_manual_recording_action([0.1, -0.2, 0.3, -0.4, 0.5, 0.77])

    assert resolved["amazinghand_motion.pos"] == 1.0
    assert teleop.get_action() == resolved
    assert len(teleop.action_features) == 6
    teleop.disconnect()


def test_recording_device_setup_rolls_back_robot_when_teleoperator_connect_fails() -> None:
    from lelab.record import _connect_recording_devices

    class Device:
        def __init__(self, *, fail_connect: bool = False) -> None:
            self.fail_connect = fail_connect
            self.connected = False
            self.disconnect_calls = 0

        def connect(self) -> None:
            if self.fail_connect:
                raise RuntimeError("leader unavailable")
            self.connected = True

        def disconnect(self) -> None:
            self.disconnect_calls += 1
            self.connected = False

    robot = Device()
    teleop = Device(fail_connect=True)

    with pytest.raises(RuntimeError, match="leader unavailable"):
        _connect_recording_devices(robot, teleop)

    assert robot.connected is False
    assert robot.disconnect_calls == 1
    assert teleop.disconnect_calls == 1


def test_recording_setup_preserves_connect_error_when_teleoperator_cleanup_fails() -> None:
    from lelab.record import _connect_recording_devices

    class Device:
        def __init__(self, *, connect_error=None, disconnect_error=None) -> None:
            self.connect_error = connect_error
            self.disconnect_error = disconnect_error
            self.disconnect_calls = 0

        def connect(self) -> None:
            if self.connect_error:
                raise self.connect_error

        def disconnect(self) -> None:
            self.disconnect_calls += 1
            if self.disconnect_error:
                raise self.disconnect_error

    robot = Device()
    teleop = Device(
        connect_error=RuntimeError("leader unavailable"),
        disconnect_error=RuntimeError("leader cleanup failed"),
    )

    with pytest.raises(RuntimeError, match="leader unavailable"):
        _connect_recording_devices(robot, teleop)

    assert teleop.disconnect_calls == 1
    assert robot.disconnect_calls == 1
