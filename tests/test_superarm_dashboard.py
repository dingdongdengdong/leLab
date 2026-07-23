from __future__ import annotations

import io
import math
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from lelab.superarm.api import ActionRequest
from lelab.superarm.mapping import (
    degrees_to_hardware_radians,
    degrees_to_mujoco,
    mujoco_hand_to_urdf,
    named_to_upstream_positions,
    upstream_positions_to_named,
)
from lelab.superarm.programs import ProgramStore
from lelab.superarm.service import SuperArmService
from lelab.superarm.showroom import (
    align_joint5_mjcf,
    align_joint5_urdf,
    remove_amazinghand_visuals,
)
from lelab.superarm.transports import MuJoCoRuntime, configure_superarm_camera


def test_calibrated_mujoco_interpolation_and_clamping() -> None:
    assert degrees_to_mujoco(1, 0) == pytest.approx(0.05)
    assert degrees_to_mujoco(1, 110) == pytest.approx(0.95)
    assert degrees_to_mujoco(2, 0) == pytest.approx(-0.02)
    assert degrees_to_mujoco(2, 110) == pytest.approx(-1.10)
    assert degrees_to_mujoco(1, -400) == pytest.approx(-math.pi / 2)
    assert degrees_to_mujoco(2, 400) == pytest.approx(-math.pi / 2)


@pytest.mark.parametrize(
    ("motor1", "motor2", "expected_motor1", "expected_motor2"),
    [
        (0.05, -0.02, 0.05, 0.02),
        (0.50, -0.56, 0.50, 0.56),
        (0.95, -1.10, 0.95, 1.10),
    ],
)
def test_mujoco_hand_positions_project_into_urdf_joint_limits(
    motor1: float,
    motor2: float,
    expected_motor1: float,
    expected_motor2: float,
) -> None:
    raw = {
        f"finger{finger}_motor{motor}": motor1 if motor == 1 else motor2
        for finger in range(1, 5)
        for motor in range(1, 3)
    }

    projected = mujoco_hand_to_urdf(raw)

    assert list(projected) == list(raw)
    for finger in range(1, 5):
        assert projected[f"finger{finger}_motor1"] == pytest.approx(expected_motor1)
        assert projected[f"finger{finger}_motor2"] == pytest.approx(expected_motor2)
        assert -0.05 <= projected[f"finger{finger}_motor1"] <= 1.05
        assert 0.0 <= projected[f"finger{finger}_motor2"] <= 1.2


def test_mujoco_hand_projection_clamps_runtime_overshoot_to_urdf_limits() -> None:
    projected = mujoco_hand_to_urdf(
        {
            "finger1_motor1": 1.3,
            "finger1_motor2": -1.4,
        }
    )

    assert projected == {
        "finger1_motor1": pytest.approx(1.05),
        "finger1_motor2": pytest.approx(1.2),
    }


def test_even_hardware_servo_is_inverted() -> None:
    assert degrees_to_hardware_radians(1, 30) == pytest.approx(math.radians(30))
    assert degrees_to_hardware_radians(2, 30) == pytest.approx(-math.radians(30))


def test_upstream_ring_middle_pointer_thumb_order_round_trip() -> None:
    raw = [50, 51, 30, 31, 10, 11, 70, 71]
    named = upstream_positions_to_named(raw)
    assert list(named) == ["ring", "middle", "pointer", "thumb"]
    assert named["pointer"] == [10.0, 11.0]
    assert named_to_upstream_positions(named) == raw


@pytest.mark.parametrize(
    "payload",
    [
        {"arm_rad": {"wrong": 0.0}},
        {"arm_rad": {"joint_rev_1": float("nan")}},
        {"hand_deg": {"pointer": [1]}},
        {"hand_deg": {"pinky": [0, 0]}},
        {"hand_deg": {"pointer": [0, 0]}, "hand_speed": {"pointer": [0, 7]}},
    ],
)
def test_action_validation_rejects_ambiguous_or_unsafe_payloads(payload) -> None:
    with pytest.raises(ValidationError):
        ActionRequest.model_validate(payload)


def test_hardware_readiness_is_read_only_and_keeps_protocols_separate() -> None:
    readiness = SuperArmService().hardware_readiness()

    assert readiness["website_controls_physical_arm"] is False
    assert readiness["arm"]["motor_type"] == "dm4340"
    assert readiness["hand"]["protocol"].endswith("SCS0009 serial")
    assert len(readiness["steps"]) == 5


