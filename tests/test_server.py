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
"""Tests for lelab.server — FastAPI app and ConnectionManager."""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# A browser sends an Accept header that prefers HTML on navigations/hard-reloads.
BROWSER_ACCEPT = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

REQUIRED_PATHS = {
    "/health",
    "/get-configs",
    "/move-arm",
    "/manual-leader-config/{name}",
    "/stop-teleoperation",
    "/teleoperation-status",
    "/joint-positions",
    "/start-recording",
    "/stop-recording",
    "/recording-status",
    "/start-calibration",
    "/stop-calibration",
    "/calibration-status",
    "/datasets",
    "/jobs",
    "/available-ports",
    "/available-cameras",
    "/hf-auth-status",
    "/ws/joint-data",
}


def test_app_exposes_required_endpoints() -> None:
    from lelab.server import app

    paths = {route.path for route in app.routes}
    missing = REQUIRED_PATHS - paths
    assert not missing, f"missing routes: {missing}"


def test_health_endpoint_returns_200(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_endpoint_returns_dict(client: TestClient) -> None:
    response = client.get("/health")
    body = response.json()
    assert isinstance(body, dict)


def test_rl_readiness_uses_server_distribution_when_client_path_is_omitted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lelab import server

    captured = {}

    def fake_check(distribution_zip, learner_port, bridge_port):
        captured.update(
            distribution_zip=distribution_zip,
            learner_port=learner_port,
            bridge_port=bridge_port,
        )
        return {"ready": True, "checks": {}, "distribution_zip": distribution_zip}

    monkeypatch.setenv("SUPERARM_ISAAC_DISTRIBUTION_ZIP", "/server/confirmed-v3.zip")
    monkeypatch.setattr(server, "check_rl_readiness", fake_check)
    response = client.get("/system/rl-readiness")
    assert response.status_code == 200
    assert captured == {
        "distribution_zip": "/server/confirmed-v3.zip",
        "learner_port": 50051,
        "bridge_port": 8765,
    }
    assert response.json()["distribution_zip"] == "/server/confirmed-v3.zip"


def test_manual_leader_config_exposes_superarm_slider_contract(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lelab import server

    config_path = Path(__file__).resolve().parents[1] / "lelab/superarm/data/superarm_mujoco.yaml"
    record = {
        "name": "SuperArm + AmazingHand",
        "robot_backend": "superarm_mujoco",
        "superarm_config": str(config_path),
        "follower_config": str(config_path),
        "superarm_asset_root": str(config_path.parents[3]),
        "mujoco_model_path": "/tmp/superarm.xml",
    }
    monkeypatch.setattr(server, "get_robot_record", lambda name: record if name == record["name"] else None)
    response = client.get("/manual-leader-config/SuperArm%20%2B%20AmazingHand")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "success"
    assert body["robot_name"] == "SuperArm + AmazingHand"
    assert body["robot_backend"] == "superarm_mujoco"
    assert body["joint_names"] == [
        "joint_rev_1",
        "joint_rev_2",
        "joint_rev_3",
        "joint_rev_4",
        "joint_rev_5",
        "amazinghand_motion",
    ]
    assert body["action_endpoint"] == "/send-joint-action"
    assert body["start_endpoint"] == "/move-arm"
    assert body["stop_endpoint"] == "/stop-teleoperation"
    assert body["start_request"]["robot_backend"] == "superarm_mujoco"
    assert body["start_request"]["superarm_config"].endswith("superarm_mujoco.yaml")
    assert all(slider["min"] < 0 < slider["max"] for slider in body["sliders"])
    assert body["presets"][0]["name"] == "Home / open"
    assert body["presets"][0]["action"] == [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def test_manual_leader_config_rejects_so101_records(client: TestClient, tmp_lerobot_home) -> None:
    name = f"Manual Leader SO101 Test {uuid.uuid4().hex[:8]}"
    response = client.post(f"/robots/{name}?create=true", json={})
    assert response.status_code == 200

    response = client.get(f"/manual-leader-config/{name}")
    assert response.status_code == 400
    assert "manual web leader" in response.json()["message"].lower()


def test_manual_leader_config_accepts_isaac_record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lelab import server

    config_path = Path(__file__).resolve().parents[1] / "lelab/superarm/data/superarm_isaac.yaml"
    record = {
        "name": "SuperArm + AmazingHand (Isaac Sim)",
        "robot_backend": "superarm_isaac",
        "superarm_config": str(config_path),
        "follower_config": str(config_path),
        "superarm_asset_root": str(config_path.parents[3]),
        "isaac_distribution_zip": "/server/superarm.zip",
        "isaac_bridge_mode": "managed",
        "isaac_host": "127.0.0.1",
        "isaac_port": 8765,
    }
    monkeypatch.setattr(server, "get_robot_record", lambda name: record if name == record["name"] else None)

    response = client.get("/manual-leader-config/SuperArm%20%2B%20AmazingHand%20%28Isaac%20Sim%29")

    assert response.status_code == 200
    body = response.json()
    assert body["robot_backend"] == "superarm_isaac"
    assert body["start_request"]["isaac_distribution_zip"] == "/server/superarm.zip"
    close = next(motion for motion in body["hand_motions"] if motion["name"] == "close")
    assert close["joint_targets"]["finger1_motor2"] == pytest.approx(1.10)


def test_mujoco_visual_routes_reject_isaac_records(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lelab import server

    record = {"name": "Isaac only", "robot_backend": "superarm_isaac"}
    monkeypatch.setattr(server, "get_robot_record", lambda name: record if name == record["name"] else None)

    manifest = client.get("/robots/Isaac%20only/mujoco-visual-manifest")
    asset = client.get("/robots/Isaac%20only/mujoco-visual-assets/hand.stl")

    assert manifest.status_code == 404
    assert asset.status_code == 404


def test_robot_showroom_serves_only_record_urdf_and_referenced_meshes(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lelab import server

    workspace = tmp_path / "superarm_ws"
    mesh_path = workspace / "meshes/arm.stl"
    urdf_path = workspace / "models/superarm.urdf"
    mesh_path.parent.mkdir(parents=True)
    urdf_path.parent.mkdir(parents=True)
    mesh_path.write_bytes(b"solid arm\nendsolid arm\n")
    urdf_path.write_text(
        f"<robot name='superarm'><link name='base'><visual><geometry><mesh filename='{mesh_path}'/></geometry></visual></link>"
        "<link name='arm_link2b'/><link name='motor_5'/><link name='arm_link3b'/>"
        "<joint name='joint_fix_43' type='fixed'><parent link='arm_link2b'/><child link='motor_5'/>"
        "<origin xyz='0.02 0 0.05' rpy='0 0 0'/></joint>"
        "<joint name='joint_rev_5' type='continuous'><parent link='motor_5'/><child link='arm_link3b'/>"
        "<origin xyz='0 0.025 0.00175' rpy='0 0 0'/><axis xyz='0 0 1'/></joint>"
        "<link name='wrist'/><link name='hand'/><joint name='wrist_adapter_to_amazinghand' type='fixed'>"
        "<parent link='wrist'/><child link='hand'/><origin xyz='0.005 -0.00014 0.600003' rpy='0 0 0'/>"
        "</joint></robot>",
        encoding="utf-8",
    )
    record = {
        "name": "SuperArm + AmazingHand",
        "robot_backend": "superarm_mujoco",
        "superarm_asset_root": str(workspace),
        "urdf_path": str(urdf_path),
    }
    monkeypatch.setattr(server, "get_robot_record", lambda name: record if name == record["name"] else None)

    response = client.get("/robots/SuperArm%20%2B%20AmazingHand/urdf")

    assert response.status_code == 200
    assert response.headers["x-lelab-urdf-mesh-count"] == "1"
    asset_url = "/robots/SuperArm%20%2B%20AmazingHand/assets/0/arm.stl"
    assert "assets/0/arm.stl" in response.text
    assert 'xyz="0 0 0.011753"' in response.text
    served_root = ET.fromstring(response.content)
    served_joints = {joint.get("name"): joint for joint in served_root.findall("joint")}
    assert served_joints["joint_rev_5"].find("child").get("link") == "motor_5"
    assert served_joints["joint_rev_5"].find("axis").get("xyz") == "0 0 -1"
    assert served_joints["joint_fix_28"].find("child").get("link") == "arm_link3b"
    assert str(mesh_path) not in response.text
    asset = client.get(asset_url)
    assert asset.status_code == 200
    assert asset.content == mesh_path.read_bytes()
    assert client.get("/robots/SuperArm%20%2B%20AmazingHand/assets/0/wrong.stl").status_code == 404
    assert client.get("/robots/SuperArm%20%2B%20AmazingHand/assets/1").status_code == 404


def test_robot_showroom_rejects_urdf_outside_workspace(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lelab import server

    workspace = tmp_path / "superarm_ws"
    outside = tmp_path / "outside.urdf"
    workspace.mkdir()
    outside.write_text("<robot name='outside' />", encoding="utf-8")
    monkeypatch.setattr(
        server,
        "get_robot_record",
        lambda name: {
            "name": name,
            "superarm_asset_root": str(workspace),
            "urdf_path": str(outside),
        },
    )

    assert client.get("/robots/unsafe/urdf").status_code == 404


def test_robot_showroom_serves_record_scoped_mujoco_visuals(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lelab import server

    asset = tmp_path / "proximal_shell.stl"
    asset.write_bytes(b"solid proximal\nendsolid proximal\n")
    record = {
        "name": "Exact Hand",
        "robot_backend": "superarm_mujoco",
        "superarm_asset_root": str(tmp_path),
        "mujoco_model_path": str(tmp_path / "combined.xml"),
    }
    monkeypatch.setattr(server, "get_robot_record", lambda name: record if name == "Exact Hand" else None)
    monkeypatch.setattr(
        server.superarm_service,
        "amazinghand_visual_manifest",
        lambda workspace_root, model_path, asset_url_prefix: {
            "root_link": "r_wrist_interface",
            "bodies": [],
            "default_pose": {"bodies": {}},
            "asset_url_prefix": asset_url_prefix,
        },
    )
    monkeypatch.setattr(
        server.superarm_service,
        "amazinghand_visual_asset_path",
        lambda mesh_name, workspace_root, model_path: (
            asset
            if mesh_name in {"proximal_shell", "proximal_shell.stl"}
            else (_ for _ in ()).throw(FileNotFoundError(mesh_name))
        ),
    )

    manifest = client.get("/robots/Exact%20Hand/mujoco-visual-manifest")
    assert manifest.status_code == 200
    assert manifest.json()["asset_url_prefix"] == "/robots/Exact%20Hand/mujoco-visual-assets"
    served_asset = client.get("/robots/Exact%20Hand/mujoco-visual-assets/proximal_shell.stl")
    assert served_asset.status_code == 200
    assert served_asset.content == asset.read_bytes()
    assert client.get("/robots/Exact%20Hand/mujoco-visual-assets/not-allowlisted").status_code == 404


def test_recording_action_rejects_when_manual_superarm_recording_is_idle(client: TestClient) -> None:
    response = client.post("/recording-action", json={"action": [0.0] * 6})

    assert response.status_code == 409
    assert "no manual superarm recording" in response.json()["message"].lower()


def test_recording_action_quantizes_manual_superarm_motion(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lelab import record

    request = record.RecordingRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="manual",
        follower_config="unused",
        dataset_repo_id="local/test",
        single_task="test",
        robot_backend="superarm_mujoco",
        input_mode="manual",
    )
    monkeypatch.setattr(record, "recording_active", True)
    monkeypatch.setattr(record, "recording_config", request)

    response = client.post("/recording-action", json={"action": [0.1, -0.2, 0.3, -0.4, 0.5, 0.77]})

    assert response.status_code == 200
    assert response.json()["resolved_logical_action"]["amazinghand_motion.pos"] == 1.0


def test_recording_action_accepts_manual_isaac_backend(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lelab import record

    request = record.RecordingRequest(
        leader_port="unused",
        follower_port="unused",
        leader_config="manual",
        follower_config="unused",
        dataset_repo_id="local/test",
        single_task="test",
        robot_backend="superarm_isaac",
        input_mode="manual",
        isaac_distribution_zip="/server/superarm.zip",
    )
    monkeypatch.setattr(record, "recording_active", True)
    monkeypatch.setattr(record, "recording_config", request)

    response = client.post(
        "/recording-action", json={"action": [0.1, -0.2, 0.3, -0.4, 0.5, 0.77]}
    )

    assert response.status_code == 200
    assert response.json()["resolved_logical_action"]["amazinghand_motion.pos"] == 1.0


def test_unknown_route_returns_404(client: TestClient) -> None:
    response = client.get("/this-does-not-exist")
    assert response.status_code == 404


@pytest.mark.parametrize("unsafe_name", ["evil..name", "..config", "back\\door"])
def test_delete_calibration_config_rejects_unsafe_name(client: TestClient, unsafe_name: str) -> None:
    """A config name with path-traversal characters is rejected before any
    filesystem access — distinct from the "not found" path, so the guard is
    proven to fire. The validator also blocks "/" and "\\"."""
    response = client.delete(f"/calibration-configs/teleop/{unsafe_name}")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "Invalid configuration name" in body["message"]


def _spa_mounted(client: TestClient) -> bool:
    return any(getattr(route, "name", None) == "frontend" for route in client.app.routes)


def test_spa_deep_link_serves_index_html(client: TestClient) -> None:
    """A browser hard-reload of a client-side route returns the SPA shell, not a 404."""
    if not _spa_mounted(client):
        pytest.skip("frontend/dist not built; SPA not mounted")
    response = client.get("/recording", headers=BROWSER_ACCEPT)
    assert response.status_code == 200
    assert response.text.lstrip().lower().startswith("<!doctype html")


def test_superarm_teleoperation_deep_link_survives_browser_reentry(client: TestClient) -> None:
    """The primary SuperArm route reloads directly with its robot query intact."""
    if not _spa_mounted(client):
        pytest.skip("frontend/dist not built; SPA not mounted")
    response = client.get(
        "/teleoperation?robot=SuperArm%20%2B%20AmazingHand",
        headers=BROWSER_ACCEPT,
    )
    assert response.status_code == 200
    assert response.text.lstrip().lower().startswith("<!doctype html")


def test_spa_fallback_does_not_mask_api_404(client: TestClient) -> None:
    """Non-HTML clients (XHR, curl, API typos) still get a real 404, not the SPA shell."""
    response = client.get("/recording", headers={"accept": "application/json"})
    assert response.status_code == 404


def test_spa_fallback_respects_explicit_html_refusal(client: TestClient) -> None:
    """`text/html;q=0` is an explicit refusal — it must not get the SPA shell."""
    response = client.get("/recording", headers={"accept": "application/json,text/html;q=0"})
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("accept", "expected"),
    [
        ("text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", True),
        ("text/html", True),
        ("text/html;q=0.5", True),
        ("application/json", False),
        ("*/*", False),
        ("", False),
        ("text/html;q=0", False),
        ("application/json,text/html;q=0", False),
        ("text/html;q=bogus", False),
    ],
)
def test_accepts_html(accept: str, expected: bool) -> None:
    from lelab.server import _accepts_html

    assert _accepts_html(accept) is expected


def test_connection_manager_tracks_connect_and_disconnect() -> None:
    from lelab.server import ConnectionManager

    mgr = ConnectionManager()
    fake_ws = MagicMock()
    fake_ws.accept = AsyncMock()

    import asyncio

    asyncio.run(mgr.connect(fake_ws))
    assert fake_ws in mgr.active_connections

    mgr.disconnect(fake_ws)
    assert fake_ws not in mgr.active_connections


def test_connection_manager_broadcast_sync_does_not_block_without_loop() -> None:
    from lelab.server import ConnectionManager

    mgr = ConnectionManager()
    # Should enqueue without raising even if there are no consumers.
    mgr.broadcast_joint_data_sync({"shoulder_pan.pos": 1.0})


def _install_fake_pygrabber(monkeypatch: pytest.MonkeyPatch, filter_graph_cls) -> None:
    import sys
    import types

    module = types.ModuleType("pygrabber.dshow_graph")
    module.FilterGraph = filter_graph_cls
    monkeypatch.setitem(sys.modules, "pygrabber", types.ModuleType("pygrabber"))
    monkeypatch.setitem(sys.modules, "pygrabber.dshow_graph", module)


def test_windows_cameras_uses_real_directshow_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows path returns pygrabber's real device names in index order so
    the frontend can match each camera to its browser deviceId (issues #12/#16).
    """
    from lelab import server

    class _FakeGraph:
        def get_input_devices(self) -> list[str]:
            return ["USB2.0_CAM1", "ASUS FHD webcam"]

    _install_fake_pygrabber(monkeypatch, _FakeGraph)

    assert server._windows_cameras() == [
        {"index": 0, "name": "USB2.0_CAM1", "available": True},
        {"index": 1, "name": "ASUS FHD webcam", "available": True},
    ]


def test_windows_cameras_falls_back_when_pygrabber_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If pygrabber is missing or its COM init fails, enumeration degrades to the
    generic cv2 probe instead of erroring."""
    from lelab import server

    class _BoomGraph:
        def __init__(self) -> None:
            raise RuntimeError("DirectShow/COM unavailable")

    _install_fake_pygrabber(monkeypatch, _BoomGraph)
    sentinel = [{"index": 0, "name": "Camera 0", "available": True}]
    monkeypatch.setattr(server, "_generic_cv2_cameras", lambda backend: sentinel)

    assert server._windows_cameras() == sentinel


def test_v4l2_camera_name_reads_sysfs(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from lelab import server

    monkeypatch.setattr("builtins.open", lambda *a, **k: io.StringIO("HD Pro Webcam C920\n"))
    assert server._v4l2_camera_name(0) == "HD Pro Webcam C920"


def test_v4l2_camera_name_returns_none_when_missing() -> None:
    from lelab import server

    # No such sysfs node (also the case on non-Linux): graceful None, not error.
    assert server._v4l2_camera_name(999999) is None


def test_import_model_route_returns_record(client, monkeypatch) -> None:
    from lelab import server

    fake = {
        "id": "act_imported_x",
        "name": "Imported · model",
        "state": "done",
        "config": {"dataset_repo_id": "(imported)", "policy_type": "act"},
        "output_dir": "/tmp/model",
        "started_at": 1.0,
        "ended_at": 1.0,
        "runner": "imported",
        "hf_repo_id": None,
    }
    from lelab.jobs import JobRecord

    monkeypatch.setattr(
        server.job_registry,
        "register_imported",
        lambda source, name=None: JobRecord(**fake),
    )
    resp = client.post("/jobs/import", json={"source": "/tmp/model"})
    assert resp.status_code == 201
    assert resp.json()["runner"] == "imported"


def test_import_model_route_maps_value_error_to_400(client, monkeypatch) -> None:
    from lelab import server

    def boom(source, name=None):
        raise ValueError("No usable model at '/tmp/x'")

    monkeypatch.setattr(server.job_registry, "register_imported", boom)
    resp = client.post("/jobs/import", json={"source": "/tmp/x"})
    assert resp.status_code == 400
    assert "No usable model" in resp.json()["detail"]
