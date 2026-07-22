"""Long-lived localhost control bridge for the validated SuperArm Isaac USD."""

from __future__ import annotations

import argparse
import json
import selectors
import socket
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Protocol

from .bridge_protocol import (
    MAX_MESSAGE_BYTES,
    ProtocolError,
    decode_frame,
    encode_message,
    error_response,
    success_response,
    validate_request,
)
from .contracts import ARM_JOINTS, HAND_JOINTS, PHYSICAL_JOINTS, validate_physical_targets


class BridgeRuntime(Protocol):
    def hello(self) -> dict[str, Any]: ...

    def step(self) -> None: ...

    def command(self, targets: Mapping[str, float]) -> dict[str, Any]: ...

    def observe(self) -> dict[str, Any]: ...

    def hold(self) -> dict[str, Any]: ...

    def capture(self, view: str, name: str) -> dict[str, Any]: ...

    def close(self) -> None: ...


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", required=True, type=Path)
    parser.add_argument("--entrypoint", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--webrtc", action="store_true")
    parser.add_argument("--webrtc-signal-port", default=49100, type=int)
    parser.add_argument("--webrtc-stream-port", default=47998, type=int)
    parser.add_argument("--webrtc-public-ip", default="")
    return parser


def _resolved_child(root: Path, candidate: Path, *, label: str) -> Path:
    root = root.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError(f"{label} must resolve beneath {root}")
    return resolved


def _validate_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.host != "127.0.0.1":
        raise ValueError("managed Isaac bridge binds only 127.0.0.1")
    if not 1 <= args.port <= 65535:
        raise ValueError("bridge port must be between 1 and 65535")
    if not 1 <= args.webrtc_signal_port <= 65535:
        raise ValueError("WebRTC signaling port must be between 1 and 65535")
    if not 1 <= args.webrtc_stream_port <= 65535:
        raise ValueError("WebRTC media port must be between 1 and 65535")
    args.asset_root = args.asset_root.resolve(strict=True)
    if not args.asset_root.is_dir():
        raise ValueError("asset root must be a directory")
    args.entrypoint = _resolved_child(args.asset_root, args.entrypoint, label="entrypoint")
    if not args.entrypoint.is_file():
        raise ValueError("entrypoint must be a file")
    args.run_dir = args.run_dir.resolve(strict=True)
    if not args.run_dir.is_dir():
        raise ValueError("run directory must be a directory")
    args.token_file = args.token_file.resolve(strict=True)
    if not args.token_file.is_file():
        raise ValueError("token file must be a file")
    return args


def _read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("bridge token file is empty")
    if len(token.encode("utf-8")) > 4096:
        raise ValueError("bridge token is too large")
    return token


def dispatch_request(runtime: BridgeRuntime, request: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Dispatch one validated request on the Isaac-owning main thread."""

    op = request["op"]
    if op == "hello":
        return runtime.hello(), False
    if op == "command":
        return runtime.command(request["targets"]), False
    if op == "observe":
        return runtime.observe(), False
    if op == "hold":
        return runtime.hold(), False
    if op == "capture":
        return runtime.capture(request["view"], request["name"]), False
    if op == "shutdown":
        return {"accepted": True}, True
    raise ProtocolError("unknown_op", f"unsupported bridge operation: {op!r}")


def _send(conn: socket.socket, message: Mapping[str, Any]) -> None:
    frame = encode_message(message)
    conn.setblocking(True)
    try:
        conn.settimeout(0.25)
        conn.sendall(frame)
    finally:
        conn.settimeout(0.0)
        conn.setblocking(False)


def serve(runtime: BridgeRuntime, *, host: str, port: int, token: str) -> None:
    """Run a single-client JSONL server while stepping physics on this thread."""

    handshake_timeout_s = 1.0
    selector = selectors.DefaultSelector()
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((host, port))
    listener.listen(1)
    listener.setblocking(False)
    selector.register(listener, selectors.EVENT_READ, data="listener")
    pending: socket.socket | None = None
    pending_buffer = b""
    pending_deadline = 0.0
    active: socket.socket | None = None
    active_buffer = b""
    shutdown = False

    def close_connection(conn: socket.socket | None) -> None:
        if conn is None:
            return
        with suppress(Exception):
            selector.unregister(conn)
        conn.close()

    print(json.dumps({"event": "listening", "host": host, "port": port}), flush=True)
    try:
        while not shutdown:
            runtime.step()
            if pending is not None and time.monotonic() >= pending_deadline:
                close_connection(pending)
                pending = None
                pending_buffer = b""
            for key, _ in selector.select(timeout=0):
                if key.data == "listener":
                    candidate, _address = listener.accept()
                    candidate.setblocking(False)
                    if active is not None:
                        candidate.close()
                        continue
                    close_connection(pending)
                    pending = candidate
                    pending_buffer = b""
                    pending_deadline = time.monotonic() + handshake_timeout_s
                    selector.register(pending, selectors.EVENT_READ, data="pending")
                    continue
                if key.data == "pending":
                    if pending is None:
                        continue
                    try:
                        chunk = pending.recv(4096)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        close_connection(pending)
                        pending = None
                        pending_buffer = b""
                        continue
                    pending_buffer += chunk
                    if len(pending_buffer) > MAX_MESSAGE_BYTES and b"\n" not in pending_buffer:
                        close_connection(pending)
                        pending = None
                        pending_buffer = b""
                        continue
                    if b"\n" not in pending_buffer:
                        continue
                    raw, remainder = pending_buffer.split(b"\n", 1)
                    request_id = "invalid"
                    try:
                        if remainder:
                            raise ProtocolError(
                                "invalid_request", "hello must be the only handshake frame"
                            )
                        decoded = decode_frame(raw + b"\n")
                        if isinstance(decoded.get("request_id"), str) and decoded["request_id"]:
                            request_id = decoded["request_id"][:128]
                        request = validate_request(decoded, expected_token=token)
                        if request["op"] != "hello":
                            raise ProtocolError(
                                "invalid_request", "first bridge operation must be hello"
                            )
                        payload, _ = dispatch_request(runtime, request)
                        _send(pending, success_response(request["request_id"], **payload))
                    except ProtocolError as exc:
                        with suppress(OSError, ProtocolError):
                            _send(pending, error_response(request_id, exc))
                        close_connection(pending)
                        pending = None
                        pending_buffer = b""
                        continue
                    except Exception as exc:
                        detail = str(exc).replace(token, "[REDACTED]")
                        with suppress(OSError, ProtocolError):
                            _send(
                                pending,
                                error_response(
                                    request_id,
                                    ProtocolError(
                                        "runtime_error",
                                        f"Isaac bridge hello failed: {detail}",
                                    ),
                                ),
                            )
                        close_connection(pending)
                        pending = None
                        pending_buffer = b""
                        continue
                    selector.unregister(pending)
                    selector.register(pending, selectors.EVENT_READ, data="active")
                    active = pending
                    active_buffer = b""
                    pending = None
                    pending_buffer = b""
                    continue
                if active is None:
                    continue
                try:
                    chunk = active.recv(4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    close_connection(active)
                    active = None
                    active_buffer = b""
                    continue
                active_buffer += chunk
                if len(active_buffer) > MAX_MESSAGE_BYTES and b"\n" not in active_buffer:
                    close_connection(active)
                    active = None
                    active_buffer = b""
                    continue
                while active is not None and b"\n" in active_buffer:
                    raw, active_buffer = active_buffer.split(b"\n", 1)
                    request_id = "invalid"
                    try:
                        decoded = decode_frame(raw + b"\n")
                        if isinstance(decoded.get("request_id"), str) and decoded["request_id"]:
                            request_id = decoded["request_id"][:128]
                        request = validate_request(decoded, expected_token=token)
                        payload, should_shutdown = dispatch_request(runtime, request)
                        response = success_response(request["request_id"], **payload)
                        shutdown = shutdown or should_shutdown
                    except ProtocolError as exc:
                        response = error_response(request_id, exc)
                    except Exception as exc:  # Isaac failures are bounded at the process boundary.
                        detail = str(exc).replace(token, "[REDACTED]")
                        response = error_response(
                            request_id,
                            ProtocolError("runtime_error", f"Isaac bridge operation failed: {detail}"),
                        )
                    try:
                        _send(active, response)
                    except (OSError, ProtocolError):
                        close_connection(active)
                        active = None
                        active_buffer = b""
                    if shutdown:
                        active_buffer = b""
                        break
    finally:
        close_connection(pending)
        close_connection(active)
        selector.unregister(listener)
        listener.close()
        selector.close()


def require_unique_articulation_root(candidates: Sequence[Any]) -> Any:
    """Return the sole articulation root and reject compound/malformed stages."""

    if len(candidates) != 1:
        raise RuntimeError(f"expected one articulation root, found {len(candidates)}")
    return candidates[0]


def simulation_app_launch(args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    """Build the Isaac launch contract without importing Isaac on the host."""

    config: dict[str, Any] = {
        "headless": True,
        "renderer": "RaytracedLighting",
        "width": 1280,
        "height": 720,
        "window_width": 1280,
        "window_height": 720,
    }
    if not args.webrtc:
        return config, ""

    prefix = "/exts/omni.kit.livestream.app/primaryStream"
    extra_args = [
        f"--{prefix}/signalPort={args.webrtc_signal_port}",
        f"--{prefix}/streamPort={args.webrtc_stream_port}",
        f'--{prefix}/streamType="webrtc"',
        f"--{prefix}/targetFps=30",
    ]
    if args.webrtc_public_ip:
        extra_args.append(f'--{prefix}/publicIp="{args.webrtc_public_ip}"')
    config.update({"hide_ui": False, "extra_args": extra_args})
    return config, "/isaac-sim/apps/isaacsim.exp.full.streaming.kit"


def _run_isaac(args: argparse.Namespace, token: str) -> None:
    from isaacsim import SimulationApp

    launch_config, experience = simulation_app_launch(args)
    app = SimulationApp(launch_config, experience=experience)

    try:
        _run_isaac_after_app(args, token, app)
    finally:
        app.close()


def _run_isaac_after_app(args: argparse.Namespace, token: str, app: Any) -> None:
    app.update()
    import numpy as np
    import omni.timeline
    import omni.usd
    from isaacsim.core.api import World
    from isaacsim.core.experimental.prims import Articulation
    from isaacsim.core.rendering_manager import ViewportManager
    from pxr import Gf, Usd, UsdGeom, UsdLux, UsdPhysics

    def flat(values: Any) -> list[float]:
        if hasattr(values, "numpy"):
            values = values.numpy()
        array = np.asarray(values, dtype=np.float64)
        if array.ndim > 1:
            array = array[0]
        return [float(value) for value in array.tolist()]

    def discover_root(stage: Usd.Stage):
        matches = [prim for prim in stage.Traverse() if prim.HasAPI(UsdPhysics.ArticulationRootAPI)]
        return require_unique_articulation_root(matches)

    class IsaacRuntime:
        def __init__(self) -> None:
            if not omni.usd.get_context().open_stage(str(args.entrypoint)):
                raise RuntimeError(f"Isaac could not open USD: {args.entrypoint}")
            for _ in range(8):
                app.update()
            self.stage = omni.usd.get_context().get_stage()
            self.root = discover_root(self.stage)
            self.root_path = str(self.root.GetPath())
            self.world = World(stage_units_in_meters=1.0, physics_dt=1.0 / 120.0, rendering_dt=1.0 / 30.0)
            self.timeline = omni.timeline.get_timeline_interface()
            self.timeline.play()
            self.art = Articulation(self.root_path)
            self.world.reset()
            self.viewport_metadata: dict[str, Any] | None = None
            if args.webrtc:
                from omni.kit.viewport.utility import get_active_viewport

                # The distributed robot USD intentionally contains no authored
                # camera or scene light. Configure both after World.reset(),
                # because reset can replace the active viewport camera state.
                UsdLux.DomeLight.Define(
                    self.stage,
                    "/LeLabWebRtcDomeLight",
                ).CreateIntensityAttr(700.0)
                self.webrtc_camera = UsdGeom.Camera.Define(self.stage, "/LeLabWebRtcCamera")
                self.webrtc_camera.GetFocalLengthAttr().Set(35.0)
                eye = Gf.Vec3d(1.6879932047709112, -1.422306776383411, 1.1664553903405106)
                target = Gf.Vec3d(-0.008846982602745329, 0.2745334109902456, 0.39516439607975784)
                camera_xform = UsdGeom.Xformable(self.webrtc_camera)
                camera_xform.ClearXformOpOrder()
                camera_xform.AddTransformOp().Set(
                    Gf.Matrix4d().SetLookAt(eye, target, Gf.Vec3d(0.0, 0.0, 1.0)).GetInverse()
                )
                ViewportManager.set_camera(self.webrtc_camera)
                viewport = get_active_viewport()
                viewport_stage = viewport.stage if viewport is not None else None
                bounds = UsdGeom.BBoxCache(
                    Usd.TimeCode.Default(),
                    [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
                    useExtentsHint=False,
                ).ComputeWorldBound(self.root).ComputeAlignedRange()
                camera_transform = camera_xform.ComputeLocalToWorldTransform(Usd.TimeCode.Default())
                self.viewport_metadata = {
                    "camera_path": str(viewport.camera_path) if viewport is not None else None,
                    "camera_position": [float(value) for value in camera_transform.ExtractTranslation()],
                    "bounds_min": [float(value) for value in bounds.GetMin()],
                    "bounds_max": [float(value) for value in bounds.GetMax()],
                    "resolution": list(viewport.resolution) if viewport is not None else None,
                    "stage_identifier": (
                        viewport_stage.GetRootLayer().identifier if viewport_stage is not None else None
                    ),
                    "controlled_stage_identifier": self.stage.GetRootLayer().identifier,
                }
                print(json.dumps({"event": "viewport_ready", **self.viewport_metadata}), flush=True)
                for _ in range(8):
                    self.world.step(render=True)
            else:
                for _ in range(2):
                    self.world.step(render=False)
            if not self.art.is_physics_tensor_entity_valid():
                raise RuntimeError("Isaac physics tensor did not initialize the articulation")
            actual = set(self.art.dof_names)
            expected = set(PHYSICAL_JOINTS)
            if self.art.num_dofs != 13 or actual != expected:
                raise RuntimeError(
                    "Isaac articulation contract mismatch: "
                    f"count={self.art.num_dofs}, missing={sorted(expected - actual)}, "
                    f"extra={sorted(actual - expected)}"
                )
            self.indices = {
                name: int(self.art.dof_names.index(name)) for name in PHYSICAL_JOINTS
            }
            positions = flat(self.art.get_dof_positions())
            self.targets = {name: positions[self.indices[name]] for name in PHYSICAL_JOINTS}
            self.physics_step = 2
            self.command_sequence = 0

        def hello(self) -> dict[str, Any]:
            payload = {
                "runtime": "isaac_sim",
                "isaac_sim_version": "6.0.0",
                "articulation_root": self.root_path,
                "articulation_root_count": 1,
                "physical_dof_count": 13,
                "logical_action_width": 6,
                "joint_names": list(PHYSICAL_JOINTS),
            }
            if args.webrtc:
                payload["webrtc"] = {
                    "enabled": True,
                    "signal_port": args.webrtc_signal_port,
                    "stream_port": args.webrtc_stream_port,
                    "viewport": self.viewport_metadata,
                }
            return payload

        def step(self) -> None:
            self.world.step(render=args.webrtc)
            self.physics_step += 1

        def command(self, targets: Mapping[str, float]) -> dict[str, Any]:
            self.targets = validate_physical_targets(targets)
            indices = np.asarray([self.indices[name] for name in PHYSICAL_JOINTS], dtype=np.int32)
            values = np.asarray([self.targets[name] for name in PHYSICAL_JOINTS], dtype=np.float32)
            self.art.set_dof_position_targets(values, dof_indices=indices)
            self.command_sequence += 1
            return {"accepted": True, "command_sequence": self.command_sequence}

        def observe(self) -> dict[str, Any]:
            positions = flat(self.art.get_dof_positions())

            def subsystem(names: Sequence[str]) -> dict[str, Any]:
                result = {}
                for name in names:
                    position = positions[self.indices[name]]
                    target = self.targets[name]
                    error = target - position
                    result[name] = {
                        "position": position,
                        "target": target,
                        "error": error,
                        "moving": abs(error) > 0.01,
                    }
                return result

            return {
                "runtime": "isaac_sim",
                "connected": True,
                "timestamp": time.time(),
                "physics_step": self.physics_step,
                "command_sequence": self.command_sequence,
                "arm": subsystem(ARM_JOINTS),
                "hand": subsystem(HAND_JOINTS),
            }

        def hold(self) -> dict[str, Any]:
            positions = flat(self.art.get_dof_positions())
            return self.command({name: positions[self.indices[name]] for name in PHYSICAL_JOINTS})

        def capture(self, view: str, name: str) -> dict[str, Any]:
            del view, name
            raise RuntimeError(
                "live Isaac capture is disabled; use the separately validated static USD evidence"
            )

        def close(self) -> None:
            self.timeline.stop()

    runtime: IsaacRuntime | None = None
    try:
        runtime = IsaacRuntime()
        serve(runtime, host=args.host, port=args.port, token=token)
    finally:
        if runtime is not None:
            runtime.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = _validate_args(_parser().parse_args(argv))
    token = _read_token(args.token_file)
    _run_isaac(args, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
