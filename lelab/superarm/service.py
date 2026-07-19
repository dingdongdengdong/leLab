"""Session, safety, sequence, and event orchestration for SuperArm APIs."""

from __future__ import annotations

import importlib.util
import os
import queue
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .mapping import ARM_JOINTS, UI_FINGERS
from .programs import ProgramStore
from .showroom import align_amazinghand_attachment
from .transports import MuJoCoRuntime, SerialAmazingHandTransport


class SuperArmService:
    def __init__(self, program_store: ProgramStore | None = None) -> None:
        self.programs = program_store or ProgramStore()
        self.runtime: MuJoCoRuntime | None = None
        self.serial: SerialAmazingHandTransport | None = None
        self.mode: str | None = None
        self.emergency_stopped = False
        self._events: list[queue.Queue[dict[str, Any]]] = []
        self._lock = threading.RLock()
        self._last_live_command = 0.0
        self._live_enabled = False
        self._sequence_thread: threading.Thread | None = None
        self._sequence_stop = threading.Event()
        self._sequence_pause = threading.Event()

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
        return {
            "runtimes": {
                "mujoco": {"enabled": mujoco_version == "3.10.0", "version": mujoco_version},
                "hybrid_serial": {
                    "enabled": mujoco_version == "3.10.0" and rustypot_available,
                    "serial_ports": SerialAmazingHandTransport.available_ports(),
                    "default_port": "/dev/ttyACM0",
                    "baudrate": 1_000_000,
                },
            },
            "workspace_root": str(root) if root else None,
            "model_source": "official AmazingHand MJCF closed-loop model",
            "arm_joints": ARM_JOINTS,
            "fingers": UI_FINGERS,
            "errors": errors,
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
            raise FileNotFoundError(
                "SuperArm MuJoCo model is missing; set SUPERARM_MUJOCO_MODEL_PATH"
            )
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
    ) -> bytes:
        urdf_path = self._urdf_path(workspace_root)
        root = ET.parse(urdf_path).getroot()
        align_amazinghand_attachment(root)
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

    def start_session(
        self,
        mode: str,
        *,
        serial_port: str = "/dev/ttyACM0",
        workspace_root: str | Path | None = None,
        model_path: str | Path | None = None,
    ) -> dict[str, Any]:
        if mode not in {"mujoco", "hybrid_serial"}:
            raise ValueError("Runtime must be mujoco or hybrid_serial")
        with self._lock:
            if self.runtime is not None:
                if self.mode == mode and self.runtime.connected:
                    return self.status()
                raise RuntimeError(
                    f"A SuperArm {self.mode} runtime session is already active; "
                    f"disconnect it before starting {mode}"
                )
            resolved_model_path = self.model_path(workspace_root, model_path)
            runtime = MuJoCoRuntime(
                resolved_model_path,
                state_callback=lambda state: self.publish(
                    {"type": "state", **self.status(), "state": state}
                ),
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
                self._live_enabled = False
            except Exception:
                runtime.close()
                if serial:
                    serial.close()
                raise
        event = self.status()
        self.publish({"type": "runtime_status", **event})
        return event

    def disconnect(self) -> dict[str, Any]:
        self.stop_sequence()
        with self._lock:
            self._live_enabled = False
            if self.serial:
                self.serial.close()
            if self.runtime:
                self.runtime.close()
            self.serial = None
            self.runtime = None
            self.mode = None
        event = self.status()
        self.publish({"type": "runtime_status", **event})
        return event

    def status(self) -> dict[str, Any]:
        return {
            "connected": bool(self.runtime and self.runtime.connected),
            "runtime": self.mode,
            "emergency_stopped": self.emergency_stopped,
            "live_enabled": self._live_enabled,
            "error": self.runtime.failure if self.runtime else None,
        }

    def action(
        self,
        *,
        arm_rad: dict[str, float] | None = None,
        hand_deg: dict[str, list[float]] | None = None,
        hand_speed: dict[str, list[int]] | None = None,
        source: str = "staged",
    ) -> dict[str, Any]:
        if not self.runtime or not self.runtime.connected:
            raise RuntimeError("SuperArm runtime is disconnected")
        if self.emergency_stopped:
            raise RuntimeError("Commands are blocked by emergency stop")
        now = time.monotonic()
        if source == "live":
            if now - self._last_live_command < 0.05:
                raise RuntimeError("Live commands are capped at 20 Hz")
            self._last_live_command = now
            self._live_enabled = True
        else:
            self._live_enabled = False
        if arm_rad:
            self.runtime.command(arm_rad)
        if hand_deg:
            speeds = hand_speed or {finger: [3, 3] for finger in hand_deg}
            if self.serial:
                self.serial.command(hand_deg, speeds)
            self.runtime.command(hand_deg, speeds)
        result = {"accepted": True, **self.status()}
        self.publish({"type": "action", **result})
        return result

    def enforce_live_timeout(self) -> None:
        if self._live_enabled and time.monotonic() - self._last_live_command >= 10.0:
            self._live_enabled = False
            if self.runtime:
                self.runtime.stop()
            self.publish({"type": "live_timeout", **self.status()})

    def emergency_stop(self, active: bool = True) -> dict[str, Any]:
        if not active and self.mode == "hybrid_serial" and self.serial and not self.serial.connected:
            try:
                self.serial.connect()
            except Exception:
                self.emergency_stopped = True
                raise
        self.emergency_stopped = active
        self._live_enabled = False
        self.stop_sequence()
        if active:
            if self.serial:
                self.serial.stop()
            if self.runtime:
                self.runtime.stop()
        result = self.status()
        self.publish({"type": "emergency_stop", **result})
        return result

    def telemetry(self) -> dict[str, Any]:
        self.enforce_live_timeout()
        state = self.runtime.observe() if self.runtime else {}
        if self.serial:
            state = dict(state)
            serial = self.serial.observe()
            mapped: dict[str, Any] = {}
            for finger_index, finger in enumerate(["pointer", "middle", "ring", "thumb"], start=1):
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
                        if self._sequence_stop.is_set():
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
