from __future__ import annotations

import socket
import struct
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from isaacsim_validation.bridge_protocol import (
    MAX_MESSAGE_BYTES,
    SCHEMA,
    ProtocolError,
    decode_frame,
    encode_message,
    success_response,
    validate_request,
)
from isaacsim_validation.contracts import expand_logical_action
from lelab.superarm.isaac_protocol import IsaacBridgeClient, IsaacBridgeError


def _request(op: str = "hello", **payload):
    return {
        "schema": SCHEMA,
        "request_id": "request-1",
        "token": "secret",
        "op": op,
        **payload,
    }


def test_codec_emits_one_bounded_newline_frame():
    frame = encode_message(_request())

    assert frame.endswith(b"\n")
    assert frame.count(b"\n") == 1
    assert len(frame) <= MAX_MESSAGE_BYTES
    assert decode_frame(frame) == _request()


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"{}", "incomplete_frame"),
        (b"\n", "invalid_json"),
        (b"{}\n{}\n", "multiple_frames"),
        (b"\xff\n", "invalid_utf8"),
        (b"not-json\n", "invalid_json"),
        (b"[]\n", "invalid_message"),
    ],
)
def test_codec_rejects_malformed_frames(raw: bytes, code: str):
    with pytest.raises(ProtocolError) as raised:
        decode_frame(raw)

    assert raised.value.code == code


def test_codec_rejects_oversized_frame_before_transport():
    with pytest.raises(ProtocolError) as raised:
        encode_message(_request(name="x" * MAX_MESSAGE_BYTES))

    assert raised.value.code == "message_too_large"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value.pop("schema"), "schema_mismatch"),
        (lambda value: value.__setitem__("schema", "wrong/v1"), "schema_mismatch"),
        (lambda value: value.__setitem__("token", "wrong"), "unauthorized"),
        (lambda value: value.__setitem__("op", "unknown"), "unknown_op"),
        (lambda value: value.__setitem__("request_id", ""), "invalid_request"),
    ],
)
def test_server_request_validation_has_stable_error_codes(
    mutate: Callable[[dict], object],
    code: str,
):
    message = _request()
    mutate(message)

    with pytest.raises(ProtocolError) as raised:
        validate_request(message, expected_token="secret")

    assert raised.value.code == code
    assert "secret" not in str(raised.value)


def test_server_rejects_unbounded_request_id():
    message = _request()
    message["request_id"] = "x" * 129

    with pytest.raises(ProtocolError) as raised:
        validate_request(message, expected_token="secret")

    assert raised.value.code == "invalid_request"


def test_auth_rejects_non_ascii_token_without_raw_type_error():
    with pytest.raises(ProtocolError) as raised:
        validate_request(_request(token="secrét"), expected_token="secret")

    assert raised.value.code == "unauthorized"


def test_unknown_operation_error_never_echoes_operation_or_token():
    with pytest.raises(ProtocolError) as raised:
        validate_request(_request("secret"), expected_token="secret")

    assert raised.value.code == "unknown_op"
    assert "secret" not in str(raised.value)


def test_command_request_requires_exact_numeric_targets():
    valid = validate_request(
        _request("command", targets=expand_logical_action([0.0] * 6)),
        expected_token="secret",
    )
    assert len(valid["targets"]) == 13

    with pytest.raises(ProtocolError) as raised:
        validate_request(
            _request("command", targets={"joint_rev_1": 0.0}),
            expected_token="secret",
        )
    assert raised.value.code == "invalid_targets"


@pytest.mark.parametrize("name", ["../escape", "/absolute", "bad/name", "bad\\name", ""])
def test_capture_request_rejects_unsafe_names(name: str):
    with pytest.raises(ProtocolError) as raised:
        validate_request(
            _request("capture", view="hand", name=name),
            expected_token="secret",
        )
    assert raised.value.code == "invalid_capture"


def test_success_response_rejects_reserved_payload_fields():
    with pytest.raises(ProtocolError) as raised:
        success_response("request-1", ok=False)

    assert raised.value.code == "invalid_response"