def test_so101_leader_readiness_uses_the_six_control_superarm_contract() -> None:
    readiness = SuperArmService().so101_leader_readiness()

    assert readiness["supported"] is True
    assert readiness["manual_page_is_physical_leader"] is False
    assert readiness["follower"]["device_type"] == "SuperArm DM4340P + AmazingHand"
    assert readiness["follower"]["first_target"] == "MuJoCo SuperArm + AmazingHand"
    assert readiness["follower"]["arm_calibration"]["required_before_hardware"] is True
    assert readiness["follower"]["arm_calibration"]["joints"] == [
        f"joint_rev_{index}" for index in range(1, 6)
    ]
    assert [item["target"] for item in readiness["mapping"]] == [
        f"joint_rev_{index}.pos" for index in range(1, 6)
    ]
    assert readiness["gripper"]["target"] == "amazinghand_motion.pos"
    assert [path["id"] for path in readiness["control_paths"]] == [
        "manual_to_sim",
        "so101_to_sim",
        "so101_to_real",
    ]
    assert [path["website_status"] for path in readiness["control_paths"]] == [
        "available",
        "available",
        "preparation_only",
    ]
    assert readiness["control_paths"][0]["entry_route"] == "/manual-leader"
    assert readiness["control_paths"][1]["recording_input_mode"] == "so101"
    assert readiness["control_paths"][2]["recording_input_mode"] is None
    assert readiness["website_sequence_complete"] is False


def test_superarm_follower_calibration_preview_requires_five_measured_joints() -> None:
    from lelab.server import app

    payload = {
        "arm_port": "can0",
        "hand_port": "/dev/ttyACM0",
        "arm_motor_config": {f"joint_rev_{index}": [index, index + 0x10] for index in range(1, 6)},
        "arm_joint_calibration": {
            f"joint_rev_{index}": [1.0 if index % 2 else -1.0, index * 0.01] for index in range(1, 6)
        },
        "arm_joint_limits_deg": {f"joint_rev_{index}": [-20.0, 20.0] for index in range(1, 6)},
        "arm_position_kp": [10.0] * 5,
        "arm_position_kd": [1.0] * 5,
        "hand_speed": 3,
        "confirmed_measured": True,
    }

    response = TestClient(app).post("/api/superarm/hardware-config/preview", json=payload)

    assert response.status_code == 200
    result = response.json()
    assert result["configuration_valid"] is True
    assert result["connects_hardware"] is False
    assert result["motion_authorized"] is False
    rendered = yaml.safe_load(result["yaml"])
    assert rendered["_type"] == "superarm_dm4340p_amazinghand"
    assert set(rendered["arm_joint_calibration"]) == {f"joint_rev_{index}" for index in range(1, 6)}

    payload["confirmed_measured"] = False
    rejected = TestClient(app).post("/api/superarm/hardware-config/preview", json=payload)
    assert rejected.status_code == 422


