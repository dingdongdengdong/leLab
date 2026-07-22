from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable

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
            "error": {"code": "rejected", "message": "command rejected"},
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