class _SocketFactory:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.calls = 0

    def __call__(self, address: tuple[str, int], timeout: float):
        del address, timeout
        self.calls += 1
        return self.sock


def _start_server(
    server: socket.socket,
    handler: Callable[[dict], dict | None],
    *,
    fragment: bool = False,
) -> threading.Thread:
    def run():
        buffer = b""
        try:
            while True:
                chunk = server.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    request = decode_frame(raw + b"\n")
                    response = handler(request)
                    if response is None:
                        return
                    encoded = encode_message(response)
                    try:
                        if fragment:
                            midpoint = len(encoded) // 2
                            server.sendall(encoded[:midpoint])
                            time.sleep(0.01)
                            server.sendall(encoded[midpoint:])
                        else:
                            server.sendall(encoded)
                    except OSError:
                        return
        finally:
            server.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def _client_and_server(handler, *, fragment: bool = False, timeout_s: float = 0.2):
    client_socket, server_socket = socket.socketpair()
    client_socket.settimeout(timeout_s)
    factory = _SocketFactory(client_socket)
    thread = _start_server(server_socket, handler, fragment=fragment)
    client = IsaacBridgeClient(
        "127.0.0.1",
        8765,
        token="secret",
        timeout_s=timeout_s,
        socket_factory=factory,
    )
    return client, thread, factory


def _ok(request: dict, **payload):
    return success_response(request["request_id"], **payload)


def test_client_reassembles_fragmented_response_and_matches_request_id():
    def handler(request):
        return _ok(
            request,
            runtime="isaac_sim",
            physical_dof_count=13,
            logical_action_width=6,
        )

    client, thread, _ = _client_and_server(handler, fragment=True)
    hello = client.connect()

    assert hello["physical_dof_count"] == 13
    client.close()
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_client_rejects_wrong_response_schema_and_request_id():
    cases = [
        {"schema": "wrong/v1", "request_id": "request", "ok": True},
        {"schema": SCHEMA, "request_id": "wrong", "ok": True},
    ]
    for response in cases:
        client, thread, _ = _client_and_server(lambda request, response=response: response)
        with pytest.raises(IsaacBridgeError) as raised:
            client.connect()
        assert raised.value.code in {"schema_mismatch", "request_mismatch"}
        thread.join(timeout=1)


def test_client_error_redacts_token():
    def handler(request):
        return {
            "schema": SCHEMA,
            "request_id": request["request_id"],
            "ok": False,
            "error": {"code": "rejected", "message": "command rejected for secret"},
        }

    client, thread, _ = _client_and_server(handler)
    with pytest.raises(IsaacBridgeError) as raised:
        client.connect()

    assert raised.value.code == "rejected"
    assert "secret" not in str(raised.value)
    assert "secret" not in repr(client)
    thread.join(timeout=1)


def test_client_validates_command_before_sending():
    requests: list[dict] = []

    def handler(request):
        requests.append(request)
        if request["op"] == "hello":
            return _ok(request, physical_dof_count=13, logical_action_width=6)
        return _ok(request, accepted=True)

    client, thread, _ = _client_and_server(handler)
    client.connect()
    with pytest.raises(ValueError, match="exactly 13"):
        client.command({"joint_rev_1": 0.0})
    client.close()
    thread.join(timeout=1)

    assert [request["op"] for request in requests] == ["hello"]


def test_client_does_not_retry_state_change_after_truncated_response():
    requests: list[dict] = []

    def handler(request):
        requests.append(request)
        if request["op"] == "hello":
            return _ok(request, physical_dof_count=13, logical_action_width=6)
        return None

    client, thread, factory = _client_and_server(handler)
    client.connect()

    with pytest.raises(IsaacBridgeError) as raised:
        client.command(expand_logical_action([0.0] * 6))

    assert raised.value.code == "truncated_frame"
    assert factory.calls == 1
    assert [request["op"] for request in requests] == ["hello", "command"]
    thread.join(timeout=1)