def test_program_store_import_round_trip_and_atomic_write(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream.yaml"
    upstream.write_text(
        yaml.safe_dump(
            {
                "poses": {"test": {"positions": [5, 6, 3, 4, 1, 2, 7, 8]}},
                "sequences": {"demo": {"steps": ["test:3,3,3,3,3,3,3,3|0.2s", "SLEEP:0.1s"]}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    store = ProgramStore(tmp_path / "programs.yaml")
    imported = store.import_upstream(upstream)
    assert imported == {"poses": 1, "sequences": 1}
    assert store.list_poses()["test"]["hand_deg"]["pointer"] == [1.0, 2.0]
    assert store.export_upstream()["poses"]["test"]["positions"] == [5.0, 6.0, 3.0, 4.0, 1.0, 2.0, 7.0, 8.0]
    assert not list(tmp_path.glob("programs-*.yaml"))


def test_missing_pose_subsystem_is_preserved(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs.yaml")
    pose = store.save_pose("arm only", {"arm_rad": {"joint_rev_1": 0.2}})
    assert pose == {"arm_rad": {"joint_rev_1": 0.2}}
    assert "hand_deg" not in pose


def test_joint5_urdf_rotates_motor_and_keeps_shell_fixed_to_it() -> None:
    root = ET.fromstring(
        "<robot name='superarm'>"
        "<link name='arm_link2b'/><link name='motor_5'/><link name='arm_link3b'/>"
        "<joint name='joint_fix_43' type='fixed'><origin xyz='0.02 0 0.05'/>"
        "<parent link='arm_link2b'/><child link='motor_5'/></joint>"
        "<joint name='joint_rev_5' type='continuous'><origin xyz='0 0.025 0.00175'/>"
        "<parent link='motor_5'/><child link='arm_link3b'/><axis xyz='0 0 1'/></joint>"
        "</robot>"
    )

    assert align_joint5_urdf(root) is True

    joints = {joint.get("name"): joint for joint in root.findall("joint")}
    moving = joints["joint_rev_5"]
    assert moving.get("type") == "continuous"
    assert moving.find("parent").get("link") == "arm_link2b"
    assert moving.find("child").get("link") == "motor_5"
    assert moving.find("origin").get("xyz") == "0.02 0 0.05"
    assert moving.find("axis").get("xyz") == "0 0 -1"
    shell_mount = joints["joint_fix_28"]
    assert shell_mount.get("type") == "fixed"
    assert shell_mount.find("parent").get("link") == "motor_5"
    assert shell_mount.find("child").get("link") == "arm_link3b"
    assert shell_mount.find("origin").get("xyz") == "0 0.025 0.00175"
    assert shell_mount.find("axis") is None


def test_amazinghand_urdf_visuals_are_removed_for_exact_mujoco_overlay() -> None:
    root = ET.fromstring(
        "<robot name='superarm'>"
        "<link name='r_wrist_interface'><visual name='wrist'><geometry><mesh filename='wrist.stl'/></geometry></visual></link>"
        "<link name='palm'/>"
        "<link name='finger1_proximal'>"
        "<visual name='proximal'><origin xyz='0 0.01 0'/><geometry><mesh filename='proximal.stl'/></geometry></visual>"
        "<visual name='proximal-shell'><origin xyz='0 0.01 0'/><geometry><mesh filename='proximal_shell.stl'/></geometry></visual>"
        "<visual name='passive-rod'><origin xyz='0.01 0.02 0.03'/><geometry><mesh filename='m2_rod_l18.stl'/></geometry></visual>"
        "<collision name='proximal-contact'><origin xyz='0 0.029 0'/><geometry><box size='0.018 0.058 0.018'/></geometry></collision>"
        "</link>"
        "<link name='finger1_distal'>"
        "<visual name='distal'><geometry><mesh filename='distal.stl'/></geometry></visual>"
        "<visual name='distal-shell'><geometry><mesh filename='distal_shell.stl'/></geometry></visual>"
        "<visual name='passive-pin'><origin xyz='0 0.01 0.02'/><geometry><mesh filename='parallel_pin.stl'/></geometry></visual>"
        "<collision name='distal-contact'><origin xyz='0 0.025 0'/><geometry><box size='0.016 0.05 0.016'/></geometry></collision>"
        "<collision name='tip-contact'><origin xyz='0 0.055 0'/><geometry><box size='0.026 0.014 0.022'/></geometry></collision>"
        "</link>"
        "<joint name='wrist_to_palm' type='fixed'><parent link='r_wrist_interface'/><child link='palm'/>"
        "<origin xyz='0.1 0.2 0.3' rpy='0 0 0'/></joint>"
        "<joint name='finger1_motor1' type='revolute'><parent link='palm'/><child link='finger1_proximal'/>"
        "<origin xyz='0.01 0.02 0.03' rpy='0 0 0'/></joint>"
        "<joint name='finger1_motor2' type='revolute'><parent link='finger1_proximal'/><child link='finger1_distal'/>"
        "<origin xyz='0 0.058 0' rpy='0 0 0'/></joint>"
        "</robot>"
    )

    assert remove_amazinghand_visuals(root) == 7
    assert not root.findall(".//link[@name='r_wrist_interface']/visual")
    assert not root.findall(".//link[@name='finger1_proximal']/visual")
    assert not root.findall(".//link[@name='finger1_distal']/visual")
    assert root.find(".//link[@name='finger1_proximal']/collision") is not None
    assert root.find(".//joint[@name='finger1_motor1']") is not None
    assert remove_amazinghand_visuals(root) == 0


def test_joint5_mjcf_moves_pivot_to_motor_without_moving_zero_pose() -> None:
    root = ET.fromstring(
        "<mujoco><worldbody><body name='arm_link2b'>"
        "<geom mesh='motor_5' pos='0.055112 -0.282649 -0.371019'/>"
        "<body name='arm_link3b' pos='0.02 0.025 0.05175'>"
        "<inertial pos='0.0331973 -0.118164 0.001401'/>"
        "<joint name='joint_rev_5' axis='0 0 1'/>"
        "<geom mesh='arm_link3b' pos='0.035112 -0.307649 -0.422769'/>"
        "<body name='wrist' pos='0 -0.025 0.186753'/>"
        "</body></body></worldbody></mujoco>"
    )

    assert align_joint5_mjcf(root) is True

    moving = root.find(".//body[@name='arm_link3b']")
    assert moving.get("pos") == "0.02 0 0.05"
    assert moving.find("joint").get("axis") == "0 0 -1"
    geoms = {geom.get("mesh"): geom for geom in moving.findall("geom")}
    assert geoms["motor_5"].get("pos") == "0.035112 -0.282649 -0.421019"
    assert geoms["arm_link3b"].get("pos") == "0.035112 -0.282649 -0.421019"
    assert moving.find("inertial").get("pos") == "0.0331973 -0.093164 0.003151"
    assert moving.find("body[@name='wrist']").get("pos") == "0 0 0.188503"


def test_sequence_parsing_and_validation(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs.yaml")
    value = store.save_sequence(
        "test sequence",
        {"steps": [{"pose": "home", "transition_s": 0.1, "hold_s": 0.2, "hand_speed": 6}, {"sleep_s": 0.01}]},
    )
    assert len(value["steps"]) == 2
    with pytest.raises(ValueError):
        store.save_sequence("bad", {"steps": [{"pose": "home", "hand_speed": 7}]})


def test_session_reconnect_emergency_stop_and_clean_shutdown(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    service = SuperArmService(ProgramStore(tmp_path / "programs.yaml"))
    started = service.start_session("mujoco", workspace_root=workspace)
    try:
        assert started["connected"] is True
        runtime = service.runtime
        import mujoco

        joint_id = mujoco.mj_name2id(runtime._model, mujoco.mjtObj.mjOBJ_JOINT, "joint_rev_5")
        assert np.allclose(runtime._model.jnt_axis[joint_id], [0, 0, -1])
        motor_mesh = mujoco.mj_name2id(runtime._model, mujoco.mjtObj.mjOBJ_MESH, "motor_5")
        shell_mesh = mujoco.mj_name2id(runtime._model, mujoco.mjtObj.mjOBJ_MESH, "arm_link3b")
        motor_geom = next(
            geom_id
            for geom_id in range(runtime._model.ngeom)
            if runtime._model.geom_dataid[geom_id] == motor_mesh
        )
        shell_geom = next(
            geom_id
            for geom_id in range(runtime._model.ngeom)
            if runtime._model.geom_dataid[geom_id] == shell_mesh
        )
        assert runtime._model.geom_bodyid[motor_geom] == runtime._model.geom_bodyid[shell_geom]
        reconnected = service.start_session("mujoco", workspace_root=workspace)
        assert reconnected["connected"] is True
        assert reconnected["runtime"] == "mujoco"
        assert service.runtime is runtime
        with pytest.raises(RuntimeError, match="mujoco runtime session is already active"):
            service.start_session("hybrid_serial", workspace_root=workspace)
        assert service.action(arm_rad={"joint_rev_1": 0.2})["accepted"]
        assert service.emergency_stop(True)["emergency_stopped"] is True
        with pytest.raises(RuntimeError, match="emergency stop"):
            service.action(arm_rad={"joint_rev_1": 0.0})
        assert service.emergency_stop(False)["emergency_stopped"] is False
        open_hand = {finger: [0, 0] for finger in ("pointer", "middle", "ring", "thumb")}
        closed_hand = {finger: [110, 110] for finger in ("pointer", "middle", "ring", "thumb")}
        service.action(hand_deg=open_hand)
        time.sleep(0.5)
        first_sequence, open_frame = service.runtime.frame()
        assert open_frame and open_frame.startswith(b"\xff\xd8")
        service.action(hand_deg=closed_hand)
        time.sleep(1.05)
        sequence, frame = service.runtime.frame()
        assert sequence - first_sequence >= 14
        assert frame and frame.startswith(b"\xff\xd8")
        open_pixels = np.asarray(Image.open(io.BytesIO(open_frame)), dtype=np.float32)
        close_pixels = np.asarray(Image.open(io.BytesIO(frame)), dtype=np.float32)
        assert close_pixels.std() > 2.0
        # The full-assembly camera intentionally shows more arm and less hand than
        # the old wrist crop, so global pixel difference is smaller but non-zero.
        assert np.abs(close_pixels - open_pixels).mean() > 0.5
        assert service.telemetry()["state"]["hand"]["finger1_motor2"]["target"] == pytest.approx(-1.10)
        assert len(service.telemetry()["state"]["hand"]) == 8
        visual_pose = service.telemetry()["state"]["visual_pose"]
        assert visual_pose["root_link"] == "r_wrist_interface"
        assert visual_pose["sequence"] >= 20
        assert len(visual_pose["bodies"]) == 33
        assert visual_pose["bodies"]["r_wrist_interface"] == {
            "position_m": [0.0, 0.0, 0.0],
            "quaternion_wxyz": [1.0, 0.0, 0.0, 0.0],
        }
    finally:
        assert service.disconnect()["connected"] is False
        assert not any(
            thread.name == "superarm-mujoco" and thread.is_alive()
            for thread in __import__("threading").enumerate()
        )


def test_source_arm_urdf_is_browser_loadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "assets"
    workspace.mkdir()
    mesh_path = workspace / "motor_1.stl"
    mesh_path.write_bytes(b"solid motor\nendsolid motor\n")
    urdf_path = workspace / "superarm_amazinghand.urdf"
    urdf_path.write_text(
        f"<robot name='superarm'><link name='base'><visual><geometry><mesh filename='{mesh_path}'/></geometry></visual></link>"
        "<link name='wrist'/><link name='hand'/><joint name='wrist_adapter_to_amazinghand' type='fixed'>"
        "<parent link='wrist'/><child link='hand'/><origin xyz='0.005 -0.00014 0.600003' rpy='0 0 0'/>"
        "</joint></robot>",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERARM_URDF_PATH", str(urdf_path))
    service = SuperArmService(ProgramStore(tmp_path / "programs.yaml"))
    urdf = service.source_arm_urdf_xml(workspace)
    assert b"/api/superarm/urdf/meshes/" in urdf
    assert b"/home/" not in urdf
    assert b'xyz="0 0 0.011753"' in urdf
    mesh = service.source_arm_mesh_path("motor_1.stl", workspace)
    assert mesh.is_file()


def test_source_arm_urdf_preserves_hand_visuals_for_isaac_telemetry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "assets"
    workspace.mkdir()
    arm_mesh = workspace / "motor_1.stl"
    hand_mesh = workspace / "finger_shell.stl"
    arm_mesh.write_bytes(b"solid arm\nendsolid arm\n")
    hand_mesh.write_bytes(b"solid hand\nendsolid hand\n")
    urdf_path = workspace / "superarm_amazinghand.urdf"
    urdf_path.write_text(
        f"<robot name='superarm'><link name='base'><visual><geometry><mesh filename='{arm_mesh}'/></geometry></visual></link>"
        "<link name='wrist'/><link name='r_wrist_interface'/>"
        f"<link name='finger1'><visual name='hand-shell'><geometry><mesh filename='{hand_mesh}'/></geometry></visual></link>"
        "<joint name='wrist_adapter_to_amazinghand' type='fixed'><parent link='wrist'/><child link='r_wrist_interface'/></joint>"
        "<joint name='finger1_motor1' type='revolute'><parent link='r_wrist_interface'/><child link='finger1'/></joint>"
        "</robot>",
        encoding="utf-8",
    )
    monkeypatch.setenv("SUPERARM_URDF_PATH", str(urdf_path))
    service = SuperArmService(ProgramStore(tmp_path / "programs.yaml"))

    mujoco_xml = service.source_arm_urdf_xml(workspace)
    isaac_xml = service.source_arm_urdf_xml(workspace, include_hand_visuals=True)

    assert b"hand-shell" not in mujoco_xml
    assert b"hand-shell" in isaac_xml
    assert b"/api/superarm/urdf/meshes/finger_shell.stl" in isaac_xml


def test_mujoco_camera_frames_the_complete_assembly(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    service = SuperArmService(ProgramStore(tmp_path / "programs.yaml"))
    model_path = service.model_path(workspace)
    import mujoco

    model = mujoco.MjModel.from_xml_path(str(model_path))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    camera = mujoco.MjvCamera()
    configure_superarm_camera(model, data, camera)
    assert np.allclose(camera.lookat, model.stat.center)
    assert camera.distance >= model.stat.extent * 1.2


def test_live_command_throttle_and_timeout(tmp_path: Path) -> None:
    class Runtime:
        connected = True
        failure = None

        def command(self, *args, **kwargs):
            pass

        def stop(self):
            self.stopped = True

        def observe(self):
            return {}

    service = SuperArmService(ProgramStore(tmp_path / "programs.yaml"))
    service.runtime = Runtime()
    service.mode = "mujoco"
    assert service.action(arm_rad={"joint_rev_1": 0.1}, source="live")["accepted"]
    with pytest.raises(RuntimeError, match="20 Hz"):
        service.action(arm_rad={"joint_rev_1": 0.2}, source="live")
    service._last_live_command -= 10.1
    service.enforce_live_timeout()
    assert service.status()["live_enabled"] is False
    assert service.runtime.stopped is True


def test_sequence_worker_is_cancelable_and_nonblocking(tmp_path: Path) -> None:
    class Runtime:
        connected = True
        failure = None

        def command(self, *args, **kwargs):
            pass

        def stop(self):
            pass

        def observe(self):
            return {}

    store = ProgramStore(tmp_path / "programs.yaml")
    store.save_pose("home", {"arm_rad": {"joint_rev_1": 0.0}})
    store.save_sequence("long demo", {"steps": [{"pose": "home", "hold_s": 30.0}]})
    service = SuperArmService(store)
    service.runtime = Runtime()
    service.mode = "mujoco"
    started = time.monotonic()
    assert service.play_sequence("long demo") == {"playing": "long demo", "loop": False}
    assert time.monotonic() - started < 0.1
    assert service.pause_sequence()["paused"] is True
    assert service.pause_sequence()["paused"] is False
    assert service.stop_sequence()["stopped"] is True
    assert not service._sequence_thread


def test_api_namespace_is_registered() -> None:
    from lelab.server import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/api/superarm/capabilities" in paths
    assert "/api/superarm/hardware-readiness" in paths
    assert "/api/superarm/hardware-config/preview" in paths
    assert "/api/superarm/session" in paths
    assert "/api/superarm/telemetry" in paths
    assert "/api/superarm/action" in paths
    assert "/api/superarm/logical-action" in paths
    assert "/api/superarm/capture" in paths
    assert "/api/superarm/capture/latest" in paths
    assert "/api/superarm/video" in paths
    assert "/api/superarm/urdf" in paths
    assert "/api/superarm/mujoco-visual-manifest" in paths
    assert "/api/superarm/mujoco-visual-assets/{mesh_name}" in paths
    assert "/robots/{name}/mujoco-visual-manifest" in paths
    assert "/robots/{name}/mujoco-visual-assets/{mesh_name}" in paths
    assert "/ws/superarm" in paths
    response = TestClient(app).get("/api/superarm/capabilities")
    assert response.status_code == 200
    assert set(response.json()["runtimes"]) == {"mujoco", "hybrid_serial", "isaac_sim"}
    readiness = TestClient(app).get("/api/superarm/hardware-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["website_controls_physical_arm"] is False
    session = TestClient(app).get("/api/superarm/session")
    assert session.status_code == 200
    assert session.json()["connected"] is False


class _FakeIsaacRuntime:
    supports_video = False
    supports_capture = True
    metadata = {
        "runtime": "isaac_sim",
        "isaac_sim_version": "6.0.0",
        "articulation_root": "/superarm_amazinghand",
        "articulation_root_count": 1,
        "physical_dof_count": 13,
        "logical_action_width": 6,
    }

    def __init__(self, distribution_zip, **kwargs):
        self.distribution_zip = distribution_zip
        self.kwargs = kwargs
        self.connected = False
        self.failure = None
        self.partial_commands = []
        self.logical_commands = []
        self.stop_calls = 0
        self.close_calls = 0
        self.capture_calls = []

    def connect(self):
        self.connected = True

    def command_partial(self, **kwargs):
        self.partial_commands.append(kwargs)

    def command_logical(self, values):
        self.logical_commands.append(list(values))

    def observe(self):
        return {
            "runtime": "isaac_sim",
            "arm": {f"joint_rev_{index}": {"position": 0.0} for index in range(1, 6)},
            "hand": {
                f"finger{finger}_motor{motor}": {"position": 0.0}
                for finger in range(1, 5)
                for motor in range(1, 3)
            },
        }

    def stop(self):
        self.stop_calls += 1

    def close(self):
        self.connected = False
        self.close_calls += 1

    def frame(self):
        return 0, None

    def capture(self, view, name):
        self.capture_calls.append((view, name))
        return {"path": f"/tmp/{view}-{name}.png", "bytes": 10}


class _BlockingCommandRuntime(_FakeIsaacRuntime):
    def __init__(self, distribution_zip):
        super().__init__(distribution_zip)
        self.command_started = threading.Event()
        self.release_command = threading.Event()
        self.events = []

    def command_partial(self, **kwargs):
        self.events.append("command-start")
        self.command_started.set()
        self.release_command.wait(2.0)
        super().command_partial(**kwargs)
        self.events.append("command-end")

    def stop(self):
        super().stop()
        self.events.append("stop")


class _BlockingHoldRuntime(_FakeIsaacRuntime):
    def __init__(self, distribution_zip):
        super().__init__(distribution_zip)
        self.hold_started = threading.Event()
        self.release_hold = threading.Event()

    def stop(self):
        self.stop_calls += 1
        self.hold_started.set()
        self.release_hold.wait(2.0)


def test_isaac_service_uses_common_atomic_dispatch_and_capture(tmp_path: Path) -> None:
    created = []

    def factory(distribution_zip, **kwargs):
        runtime = _FakeIsaacRuntime(distribution_zip, **kwargs)
        created.append(runtime)
        return runtime

    service = SuperArmService(
        ProgramStore(tmp_path / "programs.yaml"),
        isaac_runtime_factory=factory,
    )
    started = service.start_session(
        "isaac_sim",
        isaac_distribution_zip="/server/superarm.zip",
        isaac_bridge_mode="managed",
    )
    runtime = created[0]
    capture_file = tmp_path / "hand-close.png"
    capture_file.write_bytes(b"\x89PNG\r\n\x1a\nvalidated-capture")
    runtime.capture = lambda _view, _name: {
        "path": str(capture_file),
        "bytes": capture_file.stat().st_size,
    }

    assert started["runtime"] == "isaac_sim"
    assert started["connected"] is True
    assert service.action(arm_rad={"joint_rev_1": 0.2})["accepted"] is True
    assert runtime.partial_commands[-1]["arm_rad"] == {"joint_rev_1": 0.2}
    logical = service.logical_action([0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
    assert logical["logical_action"][-1] == 1.0
    assert runtime.logical_commands[-1][-1] == 1.0
    assert len(service.telemetry()["state"]["arm"]) == 5
    assert len(service.telemetry()["state"]["hand"]) == 8
    capture = service.capture("hand", "close")
    assert capture["path"].endswith("hand-close.png")
    assert service.latest_capture() == capture
    assert service.disconnect()["connected"] is False


def test_stale_runtime_callback_is_ignored_after_reconnect(tmp_path: Path) -> None:
    created = []

    def factory(distribution_zip, **kwargs):
        runtime = _FakeIsaacRuntime(distribution_zip, **kwargs)
        created.append(runtime)
        return runtime

    service = SuperArmService(
        ProgramStore(tmp_path / "programs.yaml"),
        isaac_runtime_factory=factory,
    )
    service.start_session("isaac_sim", isaac_distribution_zip="/server/superarm.zip")
    stale_callback = created[0].kwargs["state_callback"]
    service.disconnect()
    service.start_session("isaac_sim", isaac_distribution_zip="/server/superarm.zip")
    subscriber = service.subscribe()

    stale_callback({"runtime": "isaac_sim", "stale": True})

    assert subscriber.empty()
    service.unsubscribe(subscriber)
    service.disconnect()


def test_isaac_service_rejects_capture_when_runtime_does_not_advertise_it(tmp_path: Path) -> None:
    runtime = _FakeIsaacRuntime("/server/superarm.zip")
    runtime.supports_capture = False
    service = SuperArmService(
        ProgramStore(tmp_path / "programs.yaml"),
        isaac_runtime_factory=lambda *_args, **_kwargs: runtime,
    )
    service.start_session("isaac_sim", isaac_distribution_zip="/server/superarm.zip")

    with pytest.raises(RuntimeError, match="does not support live capture"):
        service.capture("hand", "close")

    service.disconnect()


def test_live_watchdog_holds_once_without_telemetry_or_websocket_calls(tmp_path: Path) -> None:
    runtime = _FakeIsaacRuntime("/server/superarm.zip")
    service = SuperArmService(
        ProgramStore(tmp_path / "programs.yaml"),
        isaac_runtime_factory=lambda *_args, **_kwargs: runtime,
        live_timeout_s=0.05,
        watchdog_interval_s=0.01,
    )
    service.start_session("isaac_sim", isaac_distribution_zip="/server/superarm.zip")
    service.action(arm_rad={"joint_rev_1": 0.1}, source="live")

    time.sleep(0.12)

    assert runtime.stop_calls == 1
    assert service.status()["live_enabled"] is False
    service.disconnect()


def test_emergency_stop_is_serialized_after_an_inflight_command(tmp_path: Path) -> None:
    runtime = _BlockingCommandRuntime("/server/superarm.zip")
    service = SuperArmService(
        ProgramStore(tmp_path / "programs.yaml"),
        isaac_runtime_factory=lambda *_args, **_kwargs: runtime,
    )
    service.start_session("isaac_sim", isaac_distribution_zip="/server/superarm.zip")
    action_thread = threading.Thread(
        target=lambda: service.action(arm_rad={"joint_rev_1": 0.1})
    )
    stop_thread = threading.Thread(target=service.emergency_stop)
    action_thread.start()
    assert runtime.command_started.wait(1.0)
    stop_thread.start()
    time.sleep(0.02)

    runtime.release_command.set()
    action_thread.join(1.0)
    stop_thread.join(1.0)

    assert not action_thread.is_alive()
    assert not stop_thread.is_alive()
    assert runtime.events == ["command-start", "command-end", "stop"]
    service.disconnect()


def test_blocked_watchdog_prevents_disconnect_and_reconnect_until_worker_exits(tmp_path: Path) -> None:
    runtimes = []

    def factory(*_args, **_kwargs):
        runtime = _BlockingHoldRuntime("/server/superarm.zip")
        runtimes.append(runtime)
        return runtime

    service = SuperArmService(
        ProgramStore(tmp_path / "programs.yaml"),
        isaac_runtime_factory=factory,
        live_timeout_s=0.01,
        watchdog_interval_s=0.005,
        watchdog_join_timeout_s=0.05,
    )
    service.start_session("isaac_sim", isaac_distribution_zip="/server/superarm.zip")
    service.action(arm_rad={"joint_rev_1": 0.1}, source="live")
    assert runtimes[0].hold_started.wait(1.0)

    with pytest.raises(RuntimeError, match="watchdog did not stop"):
        service.disconnect()
    assert service.status()["connected"] is True
    with pytest.raises(RuntimeError, match="already active"):
        service.start_session("mujoco", model_path="/unused")
    assert len(runtimes) == 1

    runtimes[0].release_hold.set()
    deadline = time.monotonic() + 1.0
    while service._watchdog_thread and service._watchdog_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert service.disconnect()["connected"] is False
    assert service.start_session(
        "isaac_sim", isaac_distribution_zip="/server/superarm.zip"
    )["connected"] is True
    assert len(runtimes) == 2
    runtimes[1].release_hold.set()
    service.disconnect()


def test_capabilities_validate_configured_isaac_distribution(tmp_path, monkeypatch):
    import lelab.superarm.service as service_module

    archive = tmp_path / "distribution.zip"
    archive.write_bytes(b"zip")
    distribution = SimpleNamespace(
        archive_sha256="a" * 64,
        entrypoint=tmp_path / "asset" / "robot.usda",
        robot_contract={"physical_dof_count": 13, "logical_action_width": 6},
    )
    monkeypatch.setenv("SUPERARM_ISAAC_DISTRIBUTION_ZIP", str(archive))
    monkeypatch.setattr(service_module.shutil, "which", lambda _name: "/usr/bin/docker")
    service = SuperArmService(
        ProgramStore(tmp_path / "programs.yaml"),
        isaac_distribution_loader=lambda *_args, **_kwargs: distribution,
    )

    isaac = service.capabilities()["runtimes"]["isaac_sim"]
    assert isaac["enabled"] is True
    assert isaac["archive_sha256"] == "a" * 64
    assert isaac["entrypoint"].endswith("robot.usda")
    assert isaac["validation_error"] is None

    invalid = SuperArmService(
        ProgramStore(tmp_path / "invalid-programs.yaml"),
        isaac_distribution_loader=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("bad manifest")
        ),
    ).capabilities()["runtimes"]["isaac_sim"]
    assert invalid["enabled"] is False
    assert "bad manifest" in invalid["validation_error"]


def test_mujoco_runtime_logical_command_updates_arm_and_hand_atomically() -> None:
    runtime = object.__new__(MuJoCoRuntime)
    runtime._connected = True
    runtime._failure = None
    runtime._lock = __import__("threading").RLock()
    runtime._targets = {}

    runtime.command_logical([0.1, 0.2, 0.3, 0.4, 0.5, 1.0])

    assert runtime._targets["joint_rev_5"] == pytest.approx(0.5)
    assert runtime._targets["finger1_motor1"] == pytest.approx(0.95)
    assert runtime._targets["finger1_motor2"] == pytest.approx(-1.10)


def test_isaac_api_session_logical_capture_and_video_boundary(tmp_path, monkeypatch):
    import lelab.superarm.api as api_module
    from lelab.server import app

    runtime = _FakeIsaacRuntime("/server/superarm.zip")
    capture_file = tmp_path / "hand-close.png"
    capture_file.write_bytes(b"\x89PNG\r\n\x1a\nvalidated-capture")
    runtime.capture = lambda _view, _name: {
        "path": str(capture_file),
        "bytes": capture_file.stat().st_size,
    }

    def runtime_factory(*_args, **kwargs):
        runtime.kwargs.update(kwargs)
        return runtime

    local_service = SuperArmService(
        ProgramStore(tmp_path / "programs.yaml"),
        isaac_runtime_factory=runtime_factory,
    )
    monkeypatch.setattr(api_module, "service", local_service)
    client = TestClient(app)

    response = client.post(
        "/api/superarm/session",
        json={
            "runtime": "isaac_sim",
            "isaac_distribution_zip": "/server/superarm.zip",
            "isaac_bridge_mode": "managed",
            "isaac_external_run_dir": "/server/shared-run",
        },
    )
    assert response.status_code == 200
    assert response.json()["runtime"] == "isaac_sim"
    assert response.json()["runtime_metadata"]["isaac_sim_version"] == "6.0.0"
    assert runtime.kwargs["external_run_dir"] == "/server/shared-run"
    telemetry = client.get("/api/superarm/telemetry")
    assert telemetry.status_code == 200
    assert len(telemetry.json()["state"]["arm"]) == 5
    assert len(telemetry.json()["state"]["hand"]) == 8
    logical = client.put(
        "/api/superarm/logical-action",
        json={"values": [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]},
    )
    assert logical.status_code == 200
    capture = client.post(
        "/api/superarm/capture",
        json={"view": "hand", "name": "close"},
    )
    assert capture.status_code == 200
    assert client.get("/api/superarm/capture/latest").json() == capture.json()
    video = client.get("/api/superarm/video")
    assert video.status_code == 409
    assert "Continuous video is only available for MuJoCo" in video.json()["detail"]
    local_service.disconnect()


def test_isaac_api_serves_only_the_validated_latest_capture_file(tmp_path, monkeypatch):
    import lelab.superarm.api as api_module
    from lelab.server import app

    capture_file = tmp_path / "hand-close.png"
    capture_file.write_bytes(b"\x89PNG\r\n\x1a\nvalidated-capture")
    runtime = _FakeIsaacRuntime("/server/superarm.zip")
    runtime.capture = lambda _view, _name: {
        "path": str(capture_file),
        "bytes": capture_file.stat().st_size,
    }
    local_service = SuperArmService(
        ProgramStore(tmp_path / "programs.yaml"),
        isaac_runtime_factory=lambda *_args, **_kwargs: runtime,
    )
    monkeypatch.setattr(api_module, "service", local_service)
    client = TestClient(app)
    client.post(
        "/api/superarm/session",
        json={"runtime": "isaac_sim", "isaac_distribution_zip": "/server/superarm.zip"},
    )
    client.post("/api/superarm/capture", json={"view": "hand", "name": "close"})

    image = client.get("/api/superarm/capture/latest/image")

    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.headers["cache-control"] == "no-store"
    assert image.content == capture_file.read_bytes()

    replacement = b"\x89PNG\r\n\x1a\nchanged---capture"
    assert len(replacement) == capture_file.stat().st_size
    capture_file.write_bytes(replacement)
    changed = client.get("/api/superarm/capture/latest/image")

    assert changed.status_code == 409
    assert "changed" in changed.json()["detail"].lower()
    local_service.disconnect()
