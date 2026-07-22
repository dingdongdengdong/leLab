"""Long-lived localhost control bridge for the validated SuperArm Isaac USD."""

from __future__ import annotations

import argparse
import json
import selectors
import shutil
import socket
import tempfile
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


def render_replicator_png(
    rep: Any,
    *,
    output: Path,
    temporary_root: Path,
    eye: Sequence[float],
    target: Sequence[float],
    image_has_detail: Any,
) -> int:
    """Render, validate, and atomically publish one fully torn-down PNG."""

    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="capture-", dir=temporary_root))
    staged = temporary / "validated.png"
    writer = None
    product = None
    camera = None
    try:
        with rep.new_layer():
            camera = rep.create.camera(
                position=eye,
                look_at=target,
                focal_length=35,
                clipping_range=(0.001, 100.0),
            )
            product = rep.create.render_product(camera, (1280, 720), force_new=True)
            writer = rep.WriterRegistry.get("BasicWriter")
            operation_error: BaseException | None = None
            try:
                writer.initialize(output_dir=str(temporary), rgb=True)
                writer.attach([product])
                for _ in range(8):
                    rep.orchestrator.step(delta_time=0.0, rt_subframes=8)
                rep.orchestrator.wait_until_complete()
            except BaseException as exc:
                operation_error = exc

            cleanup_errors: list[Exception] = []
            for cleanup in (
                writer.detach,
                product.destroy,
                getattr(camera, "destroy", lambda: None),
            ):
                try:
                    cleanup()
                except Exception as exc:
                    cleanup_errors.append(exc)
            writer = None
            product = None
            camera = None
            if operation_error is not None:
                raise operation_error
            if cleanup_errors:
                raise RuntimeError(f"Replicator cleanup failed: {cleanup_errors[0]}") from cleanup_errors[0]

        frames = sorted(temporary.glob("rgb*.png"))
        if not frames:
            raise RuntimeError("Replicator did not create an RGB frame")
        shutil.copyfile(frames[-1], staged)
        if not image_has_detail(staged):
            raise RuntimeError("captured image has no visible detail")
        size = staged.stat().st_size
        staged.replace(output)
        return size
    finally:
        for resource, method in ((writer, "detach"), (product, "destroy"), (camera, "destroy")):
            cleanup = getattr(resource, method, None)
            if callable(cleanup):
                with suppress(Exception):
                    cleanup()
        shutil.rmtree(temporary, ignore_errors=True)


def _run_isaac(args: argparse.Namespace, token: str) -> None:
    from isaacsim import SimulationApp

    app = SimulationApp(
        {
            "headless": True,
            "renderer": "RaytracedLighting",
            "width": 1280,
            "height": 720,
        }
    )

    try:
        _run_isaac_after_app(args, token, app)
    finally:
        app.close()


def _run_isaac_after_app(args: argparse.Namespace, token: str, app: Any) -> None:

    import numpy as np
    import omni.replicator.core as rep
    import omni.timeline
    import omni.usd
    from isaacsim.core.api import World
    from isaacsim.core.experimental.prims import Articulation
    from isaacsim.core.utils.extensions import enable_extension
    from pxr import Usd, UsdGeom, UsdPhysics

    from .visuals import image_has_detail

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

    def unique_named(stage: Usd.Stage, name: str):
        matches = [prim for prim in stage.Traverse() if prim.GetName() == name]
        if len(matches) != 1:
            raise RuntimeError(f"expected one prim named {name!r}, found {len(matches)}")
        return matches[0]

    def camera_pose(stage: Usd.Stage, prim: Any, *, closeup: bool) -> tuple[list[float], list[float]]:
        cache = UsdGeom.BBoxCache(
            Usd.TimeCode.Default(),
            [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
            useExtentsHint=closeup,
        )
        bounds = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        minimum = [float(value) for value in bounds.GetMin()]
        maximum = [float(value) for value in bounds.GetMax()]
        center = [(low + high) / 2.0 for low, high in zip(minimum, maximum, strict=True)]
        span = max(maximum[index] - minimum[index] for index in range(3))
        radius = max(span, 0.08)
        factor = 2.4 if closeup else 2.2
        return [center[0] + factor * radius, center[1] - factor * radius, center[2] + radius], center

    class IsaacRuntime:
        def __init__(self) -> None:
            enable_extension("omni.kit.renderer.capture")
            app.update()
            if not omni.usd.get_context().open_stage(str(args.entrypoint)):
                raise RuntimeError(f"Isaac could not open USD: {args.entrypoint}")
            for _ in range(8):
                app.update()
            self.stage = omni.usd.get_context().get_stage()
            self.root = discover_root(self.stage)
            self.root_path = str(self.root.GetPath())
            self.hand_root = unique_named(self.stage, "r_wrist_interface")
            self.world = World(stage_units_in_meters=1.0, physics_dt=1.0 / 120.0, rendering_dt=1.0 / 30.0)
            self.timeline = omni.timeline.get_timeline_interface()
            self.timeline.play()
            self.art = Articulation(self.root_path)
            self.world.reset()
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
            return {
                "runtime": "isaac_sim",
                "isaac_sim_version": "6.0.0",
                "articulation_root": self.root_path,
                "physical_dof_count": 13,
                "logical_action_width": 6,
                "joint_names": list(PHYSICAL_JOINTS),
            }

        def step(self) -> None:
            self.world.step(render=False)
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
            output_dir = args.run_dir / "captures"
            output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            output = output_dir / f"{name}.png"
            prim = self.root if view == "whole" else self.hand_root
            eye, target = camera_pose(self.stage, prim, closeup=view == "hand")
            size = render_replicator_png(
                rep,
                output=output,
                temporary_root=args.run_dir,
                eye=eye,
                target=target,
                image_has_detail=image_has_detail,
            )
            return {
                "path": output.relative_to(args.run_dir).as_posix(),
                "bytes": size,
                "view": view,
                "eye": eye,
                "target": target,
                "resolution": [1280, 720],
                "method": "headless_replicator_render_product",
            }

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
