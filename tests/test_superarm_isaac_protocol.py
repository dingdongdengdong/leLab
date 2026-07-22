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
    assert cleanup_try_index < source.index("import omni", app_index)
    assert source.index("import omni", app_index) > app_index
    assert source.index("from pxr", app_index) > app_index
    assert source.index("from isaacsim.core", app_index) > app_index
    assert source.index("finally:\n        app.close()", app_index) > cleanup_try_index


def test_articulation_root_selection_requires_exactly_one_candidate():
    from isaacsim_validation.control_bridge import require_unique_articulation_root

    only = object()
    assert require_unique_articulation_root([only]) is only
    with pytest.raises(RuntimeError, match="expected one articulation root, found 0"):
        require_unique_articulation_root([])
    with pytest.raises(RuntimeError, match="expected one articulation root, found 2"):
        require_unique_articulation_root([object(), object()])


class _FakeResource:
    def __init__(self, events, name):
        self.events = events
        self.name = name

    def destroy(self):
        self.events.append(f"destroy:{self.name}")


class _FakeWriter:
    def __init__(self, owner, *, fail_detach=False):
        self.owner = owner
        self.fail_detach = fail_detach

    def initialize(self, *, output_dir, rgb):
        assert rgb is True
        self.owner.output_dir = Path(output_dir)

    def attach(self, products):
        assert len(products) == 1
        self.owner.events.append("attach")

    def detach(self):
        self.owner.events.append("detach")
        if self.fail_detach:
            raise RuntimeError("detach failed")


class _FakeReplicator:
    class _Layer:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def __init__(self, *, fail_detach=False):
        self.events = []
        self.output_dir = None
        self.writer = _FakeWriter(self, fail_detach=fail_detach)
        owner = self

        class Create:
            @staticmethod
            def camera(**_kwargs):
                return _FakeResource(owner.events, "camera")

            @staticmethod
            def render_product(_camera, _resolution, *, force_new):
                assert force_new is True
                return _FakeResource(owner.events, "product")

        class Registry:
            @staticmethod
            def get(name):
                assert name == "BasicWriter"
                return owner.writer

        class Orchestrator:
            @staticmethod
            def step(**_kwargs):
                assert owner.output_dir is not None
                (owner.output_dir / "rgb_0.png").write_bytes(b"new-frame")

            @staticmethod
            def wait_until_complete():
                owner.events.append("complete")

        self.create = Create()
        self.WriterRegistry = Registry()
        self.orchestrator = Orchestrator()

    def new_layer(self):
        return self._Layer()


def test_replicator_capture_atomically_replaces_output_and_destroys_resources(tmp_path):
    from isaacsim_validation.control_bridge import render_replicator_png

    output = tmp_path / "captures" / "hand.png"
    output.parent.mkdir()
    output.write_bytes(b"old-frame")
    rep = _FakeReplicator()

    size = render_replicator_png(
        rep,
        output=output,
        temporary_root=tmp_path,
        eye=[1.0, 1.0, 1.0],
        target=[0.0, 0.0, 0.0],
        image_has_detail=lambda path: path.read_bytes() == b"new-frame",
    )

    assert size == len(b"new-frame")
    assert output.read_bytes() == b"new-frame"
    assert rep.events[-3:] == ["detach", "destroy:product", "destroy:camera"]
    assert not list(tmp_path.glob("capture-*"))


def test_replicator_cleanup_continues_when_detach_fails(tmp_path):
    from isaacsim_validation.control_bridge import render_replicator_png

    rep = _FakeReplicator(fail_detach=True)
    with pytest.raises(RuntimeError, match="detach failed"):
        render_replicator_png(
            rep,
            output=tmp_path / "hand.png",
            temporary_root=tmp_path,
            eye=[1.0, 1.0, 1.0],
            target=[0.0, 0.0, 0.0],
            image_has_detail=lambda _path: True,
        )

    assert "destroy:product" in rep.events
    assert "destroy:camera" in rep.events
    assert not list(tmp_path.glob("capture-*"))


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
        "container.log",
        "trap cleanup EXIT INT TERM",
        "docker rm -f \"$container_name\"",
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
