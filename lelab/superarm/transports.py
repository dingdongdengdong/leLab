"""Runtime-neutral arm/hand transports and concrete MuJoCo/serial adapters."""

from __future__ import annotations

import glob
import io
import math
import os
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from PIL import Image

from .mapping import (
    ARM_JOINTS,
    ARM_MAX_RAD,
    ARM_MIN_RAD,
    HAND_ACTUATORS,
    SERVO_IDS,
    UI_FINGERS,
    degrees_to_hardware_radians,
    hardware_radians_to_degrees,
    named_hand_to_mujoco,
)
from .showroom import aligned_mujoco_model_path, amazinghand_body_ids, amazinghand_visual_pose


def configure_superarm_camera(model: Any, data: Any, camera: Any) -> None:
    """Frame the complete arm-hand assembly instead of a misleading wrist crop."""
    import mujoco

    camera.type = mujoco.mjtCamera.mjCAMERA_FREE
    camera.lookat[:] = model.stat.center
    camera.distance = max(1.05, float(model.stat.extent) * 1.2)
    camera.azimuth = 135
    camera.elevation = -15


class ArmTransport(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def command(self, arm_rad: dict[str, float]) -> None: ...

    @abstractmethod
    def observe(self) -> dict[str, Any]: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class HandTransport(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def command(
        self,
        hand_deg: dict[str, list[float]],
        hand_speed: dict[str, list[int]],
    ) -> None: ...

    @abstractmethod
    def observe(self) -> dict[str, Any]: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class MuJoCoRuntime(ArmTransport, HandTransport):
    """Physics worker at model timestep with nonblocking 15 FPS EGL rendering."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        state_callback: Callable[[dict[str, Any]], None] | None = None,
        render_fps: float = 15.0,
    ) -> None:
        self.model_path = Path(model_path)
        self.state_callback = state_callback
        self.render_fps = render_fps
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = False
        self._failure: str | None = None
        self._model: Any = None
        self._data: Any = None
        self._actuator_ids: dict[str, int] = {}
        self._joint_qpos: dict[str, int] = {}
        self._hand_body_ids: list[int] = []
        self._targets = dict.fromkeys(ARM_JOINTS, 0.0)
        self._targets.update({name: (0.05 if name.endswith("motor1") else -0.02) for name in HAND_ACTUATORS})
        self._latest_frame: bytes | None = None
        self._latest_state: dict[str, Any] = {}
        self._frame_sequence = 0
        self._visual_sequence = 0

    @property
    def connected(self) -> bool:
        return self._connected and self._failure is None

    @property
    def failure(self) -> str | None:
        return self._failure

    def connect(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if not self.model_path.is_file():
            raise FileNotFoundError(f"MuJoCo model is missing: {self.model_path}")
        self._stop.clear()
        ready = threading.Event()

        def target() -> None:
            self._run(ready)

        self._thread = threading.Thread(target=target, name="superarm-mujoco", daemon=True)
        self._thread.start()
        if not ready.wait(15.0):
            self._stop.set()
            raise RuntimeError("MuJoCo worker did not initialize within 15 seconds")
        if self._failure:
            raise RuntimeError(self._failure)

    def _run(self, ready: threading.Event) -> None:
        renderer = None
        try:
            os.environ.setdefault("MUJOCO_GL", "egl")
            import mujoco

            with aligned_mujoco_model_path(self.model_path) as runtime_model_path:
                self._model = mujoco.MjModel.from_xml_path(str(runtime_model_path))
            self._data = mujoco.MjData(self._model)
            self._hand_body_ids = amazinghand_body_ids(self._model)
            self._actuator_ids = {
                name: mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_ACTUATOR, name)
                for name in [*ARM_JOINTS, *HAND_ACTUATORS]
            }
            self._joint_qpos = {}
            for name in [*ARM_JOINTS, *HAND_ACTUATORS]:
                joint_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
                self._joint_qpos[name] = int(self._model.jnt_qposadr[joint_id])
            renderer = mujoco.Renderer(self._model, height=480, width=640)
            camera = mujoco.MjvCamera()
            mujoco.mj_forward(self._model, self._data)
            configure_superarm_camera(self._model, self._data, camera)
            self._connected = True
            ready.set()
            next_step = time.monotonic()
            next_render = next_step
            next_state = next_step
            while not self._stop.is_set():
                now = time.monotonic()
                with self._lock:
                    for name, actuator_id in self._actuator_ids.items():
                        self._data.ctrl[actuator_id] = self._targets[name]
                    mujoco.mj_step(self._model, self._data)
                if now >= next_state:
                    self._capture_state(now)
                    next_state = now + 0.05
                if now >= next_render:
                    # Rendering replaces the previous frame. Consumers always get
                    # the freshest image, so a slow client never blocks physics.
                    with self._lock:
                        renderer.update_scene(self._data, camera=camera)
                        rgb = renderer.render().copy()
                    buffer = io.BytesIO()
                    Image.fromarray(rgb).save(buffer, format="JPEG", quality=82)
                    with self._lock:
                        self._latest_frame = buffer.getvalue()
                        self._frame_sequence += 1
                    next_render = now + 1.0 / self.render_fps
                next_step += float(self._model.opt.timestep)
                sleep_s = next_step - time.monotonic()
                if sleep_s > 0:
                    self._stop.wait(sleep_s)
                elif sleep_s < -0.1:
                    next_step = time.monotonic()
        except Exception as exc:
            self._failure = f"MuJoCo runtime failed: {exc}"
            ready.set()
        finally:
            self._connected = False
            if renderer is not None:
                renderer.close()

    def _capture_state(self, timestamp: float) -> None:
        with self._lock:
            positions = {name: float(self._data.qpos[address]) for name, address in self._joint_qpos.items()}
            target = dict(self._targets)
            visual_bodies = amazinghand_visual_pose(
                self._model,
                self._data,
                self._hand_body_ids,
            )
            self._visual_sequence += 1
            visual_sequence = self._visual_sequence
        state = {
            "timestamp": timestamp,
            "runtime": "mujoco",
            "connected": self.connected,
            "arm": {
                name: {
                    "position": positions[name],
                    "target": target[name],
                    "moving": abs(positions[name] - target[name]) > 0.01,
                }
                for name in ARM_JOINTS
            },
            "hand": {
                name: {
                    "position": positions[name],
                    "target": target[name],
                    "moving": abs(positions[name] - target[name]) > 0.01,
                    "goal": None,
                    "speed": None,
                    "load": None,
                    "voltage": None,
                    "temperature": None,
                    "status": None,
                    "estimated_current_ma": None,
                }
                for name in HAND_ACTUATORS
            },
            "frame_sequence": self._frame_sequence,
            "visual_pose": {
                "sequence": visual_sequence,
                "timestamp": timestamp,
                "root_link": "r_wrist_interface",
                "coordinate_frame": "root-relative, meters, quaternion-wxyz",
                "bodies": visual_bodies,
            },
            "error": self._failure,
        }
        self._latest_state = state
        if self.state_callback:
            self.state_callback(state)

    def command(self, values: dict[str, Any], hand_speed: dict[str, list[int]] | None = None) -> None:
        if not self.connected:
            raise RuntimeError("MuJoCo runtime is disconnected")
        with self._lock:
            if any(name in ARM_JOINTS for name in values):
                for name, value in values.items():
                    self._targets[name] = max(ARM_MIN_RAD, min(ARM_MAX_RAD, float(value)))
            else:
                self._targets.update(named_hand_to_mujoco(values))

    def observe(self) -> dict[str, Any]:
        return dict(self._latest_state)

    def frame(self) -> tuple[int, bytes | None]:
        with self._lock:
            return self._frame_sequence, self._latest_frame

    def stop(self) -> None:
        with self._lock:
            if self._data is not None:
                for name, address in self._joint_qpos.items():
                    self._targets[name] = float(self._data.qpos[address])

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        self._connected = False


class SerialAmazingHandTransport(HandTransport):
    """Physical AmazingHand adapter with discovery, validation, and safe torque off."""

    def __init__(self, port: str = "/dev/ttyACM0", baudrate: int = 1_000_000) -> None:
        self.port = port
        self.baudrate = baudrate
        self.controller: Any = None
        self.connected = False
        self._last_telemetry = 0.0
        self._last_command = 0.0
        self._lock = threading.RLock()
        self._telemetry_cache: dict[str, Any] = {}
        self._telemetry_stop = threading.Event()
        self._telemetry_thread: threading.Thread | None = None

    @staticmethod
    def available_ports() -> list[str]:
        return sorted(set(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")))

    def connect(self) -> None:
        try:
            from rustypot import Scs0009PyController
        except ImportError as exc:
            raise RuntimeError("rustypot==1.5.0 is required for hybrid_serial") from exc
        if not Path(self.port).exists():
            raise RuntimeError(
                f"Serial port {self.port} does not exist; available ports: {self.available_ports()}"
            )
        self.controller = Scs0009PyController(
            serial_port=self.port,
            baudrate=self.baudrate,
            timeout=0.5,
        )
        try:
            missing = [servo_id for servo_id in range(1, 9) if not self.controller.ping(servo_id)]
            if missing:
                raise RuntimeError(f"AmazingHand servo discovery failed for IDs: {missing}")
            for servo_id in range(1, 9):
                self.controller.write_torque_enable(servo_id, 1)
            self.connected = True
            self._last_telemetry = time.monotonic()
            self._telemetry_stop.clear()
            self._telemetry_thread = threading.Thread(
                target=self._telemetry_worker,
                name="amazinghand-telemetry",
                daemon=True,
            )
            self._telemetry_thread.start()
        except Exception:
            self._torque_off()
            raise

    def command(
        self,
        hand_deg: dict[str, list[float]],
        hand_speed: dict[str, list[int]],
    ) -> None:
        if not self.connected or self.controller is None:
            raise RuntimeError("AmazingHand serial transport is disconnected")
        servo_ids: list[int] = []
        positions: list[float] = []
        with self._lock:
            for finger, values in hand_deg.items():
                pair = SERVO_IDS[finger]
                speeds = hand_speed.get(finger, [3, 3])
                for servo_id, value, speed in zip(pair, values, speeds, strict=True):
                    self.controller.write_goal_speed(servo_id, int(speed))
                    servo_ids.append(servo_id)
                    positions.append(degrees_to_hardware_radians(servo_id, value))
            if servo_ids:
                self.controller.sync_write_goal_position(servo_ids, positions)
            self._last_command = time.monotonic()

    def _poll_telemetry(self) -> dict[str, Any]:
        if not self.connected or self.controller is None:
            return {}
        result: dict[str, Any] = {}
        with self._lock:
            for finger in UI_FINGERS:
                for index, servo_id in enumerate(SERVO_IDS[finger], start=1):
                    name = f"{finger}_motor{index}"
                    load = float(self.controller.read_present_load(servo_id))
                    result[name] = {
                        "position": hardware_radians_to_degrees(
                            servo_id, self.controller.read_present_position(servo_id)
                        ),
                        "goal": hardware_radians_to_degrees(
                            servo_id, self.controller.read_goal_position(servo_id)
                        ),
                        "speed": float(self.controller.read_present_speed(servo_id)),
                        "load": load,
                        "voltage": float(self.controller.read_present_voltage(servo_id)),
                        "temperature": float(self.controller.read_present_temperature(servo_id)),
                        "status": int(self.controller.read_status(servo_id)),
                        "moving": bool(int(self.controller.read_moving(servo_id))),
                        "estimated_current_ma": round(
                            min(150.0, abs(load) * 100.0 if abs(load) <= 1.5 else abs(load) / 10.23)
                            / 100.0
                            * 1200.0,
                            1,
                        ),
                    }
        return result

    def _telemetry_worker(self) -> None:
        while not self._telemetry_stop.wait(0.1) and self.connected:
            try:
                result = self._poll_telemetry()
                self._telemetry_cache = result
                self._last_telemetry = time.monotonic()
            except Exception:
                if time.monotonic() - self._last_telemetry > 1.0:
                    self._torque_off()
                    self.connected = False
                    return

    def observe(self) -> dict[str, Any]:
        if self.connected and time.monotonic() - self._last_telemetry > 1.0:
            self.stop()
        return dict(self._telemetry_cache)

    def _torque_off(self) -> None:
        if self.controller is None:
            return
        for servo_id in range(1, 9):
            with suppress(Exception):
                self.controller.write_torque_enable(servo_id, 0)

    def stop(self) -> None:
        self._telemetry_stop.set()
        self._torque_off()
        self.connected = False
        if self._telemetry_thread and self._telemetry_thread is not threading.current_thread():
            self._telemetry_thread.join(timeout=1.0)

    def close(self) -> None:
        self.stop()
        self.controller = None


def finite_mapping(values: dict[str, float]) -> bool:
    return all(math.isfinite(float(value)) for value in values.values())