def test_timeout_invalidates_connection_until_explicit_reconnect():
    def handler(request):
        if request["op"] == "hello":
            time.sleep(0.2)
        return _ok(request)

    client, thread, _ = _client_and_server(handler, timeout_s=0.05)
    with pytest.raises(IsaacBridgeError) as raised:
        client.connect()
    assert raised.value.code == "timeout"
    assert client.connected is False
    with pytest.raises(IsaacBridgeError, match="disconnected"):
        client.observe()
    thread.join(timeout=1)


def test_capture_uses_a_longer_timeout_without_weakening_control_requests():
    def handler(request):
        if request["op"] == "capture":
            time.sleep(0.1)
            return _ok(request, path="captures/hand.png", bytes=7)
        return _ok(request, runtime="isaac_sim")

    client_socket, server_socket = socket.socketpair()
    factory = _SocketFactory(client_socket)
    thread = _start_server(server_socket, handler)
    client = IsaacBridgeClient(
        "127.0.0.1",
        8765,
        token="secret",
        timeout_s=0.05,
        capture_timeout_s=0.2,
        socket_factory=factory,
    )
    client.connect()

    assert client.capture("hand", "hand")["path"] == "captures/hand.png"
    assert client_socket.gettimeout() == pytest.approx(0.05)
    client.close()
    thread.join(timeout=1)


def test_client_serializes_concurrent_requests_without_frame_interleaving():
    operations: list[str] = []

    def handler(request):
        operations.append(request["op"])
        return _ok(request, observed=True)

    client, thread, _ = _client_and_server(handler)
    client.connect()
    errors: list[Exception] = []

    def observe():
        try:
            client.observe()
        except Exception as exc:  # pragma: no cover - assertion reports captured failures
            errors.append(exc)

    workers = [threading.Thread(target=observe) for _ in range(4)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=1)
    client.close()
    thread.join(timeout=1)

    assert errors == []
    assert operations == ["hello", "observe", "observe", "observe", "observe"]


def test_control_bridge_import_is_host_safe_and_runtime_import_order_is_guarded():
    from isaacsim_validation import control_bridge

    source = Path(control_bridge.__file__).read_text(encoding="utf-8")
    app_index = source.index("SimulationApp(")
    cleanup_try_index = source.index("try:", app_index)
    runtime_index = source.index("def _run_isaac_after_app", app_index)
    omni_import_index = source.index("import omni", runtime_index)
    assert cleanup_try_index < omni_import_index
    assert source.index("app.update()", runtime_index) < omni_import_index
    assert omni_import_index > app_index
    assert source.index("from pxr", app_index) > app_index
    assert source.index("from isaacsim.core", app_index) > app_index
    capture_index = source.index("def capture(self, view: str, name: str)", runtime_index)
    close_index = source.index("def close(self)", capture_index)
    assert "live Isaac capture is disabled" in source[capture_index:close_index]
    assert "import omni.replicator.core as rep" not in source[runtime_index:]
    assert "from isaacsim.sensors.camera import Camera" in source[runtime_index:]
    assert "self._workspace_camera.initialize()" in source[runtime_index:]
    assert "self._workspace_camera.get_rgba()" in source[runtime_index:]
    assert "self._workspace_camera.destroy()" in source[runtime_index:]
    assert "saved_positions = np.asarray(flat(self.art.get_dof_positions())" in source[runtime_index:]
    assert "backend_utils.use_backend" not in source[runtime_index:]
    assert "useFabricSceneDelegate" not in source[runtime_index:]
    assert "contract = author_or_update_passive_linkage_runtime(" in source[runtime_index:]
    physics_ready_index = source.index(
        'print(json.dumps({"event": "rl_articulation_valid"})',
        runtime_index,
    )
    passive_author_index = source.index("self._author_initial_passive_linkage()", runtime_index)
    assert physics_ready_index < passive_author_index
    assert "valid_frames += 1" in source[runtime_index:]
    assert "if valid_frames >= 4:" in source[runtime_index:]
    assert "self.art.set_dof_positions(saved_positions)" in source[runtime_index:]
    assert "self.cube.set_linear_velocity(cube_linear_velocity)" in source[runtime_index:]
    assert "UsdLux.DomeLight.Define(\n                    self.stage" in source[runtime_index:]
    reset_index = source.index("self.world.reset()", runtime_index)
    light_index = source.index("UsdLux.DomeLight.Define(", runtime_index)
    custom_camera_index = source.index("UsdGeom.Camera.Define(", runtime_index)
    look_at_index = source.index("Gf.Matrix4d().SetLookAt(", runtime_index)
    active_camera_index = source.index("ViewportManager.set_camera(self.webrtc_camera)", runtime_index)
    assert reset_index < light_index < custom_camera_index
    assert custom_camera_index < look_at_index < active_camera_index
    assert ".GetInverse()" in source[look_at_index:active_camera_index]
    assert "frame_viewport_prims" not in source[runtime_index:]
    assert "ViewportManager.set_camera_view(" not in source[runtime_index:]
    assert "self.world.step(render=True)" in source[active_camera_index:]
    assert source.index("finally:\n        app.close()", app_index) > cleanup_try_index


