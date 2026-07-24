"""Long-lived localhost control bridge for the validated SuperArm Isaac USD."""

from __future__ import annotations

import argparse
import json
import selectors
import socket
import sys
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

TABLE_HEIGHT = 0.10
TABLE_TOP_Z = 0.10
CUBE_SIZE = 0.04
RL_FRAME_NAME = "rl-workspace.ppm"
PASSIVE_VISUAL_PROFILE = "superarm_isaac60_passive_linkage_no_shell/v1"


class BridgeRuntime(Protocol):
    def hello(self) -> dict[str, Any]: ...

    def step(self) -> None: ...

    def command(self, targets: Mapping[str, float]) -> dict[str, Any]: ...

    def observe(self) -> dict[str, Any]: ...

    def hold(self) -> dict[str, Any]: ...

    def capture(self, view: str, name: str) -> dict[str, Any]: ...

    def rl_reset(self, seed: int, max_steps: int) -> dict[str, Any]: ...

    def rl_step(self, arm_targets: Mapping[str, float], grasp: float) -> dict[str, Any]: ...

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
    parser.add_argument("--replicator-rgb", action="store_true")
    parser.add_argument("--passive-linkage-visuals", action="store_true")
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
    if args.webrtc and args.replicator_rgb:
        raise ValueError("WebRTC and RL Replicator RGB require separate Isaac processes")
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
    if args.passive_linkage_visuals:
        args.passive_python_root = _resolved_child(
            args.asset_root,
            args.asset_root / "python",
            label="passive runtime Python root",
        )
        args.passive_instances = _resolved_child(
            args.asset_root,
            (
                args.asset_root
                / "usd"
                / "superarm_amazinghand"
                / "zip_hand_payloads"
                / "instances.usda"
            ),
            label="passive linkage instances",
        )
        required = (
            args.passive_python_root / "superarm_isaac_runtime" / "__init__.py",
            args.passive_python_root / "superarm_isaac_runtime" / "passive_linkage.py",
            args.passive_python_root / "superarm_isaac_runtime" / "passive_linkage_usd.py",
            (
                args.passive_python_root
                / "superarm_isaac_runtime"
                / "data"
                / "amazinghand_passive_linkage_keyframes.json"
            ),
        )
        if not args.passive_python_root.is_dir() or not all(path.is_file() for path in required):
            raise ValueError("passive linkage runtime package is incomplete")
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
    if op == "rl_reset":
        return runtime.rl_reset(request["seed"], request["max_steps"]), False
    if op == "rl_step":
        return runtime.rl_step(request["arm_targets"], request["grasp"]), False
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
                            raise ProtocolError("invalid_request", "hello must be the only handshake frame")
                        decoded = decode_frame(raw + b"\n")
                        if isinstance(decoded.get("request_id"), str) and decoded["request_id"]:
                            request_id = decoded["request_id"][:128]
                        request = validate_request(decoded, expected_token=token)
                        if request["op"] != "hello":
                            raise ProtocolError("invalid_request", "first bridge operation must be hello")
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

    if getattr(args, "replicator_rgb", False):
        return {
            "headless": False,
            "renderer": "RayTracedLighting",
            "enable_cameras": True,
            "extra_args": ["--/exts/isaacsim.core.throttling/enable_async=false"],
        }, ""

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
    from isaacsim.core.api.objects import DynamicCuboid, FixedCuboid
    from isaacsim.core.experimental.prims import Articulation
    from isaacsim.core.rendering_manager import ViewportManager
    from isaacsim.core.simulation_manager import SimulationManager
    from pxr import Gf, Usd, UsdGeom, UsdLux, UsdPhysics

    rep = None
    if args.replicator_rgb:
        import omni.replicator.core as rep

    solve_passive_linkage = None
    author_or_update_passive_linkage_runtime = None
    if args.passive_linkage_visuals:
        sys.path.insert(0, str(args.passive_python_root))
        from superarm_isaac_runtime import passive_linkage, passive_linkage_usd

        package_root = args.passive_python_root.resolve()
        for module in (passive_linkage, passive_linkage_usd):
            module_path = Path(module.__file__).resolve()
            if not module_path.is_relative_to(package_root):
                raise RuntimeError(f"passive runtime imported outside distribution: {module_path}")
        solve_passive_linkage = passive_linkage.solve_passive_linkage
        author_or_update_passive_linkage_runtime = (
            passive_linkage_usd.author_or_update_passive_linkage_runtime
        )

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

    def world_translation(prim: Usd.Prim) -> np.ndarray:
        matrix = UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        return np.asarray(matrix.ExtractTranslation(), dtype=np.float64)

    def write_ppm_atomic(rgb: np.ndarray, destination: Path) -> None:
        frame = np.asarray(rgb)
        if frame.shape[-1] == 4:
            frame = frame[..., :3]
        if frame.shape != (256, 256, 3):
            raise RuntimeError(f"workspace camera returned unexpected shape {frame.shape}")
        frame = frame.astype(np.uint8, copy=False)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        with temporary.open("wb") as handle:
            handle.write(b"P6\n256 256\n255\n")
            handle.write(frame.tobytes(order="C"))
        temporary.replace(destination)

    class IsaacRuntime:
        def __init__(self) -> None:
            print(json.dumps({"event": "rl_runtime_initializing"}), flush=True)
            if not omni.usd.get_context().open_stage(str(args.entrypoint)):
                raise RuntimeError(f"Isaac could not open USD: {args.entrypoint}")
            print(json.dumps({"event": "rl_stage_opened"}), flush=True)
            for _ in range(8):
                app.update()
            self.stage = omni.usd.get_context().get_stage()
            self.root = discover_root(self.stage)
            self.root_path = str(self.root.GetPath())
            self.world = World(stage_units_in_meters=1.0, physics_dt=1.0 / 120.0, rendering_dt=1.0 / 30.0)
            self.timeline = omni.timeline.get_timeline_interface()
            self.timeline.play()
            self.art = Articulation(self.root_path)
            print(json.dumps({"event": "rl_overlay_creating"}), flush=True)
            self._create_rl_overlay()
            print(json.dumps({"event": "rl_overlay_created"}), flush=True)
            self.world.reset()
            print(json.dumps({"event": "rl_world_reset"}), flush=True)
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
                bounds = (
                    UsdGeom.BBoxCache(
                        Usd.TimeCode.Default(),
                        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
                        useExtentsHint=False,
                    )
                    .ComputeWorldBound(self.root)
                    .ComputeAlignedRange()
                )
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
            print(json.dumps({"event": "rl_post_warmup"}), flush=True)
            if not self.art.is_physics_tensor_entity_valid():
                raise RuntimeError("Isaac physics tensor did not initialize the articulation")
            print(json.dumps({"event": "rl_articulation_valid"}), flush=True)
            actual = set(self.art.dof_names)
            expected = set(PHYSICAL_JOINTS)
            if self.art.num_dofs != 13 or actual != expected:
                raise RuntimeError(
                    "Isaac articulation contract mismatch: "
                    f"count={self.art.num_dofs}, missing={sorted(expected - actual)}, "
                    f"extra={sorted(actual - expected)}"
                )
            self.indices = {name: int(self.art.dof_names.index(name)) for name in PHYSICAL_JOINTS}
            positions = flat(self.art.get_dof_positions())
            self.targets = {name: positions[self.indices[name]] for name in PHYSICAL_JOINTS}
            self.physics_step = 2
            self.command_sequence = 0
            self.passive_visual_contract: dict[str, Any] | None = None
            self._last_passive_positions: tuple[float, ...] | None = None
            self._update_passive_linkage(force=True)
            print(json.dumps({"event": "rl_camera_initializing"}), flush=True)
            self._initialize_rl_runtime()
            print(json.dumps({"event": "rl_runtime_ready"}), flush=True)

        def _create_rl_overlay(self) -> None:
            """Author task-only scene prims without mutating the distribution."""

            self.rl_root = "/LeLabRL"
            UsdGeom.Xform.Define(self.stage, self.rl_root)
            self.table = FixedCuboid(
                prim_path=f"{self.rl_root}/Table",
                name="lelab_rl_table",
                position=np.asarray([0.10, 0.32, TABLE_HEIGHT / 2], dtype=np.float32),
                scale=np.asarray([0.60, 0.60, TABLE_HEIGHT], dtype=np.float32),
                color=np.asarray([0.18, 0.20, 0.24], dtype=np.float32),
            )
            self.cube = DynamicCuboid(
                prim_path=f"{self.rl_root}/Cube",
                name="lelab_rl_cube",
                position=np.asarray([0.10, 0.32, TABLE_TOP_Z + CUBE_SIZE / 2], dtype=np.float32),
                scale=np.asarray([CUBE_SIZE] * 3, dtype=np.float32),
                color=np.asarray([0.95, 0.72, 0.05], dtype=np.float32),
                mass=0.04,
            )
            UsdLux.DomeLight.Define(self.stage, f"{self.rl_root}/DomeLight").CreateIntensityAttr(700.0)
            camera = UsdGeom.Camera.Define(self.stage, f"{self.rl_root}/WorkspaceCamera")
            camera.GetFocalLengthAttr().Set(28.0)
            camera_xform = UsdGeom.Xformable(camera)
            camera_xform.AddTransformOp().Set(
                Gf.Matrix4d()
                .SetLookAt(
                    Gf.Vec3d(0.95, -0.65, 0.90),
                    Gf.Vec3d(0.04, 0.30, 0.32),
                    Gf.Vec3d(0.0, 0.0, 1.0),
                )
                .GetInverse()
            )
            self.workspace_camera_path = str(camera.GetPath())

            # Additive collision shapes attach to existing rigid palm/finger
            # bodies. The immutable source USD remains unchanged on disk.
            candidates = [
                prim
                for prim in self.stage.Traverse()
                if prim.HasAPI(UsdPhysics.RigidBodyAPI)
                and any(token in prim.GetName().lower() for token in ("palm", "finger"))
            ]
            selected: list[Usd.Prim] = []
            for token in ("palm", "finger1", "finger2", "finger3", "finger4"):
                match = next((prim for prim in candidates if token in prim.GetName().lower()), None)
                if match is not None and match not in selected:
                    selected.append(match)
            if len(selected) < 5:
                raise RuntimeError("RL overlay could not bind explicit palm/finger collision proxies")
            self.rl_contact_proxy_paths = []
            for index, body in enumerate(selected[:5]):
                proxy_path = body.GetPath().AppendChild(f"LeLabRLContactProxy{index}")
                proxy = UsdGeom.Cube.Define(self.stage, proxy_path)
                proxy.CreateSizeAttr(0.018 if index else 0.035)
                UsdPhysics.CollisionAPI.Apply(proxy.GetPrim())
                self.rl_contact_proxy_paths.append(str(proxy_path))

        def _initialize_rl_runtime(self) -> None:
            self._workspace_camera = None
            self._workspace_render_product = None
            self._workspace_rgb = None
            if args.replicator_rgb:
                timeline_was_playing = self.timeline.is_playing()
                if timeline_was_playing:
                    self.timeline.stop()
                print(json.dumps({"event": "rl_replicator_configuring"}), flush=True)
                rep.orchestrator.set_capture_on_play(False)
                print(json.dumps({"event": "rl_render_product_creating"}), flush=True)
                self._workspace_render_product = rep.create.render_product(
                    self.workspace_camera_path,
                    (256, 256),
                )
                print(json.dumps({"event": "rl_render_product_created"}), flush=True)
                self._workspace_rgb = rep.AnnotatorRegistry.get_annotator("rgb")
                self._workspace_rgb.attach(self._workspace_render_product)
                print(json.dumps({"event": "rl_rgb_annotator_attached"}), flush=True)
                if timeline_was_playing:
                    self.timeline.play()
            else:
                from isaacsim.sensors.camera import Camera

                self._workspace_camera = Camera(
                    prim_path=self.workspace_camera_path,
                    resolution=(256, 256),
                )
                self._workspace_camera.initialize()
            self._frame_path = args.run_dir / RL_FRAME_NAME
            self._frame_sequence = 0
            self._rl_step_count = 0
            self._success_streak = 0
            self._previous_cube_height = TABLE_TOP_Z + CUBE_SIZE / 2
            self._previous_grasp = 0.0
            self._home_positions = np.asarray(
                [self.targets[name] for name in PHYSICAL_JOINTS], dtype=np.float32
            )
            self._ee_prim = next(
                (
                    prim
                    for prim in self.stage.Traverse()
                    if prim.HasAPI(UsdPhysics.RigidBodyAPI)
                    and any(token in prim.GetName().lower() for token in ("palm", "wrist"))
                ),
                None,
            )
            if self._ee_prim is None:
                raise RuntimeError("RL overlay could not resolve an end-effector rigid body")

        def _update_passive_linkage(self, *, force: bool = False) -> None:
            if not args.passive_linkage_visuals:
                return
            positions = flat(self.art.get_dof_positions())
            measured_values = tuple(positions[self.indices[name]] for name in HAND_JOINTS)
            if (
                not force
                and self._last_passive_positions is not None
                and max(
                    abs(current - previous)
                    for current, previous in zip(
                        measured_values,
                        self._last_passive_positions,
                        strict=True,
                    )
                )
                < 1e-5
            ):
                return
            measured = dict(zip(HAND_JOINTS, measured_values, strict=True))
            poses = solve_passive_linkage(measured)
            self.passive_visual_contract = author_or_update_passive_linkage_runtime(
                self.stage,
                self.root_path,
                poses,
                args.passive_instances,
            )
            self._last_passive_positions = measured_values

        def _capture_rl_frame(self) -> dict[str, Any]:
            if args.replicator_rgb:
                print(json.dumps({"event": "rl_rgb_capture_start"}), flush=True)
                saved_positions = np.asarray(flat(self.art.get_dof_positions()), dtype=np.float32)
                saved_velocities = np.asarray(flat(self.art.get_dof_velocities()), dtype=np.float32)
                saved_targets = np.asarray(
                    [self.targets[name] for name in PHYSICAL_JOINTS],
                    dtype=np.float32,
                )
                cube_position, cube_orientation = self.cube.get_world_pose()
                cube_linear_velocity = np.asarray(
                    self.cube.get_linear_velocity(),
                    dtype=np.float32,
                )
                cube_angular_velocity = np.asarray(
                    self.cube.get_angular_velocity(),
                    dtype=np.float32,
                )
                timeline_was_playing = self.timeline.is_playing()
                if timeline_was_playing:
                    self.timeline.stop()
                try:
                    print(json.dumps({"event": "rl_rgb_orchestrator_step"}), flush=True)
                    rep.orchestrator.step(delta_time=0.0, rt_subframes=8, pause_timeline=False)
                    print(json.dumps({"event": "rl_rgb_orchestrator_complete"}), flush=True)
                    frame = np.asarray(self._workspace_rgb.get_data())
                    print(
                        json.dumps({"event": "rl_rgb_data_ready", "shape": list(frame.shape)}),
                        flush=True,
                    )
                finally:
                    if timeline_was_playing:
                        self.timeline.play()
                        SimulationManager.invalidate_physics()
                        SimulationManager.initialize_physics()
                        self.art = Articulation(self.root_path)
                        if not self.art.is_physics_tensor_entity_valid():
                            raise RuntimeError("Isaac physics tensor did not recover after RGB capture")
                        self.art.set_dof_positions(saved_positions)
                        self.art.set_dof_velocities(saved_velocities)
                        self.art.set_dof_position_targets(saved_targets)
                        self.cube.set_world_pose(
                            position=np.asarray(cube_position, dtype=np.float32),
                            orientation=np.asarray(cube_orientation, dtype=np.float32),
                        )
                        self.cube.set_linear_velocity(cube_linear_velocity)
                        self.cube.set_angular_velocity(cube_angular_velocity)
            else:
                frame = None
                for _ in range(12):
                    self.world.step(render=True)
                    app.update()
                    self.physics_step += 1
                    frame = self._workspace_camera.get_rgba()
                    if frame is not None and np.asarray(frame).shape[:2] == (256, 256):
                        break
            if frame is None:
                raise RuntimeError("workspace camera did not produce an RGB frame")
            if hasattr(frame, "numpy"):
                frame = frame.numpy()
            write_ppm_atomic(frame, self._frame_path)
            self._frame_sequence += 1
            return {
                "path": RL_FRAME_NAME,
                "width": 256,
                "height": 256,
                "channels": 3,
                "sequence": self._frame_sequence,
            }

        def _rl_state(self) -> dict[str, Any]:
            positions = flat(self.art.get_dof_positions())
            velocities = flat(self.art.get_dof_velocities())
            arm_positions = [positions[self.indices[name]] for name in ARM_JOINTS]
            arm_velocities = [velocities[self.indices[name]] for name in ARM_JOINTS]
            ee_xyz = world_translation(self._ee_prim)
            cube_xyz = np.asarray(self.cube.get_world_pose()[0], dtype=np.float64)
            cube_velocity = np.asarray(self.cube.get_linear_velocity(), dtype=np.float64)
            return {
                "joint_positions": arm_positions,
                "joint_velocities": arm_velocities,
                "grasp_state": self._previous_grasp,
                "end_effector_xyz": ee_xyz.tolist(),
                "cube_xyz": cube_xyz.tolist(),
                "cube_linear_velocity_xyz": cube_velocity.tolist(),
                "end_effector_to_cube_xyz": (cube_xyz - ee_xyz).tolist(),
            }

        def rl_reset(self, seed: int, max_steps: int) -> dict[str, Any]:
            print(json.dumps({"event": "rl_reset_start", "seed": seed}), flush=True)
            rng = np.random.default_rng(seed)
            cube_xy = np.asarray([0.10, 0.32]) + rng.uniform(-0.035, 0.035, size=2)
            self.art.set_dof_positions(self._home_positions)
            self.art.set_dof_velocities(np.zeros_like(self._home_positions))
            self.art.set_dof_position_targets(self._home_positions)
            self.targets = dict(zip(PHYSICAL_JOINTS, self._home_positions.tolist(), strict=True))
            self.cube.set_world_pose(
                position=np.asarray([cube_xy[0], cube_xy[1], TABLE_TOP_Z + CUBE_SIZE / 2])
            )
            self.cube.set_linear_velocity(np.zeros(3, dtype=np.float32))
            self.cube.set_angular_velocity(np.zeros(3, dtype=np.float32))
            self._rl_step_count = 0
            self._rl_max_steps = int(max_steps)
            self._success_streak = 0
            self._previous_cube_height = TABLE_TOP_Z + CUBE_SIZE / 2
            self._previous_grasp = 0.0
            for _ in range(24):
                self.world.step(render=False)
                self.physics_step += 1
            self._update_passive_linkage(force=True)
            print(json.dumps({"event": "rl_reset_settled"}), flush=True)
            return {
                "state": self._rl_state(),
                "frame": self._capture_rl_frame(),
                "info": {"seed": seed, "is_intervention": False},
            }

        def rl_step(self, arm_targets: Mapping[str, float], grasp: float) -> dict[str, Any]:
            from .contracts import grasp_to_urdf_targets

            previous_arm = np.asarray([self.targets[name] for name in ARM_JOINTS])
            physical_targets = validate_physical_targets(
                {**dict(arm_targets), **grasp_to_urdf_targets(grasp)}
            )
            self.command(physical_targets)
            for _ in range(12):
                self.world.step(render=False)
                self.physics_step += 1
            self._update_passive_linkage(force=True)
            grasp_changed = grasp != self._previous_grasp
            self._previous_grasp = float(grasp)
            state = self._rl_state()
            cube_xyz = np.asarray(state["cube_xyz"], dtype=np.float64)
            ee_delta = np.asarray(state["end_effector_to_cube_xyz"], dtype=np.float64)
            lifted = cube_xyz[2] >= TABLE_TOP_Z + 0.10
            self._success_streak = self._success_streak + 1 if lifted else 0
            success = self._success_streak >= 10
            out_of_bounds = bool(
                cube_xyz[2] < TABLE_TOP_Z - 0.05
                or abs(cube_xyz[0] - 0.10) > 0.35
                or abs(cube_xyz[1] - 0.32) > 0.35
            )
            normalized_action = (np.asarray([arm_targets[name] for name in ARM_JOINTS]) - previous_arm) / 0.04
            lift_progress = min(
                2.0,
                20.0 * max(0.0, cube_xyz[2] - max(self._previous_cube_height, TABLE_TOP_Z)),
            )
            terms = {
                "distance": -2.0 * float(np.linalg.norm(ee_delta)),
                "lift_progress": lift_progress,
                "success": 10.0 if success else 0.0,
                "action": -0.01 * float(np.square(normalized_action).sum()),
                "grasp_change": -0.02 if grasp_changed else 0.0,
            }
            self._rl_step_count += 1
            self._previous_cube_height = float(cube_xyz[2])
            return {
                "state": state,
                "frame": self._capture_rl_frame(),
                "reward": float(sum(terms.values())),
                "terminated": bool(success or out_of_bounds),
                "truncated": bool(self._rl_step_count >= self._rl_max_steps),
                "info": {
                    "is_intervention": False,
                    "success": success,
                    "failure": out_of_bounds,
                    "success_streak": self._success_streak,
                    "reward_terms": terms,
                    "contact_proxy_paths": self.rl_contact_proxy_paths,
                },
            }

        def hello(self) -> dict[str, Any]:
            payload = {
                "runtime": "isaac_sim",
                "isaac_sim_version": "6.0.1" if args.replicator_rgb else "6.0.0",
                "articulation_root": self.root_path,
                "articulation_root_count": 1,
                "physical_dof_count": 13,
                "logical_action_width": 6,
                "joint_names": list(PHYSICAL_JOINTS),
                "visual_profile": (
                    PASSIVE_VISUAL_PROFILE if args.passive_linkage_visuals else None
                ),
                "passive_follower_count": (
                    self.passive_visual_contract.get("visual_part_count")
                    if self.passive_visual_contract is not None
                    else 0
                ),
                "outer_shells_included": False,
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
            self._update_passive_linkage()

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
            if self._workspace_rgb is not None and self._workspace_render_product is not None:
                with suppress(Exception):
                    self._workspace_rgb.detach(self._workspace_render_product)
                with suppress(Exception):
                    self._workspace_render_product.destroy()
                with suppress(Exception):
                    rep.orchestrator.wait_until_complete()
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
