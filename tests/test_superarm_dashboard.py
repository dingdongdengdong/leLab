from __future__ import annotations

import io
import math
import time
from pathlib import Path

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
    named_to_upstream_positions,
    upstream_positions_to_named,
)
from lelab.superarm.programs import ProgramStore
from lelab.superarm.service import SuperArmService


def test_calibrated_mujoco_interpolation_and_clamping() -> None:
    assert degrees_to_mujoco(1, 0) == pytest.approx(0.05)
    assert degrees_to_mujoco(1, 110) == pytest.approx(0.95)
    assert degrees_to_mujoco(2, 0) == pytest.approx(-0.02)
    assert degrees_to_mujoco(2, 110) == pytest.approx(-1.10)
    assert degrees_to_mujoco(1, -400) == pytest.approx(-math.pi / 2)
    assert degrees_to_mujoco(2, 400) == pytest.approx(-math.pi / 2)


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


def test_sequence_parsing_and_validation(tmp_path: Path) -> None:
    store = ProgramStore(tmp_path / "programs.yaml")
    value = store.save_sequence(
        "test sequence",
        {"steps": [{"pose": "home", "transition_s": 0.1, "hold_s": 0.2, "hand_speed": 6}, {"sleep_s": 0.01}]},
    )
    assert len(value["steps"]) == 2
    with pytest.raises(ValueError):
        store.save_sequence("bad", {"steps": [{"pose": "home", "hand_speed": 7}]})


def test_session_exclusivity_emergency_stop_and_clean_shutdown(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    service = SuperArmService(ProgramStore(tmp_path / "programs.yaml"))
    started = service.start_session("mujoco", workspace_root=workspace)
    try:
        assert started["connected"] is True
        with pytest.raises(RuntimeError, match="already active"):
            service.start_session("mujoco", workspace_root=workspace)
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
        assert np.abs(close_pixels - open_pixels).mean() > 5.0
        assert service.telemetry()["state"]["hand"]["finger1_motor2"]["target"] == pytest.approx(-1.10)
        assert len(service.telemetry()["state"]["hand"]) == 8
    finally:
        assert service.disconnect()["connected"] is False
        assert not any(thread.name == "superarm-mujoco" and thread.is_alive() for thread in __import__("threading").enumerate())


def test_live_command_throttle_and_timeout(tmp_path: Path) -> None:
    class Runtime:
        connected = True
        failure = None

        def command(self, *args, **kwargs): pass
        def stop(self): self.stopped = True
        def observe(self): return {}

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

        def command(self, *args, **kwargs): pass
        def stop(self): pass
        def observe(self): return {}

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
    assert "/api/superarm/session" in paths
    assert "/api/superarm/action" in paths
    assert "/api/superarm/video" in paths
    assert "/ws/superarm" in paths
    response = TestClient(app).get("/api/superarm/capabilities")
    assert response.status_code == 200
    assert response.json()["runtimes"]["isaac_sim"]["enabled"] is False