def test_articulation_root_selection_requires_exactly_one_candidate():
    from isaacsim_validation.control_bridge import require_unique_articulation_root

    only = object()
    assert require_unique_articulation_root([only]) is only
    with pytest.raises(RuntimeError, match="expected one articulation root, found 0"):
        require_unique_articulation_root([])
    with pytest.raises(RuntimeError, match="expected one articulation root, found 2"):
        require_unique_articulation_root([object(), object()])


def test_control_bridge_uses_official_isaac_streaming_experience_for_webrtc():
    from argparse import Namespace

    from isaacsim_validation.control_bridge import simulation_app_launch

    config, experience = simulation_app_launch(
        Namespace(
            webrtc=True,
            webrtc_signal_port=49100,
            webrtc_stream_port=47998,
            webrtc_public_ip="100.96.41.100",
        )
    )

    assert experience == "/isaac-sim/apps/isaacsim.exp.full.streaming.kit"
    assert config["headless"] is True
    assert config["hide_ui"] is False
    assert (config["width"], config["height"]) == (1280, 720)
    assert (config["window_width"], config["window_height"]) == (1280, 720)
    assert "--/exts/omni.kit.livestream.app/primaryStream/signalPort=49100" in config[
        "extra_args"
    ]
    assert "--/exts/omni.kit.livestream.app/primaryStream/streamPort=47998" in config[
        "extra_args"
    ]
    assert (
        '--/exts/omni.kit.livestream.app/primaryStream/publicIp="100.96.41.100"'
        in config["extra_args"]
    )


def test_control_bridge_keeps_non_webrtc_launch_lightweight():
    from argparse import Namespace

    from isaacsim_validation.control_bridge import simulation_app_launch

    config, experience = simulation_app_launch(
        Namespace(
            webrtc=False,
            webrtc_signal_port=49100,
            webrtc_stream_port=47998,
            webrtc_public_ip="",
        )
    )

    assert experience == ""
    assert "extra_args" not in config


def test_control_bridge_uses_verified_replicator_launch_contract_for_rl():
    from argparse import Namespace

    from isaacsim_validation.control_bridge import simulation_app_launch

    config, experience = simulation_app_launch(
        Namespace(
            replicator_rgb=True,
            webrtc=False,
            webrtc_signal_port=49100,
            webrtc_stream_port=47998,
            webrtc_public_ip="",
        )
    )

    assert experience == ""
    assert config["headless"] is False
    assert config["enable_cameras"] is True
    assert "--/exts/isaacsim.core.throttling/enable_async=false" in config["extra_args"]


