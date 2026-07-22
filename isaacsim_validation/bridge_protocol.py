"""Pure-stdlib JSON Lines contract shared by LeLab and the Isaac bridge."""

from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from typing import Any

from .contracts import validate_physical_targets

SCHEMA = "lelab.superarm.isaac_bridge/v1"
MAX_MESSAGE_BYTES = 65_536
OPERATIONS = frozenset({"hello", "command", "observe", "hold", "capture", "shutdown"})
CAPTURE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ProtocolError(ValueError):
    """Stable bridge protocol error that is safe to return to a client."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def encode_message(message: Mapping[str, Any]) -> bytes:
    """Encode exactly one bounded UTF-8 JSON object plus a newline."""

    if not isinstance(message, Mapping):
        raise ProtocolError("invalid_message", "bridge message must be an object")
    try:
        frame = json.dumps(
            dict(message),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
    except (TypeError, ValueError) as exc:
        raise ProtocolError("invalid_message", "bridge message is not valid JSON") from exc
    if len(frame) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message_too_large", "bridge message exceeds 65536 bytes")
    return frame


def decode_frame(raw: bytes) -> dict[str, Any]:
    """Decode one complete bounded JSON Lines frame."""

    if len(raw) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message_too_large", "bridge message exceeds 65536 bytes")
    if not raw.endswith(b"\n"):
        raise ProtocolError("incomplete_frame", "bridge frame is missing its newline terminator")
    if raw.count(b"\n") != 1:
        raise ProtocolError("multiple_frames", "expected exactly one bridge frame")
    try:
        text = raw[:-1].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid_utf8", "bridge frame is not valid UTF-8") from exc
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("invalid_json", "bridge frame is not valid JSON") from exc
    if not isinstance(message, dict):
        raise ProtocolError("invalid_message", "bridge message must be an object")
    return message


def _required_text(message: Mapping[str, Any], name: str) -> str:
    value = message.get(name)
    if not isinstance(value, str) or not value:
        raise ProtocolError("invalid_request", f"bridge request requires non-empty {name}")
    return value


def validate_request(message: Mapping[str, Any], *, expected_token: str) -> dict[str, Any]:
    """Validate one authenticated client request and normalize its payload."""

    if message.get("schema") != SCHEMA:
        raise ProtocolError("schema_mismatch", f"bridge schema must be {SCHEMA}")
    request_id = _required_text(message, "request_id")
    if len(request_id) > 128:
        raise ProtocolError("invalid_request", "bridge request_id exceeds 128 characters")
    token = message.get("token")
    if not isinstance(token, str) or not hmac.compare_digest(
        token.encode("utf-8"), expected_token.encode("utf-8")
    ):
        raise ProtocolError("unauthorized", "bridge request token is invalid")
    op = message.get("op")
    if not isinstance(op, str) or op not in OPERATIONS:
        raise ProtocolError("unknown_op", "unsupported bridge operation")

    allowed = {"schema", "request_id", "token", "op"}
    normalized: dict[str, Any] = {"request_id": request_id, "op": op}
    if op == "command":
        allowed.add("targets")
        targets = message.get("targets")
        if not isinstance(targets, Mapping):
            raise ProtocolError("invalid_targets", "command targets must be an object")
        try:
            normalized["targets"] = validate_physical_targets(targets)
        except ValueError as exc:
            raise ProtocolError("invalid_targets", str(exc)) from exc
    elif op == "capture":
        allowed.update({"view", "name"})
        view = message.get("view")
        name = message.get("name")
        if view not in {"whole", "hand"} or not isinstance(name, str) or not CAPTURE_NAME.fullmatch(name):
            raise ProtocolError("invalid_capture", "capture requires whole/hand view and a safe name")
        normalized.update({"view": view, "name": name})
    extra = sorted(set(message) - allowed)
    if extra:
        raise ProtocolError("invalid_request", f"unexpected bridge request fields: {extra}")
    return normalized


def success_response(request_id: str, **payload: Any) -> dict[str, Any]:
    reserved = sorted({"schema", "request_id", "ok"} & payload.keys())
    if reserved:
        raise ProtocolError("invalid_response", f"reserved response fields: {reserved}")
    return {"schema": SCHEMA, "request_id": request_id, "ok": True, **payload}


def error_response(request_id: str, error: ProtocolError) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "request_id": request_id,
        "ok": False,
        "error": {"code": error.code, "message": str(error)},
    }


def validate_response(message: Mapping[str, Any], *, request_id: str) -> dict[str, Any]:
    """Validate response correlation and return a plain dictionary."""

    if message.get("schema") != SCHEMA:
        raise ProtocolError("schema_mismatch", f"bridge schema must be {SCHEMA}")
    if message.get("request_id") != request_id:
        raise ProtocolError("request_mismatch", "bridge response request_id does not match")
    if not isinstance(message.get("ok"), bool):
        raise ProtocolError("invalid_response", "bridge response requires boolean ok")
    if message["ok"] is False:
        error = message.get("error")
        if not isinstance(error, Mapping):
            raise ProtocolError("invalid_response", "bridge error response is malformed")
        code = error.get("code")
        detail = error.get("message")
        if not isinstance(code, str) or not code or not isinstance(detail, str) or not detail:
            raise ProtocolError("invalid_response", "bridge error response is malformed")
    return dict(message)
