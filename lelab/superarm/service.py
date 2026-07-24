"""Session, safety, sequence, and event orchestration for SuperArm APIs."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import queue
import shutil
import stat
import threading
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from .actions import action_to_runtime_commands, normalize_superarm_action
from .control_guide import build_control_paths
from .isaac_distribution import IsaacDistribution, validate_and_extract_distribution
from .isaac_runtime import IsaacSimRuntime
from .mapping import ARM_JOINTS, UI_FINGERS
from .programs import ProgramStore
from .showroom import (
    align_amazinghand_attachment,
    align_joint5_urdf,
    amazinghand_visual_assets,
    build_amazinghand_visual_manifest,
    remove_amazinghand_visuals,
)
from .transports import MuJoCoRuntime, SerialAmazingHandTransport, SuperArmRuntime


class SuperArmService:
    def __init__(
        self,
        program_store: ProgramStore | None = None,
        *,
        mujoco_runtime_factory: Callable[..., SuperArmRuntime] = MuJoCoRuntime,
        isaac_runtime_factory: Callable[..., SuperArmRuntime] = IsaacSimRuntime,
        isaac_distribution_loader: Callable[..., IsaacDistribution] = validate_and_extract_distribution,
        live_timeout_s: float = 10.0,
        watchdog_interval_s: float = 0.1,
        watchdog_join_timeout_s: float = 1.0,
    ) -> None:
        self.programs = program_store or ProgramStore()
        self.runtime: SuperArmRuntime | None = None
        self.serial: SerialAmazingHandTransport | None = None
        self.mode: str | None = None
        self.emergency_stopped = False
        self._events: list[queue.Queue[dict[str, Any]]] = []
        self._lock = threading.RLock()
        self._control_lock = threading.RLock()
        self._last_live_command = 0.0
        self._live_enabled = False
        self._sequence_thread: threading.Thread | None = None
        self._sequence_stop = threading.Event()
        self._sequence_pause = threading.Event()
        self._mujoco_runtime_factory = mujoco_runtime_factory
        self._isaac_runtime_factory = isaac_runtime_factory
        self._isaac_distribution_loader = isaac_distribution_loader
        self._live_timeout_s = float(live_timeout_s)
        self._watchdog_interval_s = float(watchdog_interval_s)
        self._watchdog_join_timeout_s = float(watchdog_join_timeout_s)
        self._watchdog_stop: threading.Event | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_generation: int | None = None
        self._session_generation = 0
        self._closing = False
        self._latest_capture: dict[str, Any] | None = None
        self._latest_capture_fingerprint: tuple[str, int, int, int, int, str] | None = None

    def capabilities(self, workspace_root: str | Path | None = None) -> dict[str, Any]:
        errors: list[str] = []
        try:
            import mujoco

            mujoco_version = getattr(mujoco, "__version__", "unknown")
        except Exception as exc:
            mujoco_version = None
            errors.append(f"MuJoCo unavailable: {exc}")
        rustypot_available = importlib.util.find_spec("rustypot") is not None
        root = self.resolve_workspace(workspace_root)
        isaac_distribution = os.environ.get("SUPERARM_ISAAC_DISTRIBUTION_ZIP")
        isaac_path = Path(isaac_distribution).expanduser().resolve() if isaac_distribution else None
        isaac_validation_error: str | None = None
        isaac_archive_sha256: str | None = None
        isaac_entrypoint: str | None = None
        isaac_contract: dict[str, int] | None = None
        if isaac_path is not None and isaac_path.is_file():
            try:
                distribution = self._isaac_distribution_loader(isaac_path)
                isaac_archive_sha256 = distribution.archive_sha256
                isaac_entrypoint = str(distribution.entrypoint)
                isaac_contract = dict(distribution.robot_contract)
            except Exception as exc:
                isaac_validation_error = str(exc)
                errors.append(f"Isaac distribution invalid: {exc}")
        return {
            "runtimes": {
                "mujoco": {"enabled": mujoco_version == "3.10.0", "version": mujoco_version},
                "hybrid_serial": {
                    "enabled": mujoco_version == "3.10.0" and rustypot_available,
                    "serial_ports": SerialAmazingHandTransport.available_ports(),
                    "default_port": "/dev/ttyACM0",
                    "baudrate": 1_000_000,
                },
                "isaac_sim": {
                    "enabled": shutil.which("docker") is not None
                    and isaac_contract is not None
                    and isaac_validation_error is None,
                    "image": os.environ.get(
                        "ISAAC_SIM_IMAGE", "nvcr.io/nvidia/isaac-sim:6.0.0"
                    ),
                    "distribution_zip": str(isaac_path) if isaac_path else None,
                    "archive_sha256": isaac_archive_sha256,
                    "entrypoint": isaac_entrypoint,
                    "validation_error": isaac_validation_error,
                    "contract": isaac_contract,
                    "bridge_mode": "managed",
                    "physical_dof_count": 13,
                    "logical_action_width": 6,
                },
            },
            "workspace_root": str(root) if root else None,
            "model_source": "official AmazingHand MJCF closed-loop model",
            "arm_joints": ARM_JOINTS,
            "fingers": UI_FINGERS,
            "errors": errors,
        }

    def hardware_readiness(self) -> dict[str, Any]:
        """Return a read-only checklist for the real CAN-plus-serial robot.

        This endpoint intentionally reports prerequisites only.  Browser users
        cannot connect, calibrate, or torque-enable the DM4340P arm from it.
        """
        can_available = importlib.util.find_spec("can") is not None
        rustypot_available = importlib.util.find_spec("rustypot") is not None
        template = Path(__file__).with_name("data") / "superarm_dm4340p_amazinghand.example.yaml"
        return {
            "website_controls_physical_arm": False,
            "config_template": str(template),
            "arm": {
                "protocol": "LeRobot OpenArm/Damiao CAN or CAN-FD",
                "motor_type": "dm4340",
                "python_can_available": can_available,
                "requires": [
                    "five custom CAN ID pairs",
                    "measured direction/zero offsets",
                    "measured limits and MIT gains",
                ],
            },
            "hand": {
                "protocol": "AmazingHandControl Feetech SCS0009 serial",
                "rustypot_available": rustypot_available,
                "serial_ports": SerialAmazingHandTransport.available_ports(),
                "requires": ["IDs 1 through 8", "1,000,000 baud", "open/half/close feedback check"],
            },
            "steps": [
                "Install the superarm extra with python-can and rustypot.",
                "Discover the DM4340P CAN bus with torque disabled and replace every invalid template value.",
                "Validate AmazingHand SCS0009 IDs 1 through 8 separately before combined control.",
                "Run one torque-limited five-arm plus one-grasp pulse and verify readback.",
                "Record a LeRobot episode only after the isolated checks pass.",
            ],
        }

    def so101_leader_readiness(self) -> dict[str, Any]:
        """Expose the existing SO-101-to-SuperArm recording contract safely."""
        config_path = Path(__file__).with_name("data") / "superarm_mujoco.yaml"
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        mapping = list(raw.get("so101_leader_mapping") or [])
        return {
            "supported": len(mapping) == 5,
            "manual_page_is_physical_leader": False,
            "website_sequence_complete": False,
            "control_paths": build_control_paths(),
            "recording_input_mode": "so101",
            "leader": {
                "protocol": "LeRobot SO101Leader / Feetech serial",
                "serial_ports": SerialAmazingHandTransport.available_ports(),
                "requires": ["calibrated SO-101 leader", "leader serial port", "leader calibration ID"],
            },
            "follower": {
                "device_type": "SuperArm DM4340P + AmazingHand",
                "first_target": "MuJoCo SuperArm + AmazingHand",
                "hardware_config_template": "lelab/superarm/data/superarm_dm4340p_amazinghand.example.yaml",
                "arm_calibration": {
                    "required_before_hardware": True,
                    "joints": list(ARM_JOINTS),
                    "format": "[direction, zero_offset_rad]",
                    "requires": [
                        "measured direction (+1 or -1) for every joint",
                        "measured zero offset in SuperArm joint radians for every joint",
                        "measured lower and upper degree limits for every joint",
                        "five measured position_kp and five position_kd values",
                    ],
                },
            },
            "mapping": mapping,
            "gripper": {
                "source": raw.get("so101_gripper_feature"),
                "target": "amazinghand_motion.pos",
                "motions": raw.get("hand_motions"),
            },
            "steps": [
                "Connect and calibrate the SO-101 leader with LeLab's existing calibration page.",
                "Keep the follower in MuJoCo for the first dry run; do not enable real follower torque yet.",
                "On the dashboard, choose SuperArm + AmazingHand, then Record and select SO101 Leader.",
                "Enter the SO-101 serial port and calibration ID; LeLab maps five arm joints and quantizes the gripper to open, half-close, or close.",
                "Record one short dry-run episode and inspect all six logical actions before progressing to the real follower checklist.",
            ],
        }

    @staticmethod
    def resolve_workspace(workspace_root: str | Path | None = None) -> Path | None:
        candidates = []
        if workspace_root:
            candidates.append(Path(workspace_root))
        if os.environ.get("SUPERARM_ASSET_ROOT"):
            candidates.append(Path(os.environ["SUPERARM_ASSET_ROOT"]))
        candidates.extend(
            [
                Path.cwd(),
                Path.home() / ".cache" / "huggingface" / "lerobot" / "amazinghand" / "model",
            ]
        )
        for candidate in candidates:
            resolved = candidate.expanduser().resolve()
            if resolved.is_dir():
                return resolved
        return None

    def model_path(
        self,
        workspace_root: str | Path | None = None,
        model_path: str | Path | None = None,
    ) -> Path:
        root = self.resolve_workspace(workspace_root)
        configured = model_path or os.environ.get("SUPERARM_MUJOCO_MODEL_PATH")
        candidate = Path(configured).expanduser() if configured else None
        if candidate is not None and not candidate.is_absolute() and root is not None:
            candidate = root / candidate
        if candidate is None:
            candidate = Path.home() / ".cache/huggingface/lerobot/amazinghand/model/superarm_amazinghand.xml"
        candidate = candidate.resolve()
        if not candidate.is_file():
            raise FileNotFoundError("SuperArm MuJoCo model is missing; set SUPERARM_MUJOCO_MODEL_PATH")
        return candidate

    def _urdf_path(self, workspace_root: str | Path | None = None) -> Path:
        root = self.resolve_workspace(workspace_root)
        configured = os.environ.get("SUPERARM_URDF_PATH")
        candidate = Path(configured).expanduser() if configured else None
        if candidate is not None and not candidate.is_absolute() and root is not None:
            candidate = root / candidate
        if candidate is None or not candidate.resolve().is_file():
            raise FileNotFoundError("SuperArm showroom URDF is missing; set SUPERARM_URDF_PATH")
        return candidate.resolve()

    def source_arm_urdf_xml(
        self,
        workspace_root: str | Path | None = None,
        *,
        include_hand_visuals: bool = False,
    ) -> bytes:
        urdf_path = self._urdf_path(workspace_root)
        root = ET.parse(urdf_path).getroot()
        align_joint5_urdf(root)
        align_amazinghand_attachment(root)
        if not include_hand_visuals:
            remove_amazinghand_visuals(root)
        for mesh in root.findall(".//mesh"):
            filename = mesh.get("filename")
            if filename:
                mesh.set(
                    "filename",
                    f"/api/superarm/urdf/meshes/{quote(Path(filename).name)}",
                )
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    def source_arm_mesh_path(
        self,
        filename: str,
        workspace_root: str | Path | None = None,
    ) -> Path:
        if Path(filename).name != filename:
            raise ValueError("Invalid source-arm mesh filename")
        urdf_path = self._urdf_path(workspace_root)
        for mesh in ET.parse(urdf_path).getroot().findall(".//mesh"):
            source = mesh.get("filename")
            if source and Path(source).name == filename:
                path = Path(source)
                if not path.is_absolute():
                    path = urdf_path.parent / path
                if path.resolve().is_file():
                    return path.resolve()
        raise FileNotFoundError(f"Source-arm mesh is missing: {filename}")

    def amazinghand_visual_manifest(
        self,
        workspace_root: str | Path | None = None,
        model_path: str | Path | None = None,
        *,
        asset_url_prefix: str = "/api/superarm/mujoco-visual-assets",
    ) -> dict[str, Any]:
        resolved_model = self.model_path(workspace_root, model_path)
        return build_amazinghand_visual_manifest(
            resolved_model,
            asset_url_prefix=asset_url_prefix,
        )

    def amazinghand_visual_asset_path(
        self,
        mesh_name: str,
        workspace_root: str | Path | None = None,
        model_path: str | Path | None = None,
    ) -> Path:
        if not mesh_name or Path(mesh_name).name != mesh_name:
            raise ValueError("Invalid AmazingHand visual asset name")
        if mesh_name.endswith(".stl"):
            mesh_name = mesh_name.removesuffix(".stl")
        resolved_model = self.model_path(workspace_root, model_path)
        asset = amazinghand_visual_assets(resolved_model).get(mesh_name)
        if asset is None:
            raise FileNotFoundError(f"AmazingHand visual asset is missing: {mesh_name}")
        return asset

    def start_session(
        self,
        mode: str,
        *,
        serial_port: str = "/dev/ttyACM0",
        workspace_root: str | Path | None = None,
        model_path: str | Path | None = None,
        isaac_distribution_zip: str | Path | None = None,
        isaac_expected_sha256: str | None = None,
        isaac_bridge_mode: str = "managed",
        isaac_host: str = "127.0.0.1",
        isaac_port: int = 8765,
        isaac_external_run_dir: str | Path | None = None,
        isaac_arm_limits: dict[str, dict[str, float]] | None = None,
    ) -> dict[str, Any]:
        if mode not in {"mujoco", "hybrid_serial", "isaac_sim"}:
            raise ValueError("Runtime must be mujoco, hybrid_serial, or isaac_sim")
        with self._control_lock:
            if self.runtime is not None:
                if self.mode == mode and self.runtime.connected:
                    return self.status()
                raise RuntimeError(
                    f"A SuperArm {self.mode} runtime session is already active; "
                    f"disconnect it before starting {mode}"
                )
            generation = self._session_generation + 1

            def state_callback(state: dict[str, Any]) -> None:
                if generation == self._session_generation and not self._closing:
                    self.publish({"type": "state", **self.status(), "state": state})
            if mode == "isaac_sim":
                configured_zip = isaac_distribution_zip or os.environ.get(
                    "SUPERARM_ISAAC_DISTRIBUTION_ZIP"
                )
                if not configured_zip:
                    raise ValueError("isaac_sim requires a server-local distribution ZIP path")
                runtime = self._isaac_runtime_factory(
                    configured_zip,
                    bridge_mode=isaac_bridge_mode,
                    host=isaac_host,
                    port=isaac_port,
                    expected_sha256=isaac_expected_sha256,
                    external_run_dir=isaac_external_run_dir,
                    arm_limits=isaac_arm_limits,
                    state_callback=state_callback,
                )
            else:
                resolved_model_path = self.model_path(workspace_root, model_path)
                runtime = self._mujoco_runtime_factory(
                    resolved_model_path,
                    state_callback=state_callback,
                )
            serial = None
            try:
                runtime.connect()
                if mode == "hybrid_serial":
                    serial = SerialAmazingHandTransport(serial_port)
                    serial.connect()
                self.runtime = runtime
                self.serial = serial
                self.mode = mode
                self._session_generation = generation
                self._closing = False
                self._live_enabled = False
                self._latest_capture = None
                self._latest_capture_fingerprint = None
                self._start_watchdog()
            except Exception:
                with suppress(Exception):
                    runtime.close()
                if serial:
                    serial.close()
                self.runtime = None
                self.serial = None
                self.mode = None
                raise
        event = self.status()
        self.publish({"type": "runtime_status", **event})
        return event

    def disconnect(self) -> dict[str, Any]:
        self.stop_sequence()
        self._stop_watchdog()
        close_error: Exception | None = None
        with self._control_lock:
            self._closing = True
            self._session_generation += 1
            self._live_enabled = False
            try:
                if self.serial:
                    self.serial.close()
                if self.runtime:
                    self.runtime.close()
            except Exception as exc:
                close_error = exc
            finally:
                self.serial = None
                self.runtime = None
                self.mode = None
                self._latest_capture = None
                self._latest_capture_fingerprint = None
                self._closing = False
        event = self.status()
        self.publish({"type": "runtime_status", **event})
        if close_error is not None:
            raise close_error
        return event

    def status(self) -> dict[str, Any]:
        runtime_metadata = getattr(self.runtime, "metadata", None) if self.runtime else None
        return {
            "connected": bool(self.runtime and self.runtime.connected),
            "runtime": self.mode,
            "emergency_stopped": self.emergency_stopped,
            "live_enabled": self._live_enabled,
            "error": self.runtime.failure if self.runtime else None,
            "supports_video": bool(self.runtime and getattr(self.runtime, "supports_video", False)),
            "supports_capture": bool(
                self.runtime and getattr(self.runtime, "supports_capture", False)
            ),
            "runtime_metadata": (
                dict(runtime_metadata) if isinstance(runtime_metadata, Mapping) else None
            ),
        }

    def action(
        self,
        *,
        arm_rad: dict[str, float] | None = None,
        hand_deg: dict[str, list[float]] | None = None,
        hand_speed: dict[str, list[int]] | None = None,
        source: str = "staged",
    ) -> dict[str, Any]:
        return self._dispatch(
            arm_rad=arm_rad,
            hand_deg=hand_deg,
            hand_speed=hand_speed,
            source=source,
        )

    def _dispatch(
        self,
        *,
        arm_rad: dict[str, float] | None = None,
        hand_deg: dict[str, list[float]] | None = None,
        hand_speed: dict[str, list[int]] | None = None,
        logical: list[float] | None = None,
        source: str = "staged",
    ) -> dict[str, Any]:
        with self._control_lock:
            if not self.runtime or not self.runtime.connected or self._closing:
                raise RuntimeError("SuperArm runtime is disconnected")
            if self.emergency_stopped:
                raise RuntimeError("Commands are blocked by emergency stop")
            now = time.monotonic()
            if source == "live" and now - self._last_live_command < 0.05:
                raise RuntimeError("Live commands are capped at 20 Hz")
            if logical is not None:
                self.runtime.command_logical(logical)
                if self.serial:
                    _, serial_hand = action_to_runtime_commands(logical)
                    speeds = {finger: [3, 3] for finger in serial_hand}
                    self.serial.command(serial_hand, speeds)
            else:
                command_partial = getattr(self.runtime, "command_partial", None)
                if callable(command_partial):
                    command_partial(
                        arm_rad=arm_rad,
                        hand_deg=hand_deg,
                        hand_speed=hand_speed,
                    )
                else:  # Compatibility for injected legacy test/runtime adapters.
                    if arm_rad:
                        self.runtime.command(arm_rad)
                    if hand_deg:
                        self.runtime.command(hand_deg, hand_speed)
            if hand_deg and self.serial:
                speeds = hand_speed or {finger: [3, 3] for finger in hand_deg}
                self.serial.command(hand_deg, speeds)
            if source == "live":
                self._last_live_command = now
                self._live_enabled = True
            else:
                self._live_enabled = False
            result = {"accepted": True, **self.status()}
        self.publish({"type": "action", **result})
        return result

    def logical_action(
        self,
        values: list[float],
        *,
        arm_limits: dict[str, dict[str, float]] | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_superarm_action(values, arm_limits=arm_limits)
        result = self._dispatch(logical=normalized, source="staged")
        return {**result, "logical_action": normalized}

    def enforce_live_timeout(
        self,
        generation: int | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        timed_out = False
        with self._control_lock:
            if (
                (generation is None or generation == self._session_generation)
                and (stop_event is None or not stop_event.is_set())
                and not self._closing
                and self._live_enabled
                and time.monotonic() - self._last_live_command >= self._live_timeout_s
            ):
                self._live_enabled = False
                if self.runtime:
                    self.runtime.stop()
                timed_out = True
        if timed_out:
            self.publish({"type": "live_timeout", **self.status()})

    def _watchdog_worker(self, generation: int, stop_event: threading.Event) -> None:
        while not stop_event.wait(self._watchdog_interval_s):
            self.enforce_live_timeout(generation, stop_event)

    def _start_watchdog(self) -> None:
        if self._watchdog_thread:
            if self._watchdog_thread.is_alive():
                raise RuntimeError("previous SuperArm watchdog is still running")
            self._watchdog_thread = None
            self._watchdog_stop = None
            self._watchdog_generation = None
        stop_event = threading.Event()
        generation = self._session_generation
        self._watchdog_stop = stop_event
        self._watchdog_generation = generation
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_worker,
            args=(generation, stop_event),
            name="superarm-live-watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def _stop_watchdog(self) -> None:
        thread = self._watchdog_thread
        stop_event = self._watchdog_stop
        if stop_event is not None:
            stop_event.set()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=self._watchdog_join_timeout_s)
            if thread.is_alive():
                raise RuntimeError("SuperArm watchdog did not stop before disconnect")
        self._watchdog_thread = None
        self._watchdog_stop = None
        self._watchdog_generation = None

    def capture(self, view: str, name: str) -> dict[str, Any]:
        if self.mode != "isaac_sim" or self.runtime is None:
            raise RuntimeError("Isaac capture is available only for isaac_sim sessions")
        with self._control_lock:
            if self.mode != "isaac_sim" or self.runtime is None or self._closing:
                raise RuntimeError("Isaac capture is available only for isaac_sim sessions")
            if not getattr(self.runtime, "supports_capture", False):
                raise RuntimeError("Isaac runtime does not support live capture")
            capture = self.runtime.capture(view, name)
            _, _, fingerprint = self._read_capture_file(capture)
            self._latest_capture = dict(capture)
            self._latest_capture_fingerprint = fingerprint
            return dict(capture)

    def latest_capture(self) -> dict[str, Any]:
        with self._control_lock:
            if self.mode != "isaac_sim":
                raise RuntimeError("Isaac capture is available only for isaac_sim sessions")
            if self._latest_capture is None:
                raise RuntimeError("No Isaac capture has been created")
            return dict(self._latest_capture)

    @staticmethod
    def _read_capture_file(
        capture: dict[str, Any],
    ) -> tuple[Path, bytes, tuple[str, int, int, int, int, str]]:
        raw_path = capture.get("path")
        if not isinstance(raw_path, str):
            raise RuntimeError("Latest Isaac capture path is unavailable")
        try:
            path = Path(raw_path).resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("Latest Isaac capture file is unavailable") from exc
        if path.suffix.lower() != ".png" or not path.is_file() or Path(raw_path).is_symlink():
            raise RuntimeError("Latest Isaac capture file is invalid")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 32 * 1024 * 1024:
                raise RuntimeError("Latest Isaac capture file is invalid")
            content = stream.read(32 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())
        current = path.stat()
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in stable_fields) or any(
            getattr(after, field) != getattr(current, field) for field in stable_fields
        ):
            raise RuntimeError("Latest Isaac capture file changed while reading")
        if len(content) > 32 * 1024 * 1024 or not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Latest Isaac capture file is invalid")
        expected_bytes = capture.get("bytes")
        if expected_bytes is not None and len(content) != expected_bytes:
            raise RuntimeError("Latest Isaac capture file size changed")
        fingerprint = (
            str(path),
            int(after.st_dev),
            int(after.st_ino),
            int(after.st_size),
            int(after.st_mtime_ns),
            hashlib.sha256(content).hexdigest(),
        )
        return path, content, fingerprint

    def latest_capture_image(self) -> bytes:
        with self._control_lock:
            capture = self.latest_capture()
            expected = self._latest_capture_fingerprint
            if expected is None:
                raise RuntimeError("Latest Isaac capture integrity metadata is unavailable")
            _, content, current = self._read_capture_file(capture)
            if current != expected:
                raise RuntimeError("Latest Isaac capture file changed after validation")
            return content

    def emergency_stop(self, active: bool = True) -> dict[str, Any]:
        self._sequence_stop.set()
        self._sequence_pause.clear()
        with self._control_lock:
            if not active and self.mode == "hybrid_serial" and self.serial and not self.serial.connected:
                try:
                    self.serial.connect()
                except Exception:
                    self.emergency_stopped = True
                    raise
            self.emergency_stopped = active
            self._live_enabled = False
            if active:
                if self.serial:
                    self.serial.stop()
                if self.runtime:
                    self.runtime.stop()
            result = self.status()
        self.stop_sequence()
        self.publish({"type": "emergency_stop", **result})
        return result

    def telemetry(self) -> dict[str, Any]:
        self.enforce_live_timeout()
        with self._control_lock:
            state = self.runtime.observe() if self.runtime and not self._closing else {}
            if self.serial:
                state = dict(state)
                serial = self.serial.observe()
                mapped: dict[str, Any] = {}
                for finger_index, finger in enumerate(
                    ["pointer", "middle", "ring", "thumb"], start=1
                ):
                    for motor in (1, 2):
                        value = serial.get(f"{finger}_motor{motor}")
                        if value is not None:
                            mapped[f"finger{finger_index}_motor{motor}"] = value
                state["serial_hand"] = mapped
            return {"type": "state", **self.status(), "state": state}

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._events)
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except queue.Full:
                try:
                    subscriber.get_nowait()
                    subscriber.put_nowait(event)
                except queue.Empty:
                    pass

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=2)
        with self._lock:
            self._events.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if subscriber in self._events:
                self._events.remove(subscriber)

    def play_sequence(self, name: str, *, loop: bool = False) -> dict[str, Any]:
        if self._sequence_thread and self._sequence_thread.is_alive():
            raise RuntimeError("A sequence is already playing")
        sequence = self.programs.list_sequences().get(name)
        if sequence is None:
            raise KeyError(name)
        poses = self.programs.list_poses()
        generation = self._session_generation
        self._sequence_stop.clear()
        self._sequence_pause.clear()

        def sleep_interruptibly(seconds: float) -> bool:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if self._sequence_stop.wait(min(0.05, deadline - time.monotonic())):
                    return False
                while self._sequence_pause.is_set() and not self._sequence_stop.is_set():
                    time.sleep(0.05)
            return not self._sequence_stop.is_set()

        def worker() -> None:
            try:
                while not self._sequence_stop.is_set():
                    for index, step in enumerate(sequence["steps"]):
                        if (
                            self._sequence_stop.is_set()
                            or generation != self._session_generation
                        ):
                            return
                        self.publish({"type": "sequence_step", "sequence": name, "index": index})
                        if "sleep_s" in step:
                            if not sleep_interruptibly(float(step["sleep_s"])):
                                return
                            continue
                        pose = poses.get(step["pose"])
                        if pose is None:
                            raise RuntimeError(f"Sequence pose is missing: {step['pose']}")
                        speed_value = step.get("hand_speed", 3)
                        hand_speed = None
                        if pose.get("hand_deg"):
                            if isinstance(speed_value, list):
                                hand_speed = {
                                    finger: speed_value[offset : offset + 2]
                                    for offset, finger in zip(
                                        range(0, 8, 2),
                                        ["ring", "middle", "pointer", "thumb"],
                                        strict=True,
                                    )
                                    if finger in pose["hand_deg"]
                                }
                            else:
                                hand_speed = {
                                    finger: [int(speed_value), int(speed_value)]
                                    for finger in pose["hand_deg"]
                                }
                        # Omitted subsystems are deliberately not passed, so they hold.
                        self.action(
                            arm_rad=pose.get("arm_rad"),
                            hand_deg=pose.get("hand_deg"),
                            hand_speed=hand_speed,
                            source="sequence",
                        )
                        if not sleep_interruptibly(
                            float(step.get("transition_s", 0.0)) + float(step.get("hold_s", 0.0))
                        ):
                            return
                    if not loop:
                        return
            except Exception as exc:
                self.publish({"type": "sequence_error", "sequence": name, "error": str(exc)})
            finally:
                self.publish({"type": "sequence_stopped", "sequence": name})

        self._sequence_thread = threading.Thread(target=worker, name="superarm-sequence", daemon=True)
        self._sequence_thread.start()
        return {"playing": name, "loop": loop}

    def pause_sequence(self) -> dict[str, Any]:
        if not self._sequence_thread or not self._sequence_thread.is_alive():
            raise RuntimeError("No sequence is playing")
        if self._sequence_pause.is_set():
            self._sequence_pause.clear()
        else:
            self._sequence_pause.set()
        return {"paused": self._sequence_pause.is_set()}

    def stop_sequence(self) -> dict[str, Any]:
        self._sequence_stop.set()
        self._sequence_pause.clear()
        if self._sequence_thread and self._sequence_thread is not threading.current_thread():
            self._sequence_thread.join(timeout=1.0)
        self._sequence_thread = None
        return {"stopped": True}


service = SuperArmService()