def test_control_bridge_dispatch_survives_repeated_capture_then_command():
    from isaacsim_validation.control_bridge import dispatch_request

    class FakeRuntime:
        def __init__(self):
            self.commands = []
            self.captures = []

        def capture(self, view, name):
            self.captures.append((view, name))
            return {"path": f"captures/{name}.png", "bytes": 123}

        def command(self, targets):
            self.commands.append(targets)
            return {"accepted": True, "command_sequence": len(self.commands)}

    runtime = FakeRuntime()
    first, shutdown = dispatch_request(
        runtime,
        {"op": "capture", "request_id": "one", "view": "hand", "name": "before"},
    )
    second, shutdown_again = dispatch_request(
        runtime,
        {"op": "capture", "request_id": "two", "view": "whole", "name": "after"},
    )
    targets = expand_logical_action([0.0] * 6)
    command, command_shutdown = dispatch_request(
        runtime,
        {"op": "command", "request_id": "three", "targets": targets},
    )

    assert first["path"] == "captures/before.png"
    assert second["path"] == "captures/after.png"
    assert command["accepted"] is True
    assert runtime.captures == [("hand", "before"), ("whole", "after")]
    assert runtime.commands == [targets]
    assert (shutdown, shutdown_again, command_shutdown) == (False, False, False)


