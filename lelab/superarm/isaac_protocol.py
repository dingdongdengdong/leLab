"""Bounded, serialized host client for the local Isaac Sim bridge."""

from __future__ import annotations

import socket
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from typing import Any, Literal

from isaacsim_validation.bridge_protocol import (
    MAX_MESSAGE_BYTES,
    SCHEMA,
    ProtocolError,
    decode_frame,
    encode_message,
    validate_request,
    validate_response,
)
from isaacsim_validation.contracts import validate_physical_targets


class IsaacBridgeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class IsaacBridgeClient:
    """One-request-at-a-time JSONL client with no implicit state-change retry."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        token: str,
        timeout_s: float = 2.0,
        capture_timeout_s: float = 120.0,
        socket_factory: Callable[[tuple[str, int], float], socket.socket] = socket.create_connection,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout_s = float(timeout_s)
        self.capture_timeout_s = float(capture_timeout_s)
        self._token = token
        self._socket_factory = socket_factory
        self._socket: socket.socket | None = None
        self._buffer = b""
        self._hello: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(host={self.host!r}, port={self.port}, "
            f"connected={self.connected})"
        )

    def _redact(self, message: str) -> str:
        return message.replace(self._token, "[REDACTED]") if self._token else message

    @property
    def connected(self) -> bool:
        return self._socket is not None and self._hello is not None

    def connect(self) -> dict[str, Any]:
        with self._lock:
            if self.connected:
                return dict(self._hello or {})
            self.close()
            try:
                self._socket = self._socket_factory((self.host, self.port), self.timeout_s)
                self._socket.settimeout(self.timeout_s)
                hello = self._request("hello", allow_handshake=True)
            except Exception:
                self.close()
                raise
            self._hello = hello
            return dict(hello)

    def _read_frame(self) -> bytes:
        if self._socket is None:
            raise IsaacBridgeError("disconnected", "Isaac bridge is disconnected")
        while b"\n" not in self._buffer:
            try:
                chunk = self._socket.recv(4096)
            except TimeoutError as exc:
                raise IsaacBridgeError("timeout", "Isaac bridge response timed out") from exc
            except OSError as exc:
                raise IsaacBridgeError("connection_error", "Isaac bridge connection failed") from exc
            if not chunk:
                raise IsaacBridgeError(
                    "truncated_frame", "Isaac bridge closed before completing a response"
                )
            self._buffer += chunk
            if len(self._buffer) > MAX_MESSAGE_BYTES:
                raise IsaacBridgeError("message_too_large", "Isaac bridge response is too large")
        payload, self._buffer = self._buffer.split(b"\n", 1)
        return payload + b"\n"

    def _request(self, op: str, *, allow_handshake: bool = False, **payload: Any) -> dict[str, Any]:
        with self._lock:
            if self._socket is None or (not allow_handshake and not self.connected):
                raise IsaacBridgeError("disconnected", "Isaac bridge is disconnected")
            request_id = uuid.uuid4().hex
            request = {
                "schema": SCHEMA,
                "request_id": request_id,
                "token": self._token,
                "op": op,
                **payload,
            }
            try:
                validate_request(request, expected_token=self._token)
                frame = encode_message(request)
                self._socket.sendall(frame)
                response = validate_response(decode_frame(self._read_frame()), request_id=request_id)
            except ProtocolError as exc:
                self.close()
                raise IsaacBridgeError(exc.code, str(exc)) from exc
            except IsaacBridgeError:
                self.close()
                raise
            except (TimeoutError, OSError) as exc:
                self.close()
                code = "timeout" if isinstance(exc, TimeoutError) else "connection_error"
                raise IsaacBridgeError(code, "Isaac bridge request failed") from exc
            if response["ok"] is False:
                error = response["error"]
                raise IsaacBridgeError(error["code"], self._redact(error["message"]))
            return {key: value for key, value in response.items() if key not in {"schema", "request_id", "ok"}}

    def command(self, targets: Mapping[str, float]) -> dict[str, Any]:
        return self._request("command", targets=validate_physical_targets(targets))

    def observe(self) -> dict[str, Any]:
        return self._request("observe")

    def hold(self) -> dict[str, Any]:
        return self._request("hold")

    def capture(self, view: Literal["whole", "hand"], name: str) -> dict[str, Any]:
        with self._lock:
            sock = self._socket
            if sock is None:
                raise IsaacBridgeError("disconnected", "Isaac bridge is disconnected")
            previous_timeout = sock.gettimeout()
            sock.settimeout(self.capture_timeout_s)
            try:
                return self._request("capture", view=view, name=name)
            finally:
                if self._socket is sock:
                    sock.settimeout(previous_timeout)

    def shutdown(self) -> dict[str, Any]:
        try:
            return self._request("shutdown")
        finally:
            self.close()

    def close(self) -> None:
        with self._lock:
            sock, self._socket = self._socket, None
            self._hello = None
            self._buffer = b""
            if sock is not None:
                with suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
                sock.close()
