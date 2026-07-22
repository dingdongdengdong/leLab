from __future__ import annotations

import importlib

HAND_JOINTS = [
    f"finger{finger}_motor{motor}"
    for finger in range(1, 5)
    for motor in range(1, 3)
]


def test_combined_record_and_manual_contract_are_mujoco_only(tmp_path, monkeypatch) -> None:
    asset_root = tmp_path / "assets"
    asset_root.mkdir()
    urdf = asset_root / "superarm_amazinghand.urdf"
    model = asset_root / "superarm_amazinghand.xml"
    urdf.write_text("<robot name='superarm_amazinghand' />\n", encoding="utf-8")
    model.write_text("<mujoco model='superarm_amazinghand' />\n", encoding="utf-8")
    monkeypatch.setenv("SUPERARM_ASSET_ROOT", str(asset_root))
    monkeypatch.setenv("SUPERARM_URDF_PATH", str(urdf))
    monkeypatch.setenv("SUPERARM_MUJOCO_MODEL_PATH", str(model))

    from lelab.manual_leader import build_manual_leader_config
    from lelab.utils import config as config_module

    config = importlib.reload(config_module)
    config.ROBOTS_PATH = str(tmp_path / "robots")
    records = config.list_robot_records()

    assert [record["name"] for record in records] == ["SuperArm + AmazingHand"]
    combined = records[0]
    assert combined["robot_backend"] == "superarm_mujoco"
    assert combined["mujoco_model_path"] == str(model)
    assert config.is_robot_record_clean(combined)

    body = build_manual_leader_config(combined)
    assert body["joint_names"] == [
        "joint_rev_1",
        "joint_rev_2",
        "joint_rev_3",
        "joint_rev_4",
        "joint_rev_5",
        "amazinghand_motion",
    ]
    assert [slider["name"] for slider in body["sliders"]] == body["joint_names"][:5]
    assert [motion["name"] for motion in body["hand_motions"]] == ["open", "half_close", "close"]
    assert [motion["code"] for motion in body["hand_motions"]] == [0.0, 0.5, 1.0]
    assert body["start_request"]["robot_backend"] == "superarm_mujoco"
    assert body["start_request"]["mujoco_model_path"] == str(model)
    assert len(body["physical_joint_names"]) == 13
    assert body["physical_joint_names"][5:] == HAND_JOINTS
    assert all(len(preset["action"]) == 6 for preset in body["presets"])


def test_isaac_manual_contract_uses_positive_hand_targets(tmp_path) -> None:
    from pathlib import Path

    from lelab.manual_leader import build_manual_leader_config

    config_path = (
        Path(__file__).resolve().parents[1] / "lelab" / "superarm" / "data" / "superarm_isaac.yaml"
    )
    record = {
        "name": "SuperArm + AmazingHand (Isaac Sim)",
        "robot_backend": "superarm_isaac",
        "superarm_config": str(config_path),
        "follower_config": str(config_path),
        "superarm_asset_root": str(config_path.parents[3]),
        "isaac_distribution_zip": str(tmp_path / "superarm.zip"),
        "isaac_bridge_mode": "managed",
        "isaac_host": "127.0.0.1",
        "isaac_port": 8765,
    }

    body = build_manual_leader_config(record)

    assert body["robot_backend"] == "superarm_isaac"
    assert body["start_request"]["robot_backend"] == "superarm_isaac"
    assert body["start_request"]["isaac_distribution_zip"].endswith("superarm.zip")
    assert [motion["code"] for motion in body["hand_motions"]] == [0.0, 0.5, 1.0]
    close = next(motion for motion in body["hand_motions"] if motion["name"] == "close")
    assert close["joint_targets"]["finger1_motor1"] == 0.95
    assert close["joint_targets"]["finger1_motor2"] == 1.10
    assert all(value >= 0.0 for value in close["joint_targets"].values())