def test_control_bridge_server_handles_full_client_lifecycle():
    from isaacsim_validation.control_bridge import serve

    class FakeRuntime:
        def __init__(self):
            self.steps = 0
            self.commands = []

        def hello(self):
            return {
                "runtime": "isaac_sim",
                "physical_dof_count": 13,
                "logical_action_width": 6,
                "joint_names": list(expand_logical_action([0.0] * 6)),
            }

        def step(self):
            self.steps += 1
            time.sleep(0.0001)

        def command(self, targets):
            self.commands.append(targets)
            return {"accepted": True}

        def observe(self):
            return {"physics_step": self.steps}

        def hold(self):
            return {"accepted": True}

        def capture(self, view, name):
            return {"path": f"captures/{view}-{name}.png", "bytes": 123}

        def close(self):
            return None

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    runtime = FakeRuntime()
    thread = threading.Thread(
        target=serve,
        kwargs={"runtime": runtime, "host": "127.0.0.1", "port": port, "token": "secret"},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 1.0
    idle_client = socket.create_connection(("127.0.0.1", port), timeout=0.2)
    client = IsaacBridgeClient("127.0.0.1", port, token="secret", timeout_s=0.2)
    while True:
        try:
            client.connect()
            break
        except (IsaacBridgeError, ConnectionRefusedError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)

    assert client.capture("hand", "one")["path"] == "captures/hand-one.png"
    targets = expand_logical_action([0.0] * 6)
    assert client.command(targets)["accepted"] is True
    assert client.shutdown()["accepted"] is True
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert runtime.commands == [targets]
    assert idle_client.recv(1) == b""
    idle_client.close()


def test_control_bridge_survives_runtime_hello_failure_for_later_client():
    from isaacsim_validation.control_bridge import serve

    class FlakyRuntime:
        def __init__(self):
            self.hello_calls = 0

        def hello(self):
            self.hello_calls += 1
            if self.hello_calls == 1:
                raise RuntimeError("hello failed")
            return {"runtime": "isaac_sim"}

        def step(self):
            time.sleep(0.0001)

        def close(self):
            return None

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    runtime = FlakyRuntime()
    thread = threading.Thread(
        target=serve,
        kwargs={"runtime": runtime, "host": "127.0.0.1", "port": port, "token": "secret"},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 1.0
    first = IsaacBridgeClient("127.0.0.1", port, token="secret", timeout_s=0.2)
    while True:
        try:
            first.connect()
        except ConnectionRefusedError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
            continue
        except IsaacBridgeError as exc:
            assert exc.code == "runtime_error"
            break

    second = IsaacBridgeClient("127.0.0.1", port, token="secret", timeout_s=0.2)
    assert second.connect()["runtime"] == "isaac_sim"
    second.shutdown()
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_control_bridge_survives_hello_peer_reset_before_response():
    from isaacsim_validation.control_bridge import serve

    class SlowRuntime:
        def __init__(self):
            self.hello_calls = 0

        def hello(self):
            self.hello_calls += 1
            if self.hello_calls == 1:
                time.sleep(0.05)
            return {"runtime": "isaac_sim"}

        def step(self):
            time.sleep(0.0001)

        def close(self):
            return None

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    runtime = SlowRuntime()
    thread = threading.Thread(
        target=serve,
        kwargs={"runtime": runtime, "host": "127.0.0.1", "port": port, "token": "secret"},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 1.0
    while True:
        try:
            reset_peer = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            break
        except ConnectionRefusedError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
    reset_peer.sendall(encode_message(_request()))
    reset_peer.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    reset_peer.close()
    time.sleep(0.1)

    client = IsaacBridgeClient("127.0.0.1", port, token="secret", timeout_s=0.2)
    assert client.connect()["runtime"] == "isaac_sim"
    client.shutdown()
    thread.join(timeout=1)
    assert not thread.is_alive()


def test_control_launcher_has_exact_isolation_and_lifecycle_contract():
    launcher = Path("isaacsim_validation/run_isaacsim60_control_bridge.sh").read_text(
        encoding="utf-8"
    )

    required = [
        "nvcr.io/nvidia/isaac-sim:6.0.0",
        "--network host",
        'container_gid=$(id -g)',
        '--user "0:$container_gid"',
        "ACCEPT_EULA=Y",
        "NVIDIA_VISIBLE_DEVICES=all",
        "NVIDIA_DRIVER_CAPABILITIES=all",
        "Path(isaacsim_validation.__file__).parent",
        ":/workspace/isaacsim_validation/isaacsim_validation:ro",
        ":/workspace/asset:ro",
        ":/workspace/run:rw",
        "PYTHONPATH=/workspace/isaacsim_validation",
        "--entrypoint /isaac-sim/python.sh",
        "-m isaacsim_validation.control_bridge",
        "--webrtc",
        "ISAACSIM_SIGNAL_PORT",
        "ISAACSIM_STREAM_PORT",
        "container.log",
        "terminate() {",
        "trap cleanup EXIT",
        "trap terminate INT TERM",
        'docker_pid=$!',
        'wait "$docker_pid"',
        "docker rm -f \"$container_name\"",
        "--rl-display",
        "nvcr.io/nvidia/isaac-sim:6.0.1",
        '--user "1234:$container_gid"',
        "-e OMNI_USER_DIR=/tmp/omni-user",
        "-e OMNI_CACHE_DIR=/tmp/omni-cache",
        ":/isaac-sim/kit/cache",
        ":/root/.cache/nvidia/GLCache",
        ":/root/.nv/ComputeCache",
        "-v /tmp/.X11-unix:/tmp/.X11-unix:rw",
        'xdpyinfo -display "$rl_display"',
        "--replicator-rgb",
        "--passive-linkage-visuals",
        "superarm_isaac_runtime",
    ]
    for marker in required:
        assert marker in launcher


def test_control_launcher_rejects_token_inside_rw_run_directory(tmp_path):
    asset_root = tmp_path / "asset"
    run_dir = tmp_path / "run"
    asset_root.mkdir()
    run_dir.mkdir()
    entrypoint = asset_root / "robot.usda"
    entrypoint.write_text("#usda 1.0\n", encoding="utf-8")
    token = run_dir / "token"
    token.write_text("secret\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "isaacsim_validation/run_isaacsim60_control_bridge.sh",
            "--asset-root",
            str(asset_root),
            "--entrypoint",
            str(entrypoint),
            "--run-dir",
            str(run_dir),
            "--token-file",
            str(token),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "outside the read-write run directory" in result.stderr


def test_control_launcher_rejects_rw_run_directory_overlapping_read_only_asset(tmp_path):
    asset_root = tmp_path / "asset"
    asset_root.mkdir()
    entrypoint = asset_root / "robot.usda"
    entrypoint.write_text("#usda 1.0\n", encoding="utf-8")
    token = tmp_path.parent / f"{tmp_path.name}-bridge-token"
    token.write_text("secret\n", encoding="utf-8")
    try:
        result = subprocess.run(
            [
                "bash",
                "isaacsim_validation/run_isaacsim60_control_bridge.sh",
                "--asset-root",
                str(asset_root),
                "--entrypoint",
                str(entrypoint),
                "--run-dir",
                str(tmp_path),
                "--token-file",
                str(token),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        token.unlink(missing_ok=True)

    assert result.returncode == 2
    assert "read-write run directory must not overlap read-only sources" in result.stderr
