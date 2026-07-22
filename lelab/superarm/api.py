"""FastAPI namespace for the unified SuperArm + AmazingHand controller."""

from __future__ import annotations

import asyncio
import math
import queue
import time
from typing import Literal

import yaml
from fastapi import APIRouter, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, model_validator

from .calibration import superarm_calibration
from .hardware import (
    DM4340P_LEROBOT_TYPE,
    validate_arm_joint_calibration,
    validate_arm_limits_and_gains,
    validate_dm4340p_arm_motors,
)
from .mapping import (
    ARM_JOINTS,
    ARM_MAX_RAD,
    ARM_MIN_RAD,
    SERVO_MAX_DEG,
    SERVO_MIN_DEG,
    SERVO_SPEED_MAX,
    SERVO_SPEED_MIN,
    UI_FINGERS,
)
from .service import service

router = APIRouter()


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    runtime: Literal["mujoco", "hybrid_serial", "isaac_sim"] = "mujoco"
    serial_port: str = "/dev/ttyACM0"
    workspace_root: str | None = None
    model_path: str | None = None
    isaac_distribution_zip: str | None = None
    isaac_expected_sha256: str | None = None
    isaac_bridge_mode: Literal["managed", "external"] = "managed"
    isaac_host: str = "127.0.0.1"
    isaac_port: int = 8765
    isaac_external_run_dir: str | None = None


class LogicalActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: list[float]

    @model_validator(mode="after")
    def validate_values(self) -> LogicalActionRequest:
        if len(self.values) != 6 or any(not math.isfinite(value) for value in self.values):
            raise ValueError("Logical action requires exactly six finite values")
        return self


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: Literal["whole", "hand"]
    name: str

    @model_validator(mode="after")
    def validate_name(self) -> CaptureRequest:
        if not self.name or len(self.name) > 64 or any(
            not (character.isalnum() or character in {"-", "_"})
            for character in self.name
        ):
            raise ValueError("Capture name must contain only letters, numbers, hyphens, or underscores")
        return self


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arm_rad: dict[str, float] | None = None
    hand_deg: dict[str, list[float]] | None = None
    hand_speed: dict[str, list[int]] | None = None
    source: Literal["staged", "live", "keyboard", "sequence"] = "staged"

    @model_validator(mode="after")
    def validate_action(self) -> ActionRequest:
        if not self.arm_rad and not self.hand_deg:
            raise ValueError("Action must include arm_rad or hand_deg")
        if self.arm_rad:
            unknown = set(self.arm_rad) - set(ARM_JOINTS)
            if unknown:
                raise ValueError(f"Unknown arm joints: {sorted(unknown)}")
            for name, value in self.arm_rad.items():
                if not math.isfinite(value) or not ARM_MIN_RAD <= value <= ARM_MAX_RAD:
                    raise ValueError(f"{name} must be finite and within [{ARM_MIN_RAD}, {ARM_MAX_RAD}]")
        if self.hand_deg:
            unknown = set(self.hand_deg) - set(UI_FINGERS)
            if unknown:
                raise ValueError(f"Unknown fingers: {sorted(unknown)}")
            for finger, values in self.hand_deg.items():
                if len(values) != 2 or any(not math.isfinite(value) for value in values):
                    raise ValueError(f"{finger} requires two finite servo degree values")
                if any(value < SERVO_MIN_DEG or value > SERVO_MAX_DEG for value in values):
                    raise ValueError(
                        f"{finger} servo values must be within [{SERVO_MIN_DEG}, {SERVO_MAX_DEG}]"
                    )
        if self.hand_speed:
            unknown = set(self.hand_speed) - set(UI_FINGERS)
            if unknown:
                raise ValueError(f"Unknown speed fingers: {sorted(unknown)}")
            for finger, values in self.hand_speed.items():
                if len(values) != 2 or any(
                    value < SERVO_SPEED_MIN or value > SERVO_SPEED_MAX for value in values
                ):
                    raise ValueError(
                        f"{finger} speed requires two values from {SERVO_SPEED_MIN} to {SERVO_SPEED_MAX}"
                    )
        return self


class EmergencyStopRequest(BaseModel):
    active: bool = True


class HardwareCalibrationRequest(BaseModel):
    """Measured SuperArm follower values, validated without touching hardware."""

    model_config = ConfigDict(extra="forbid")
    arm_port: str
    hand_port: str
    arm_motor_config: dict[str, tuple[int, int]]
    arm_joint_calibration: dict[str, tuple[float, float]]
    arm_joint_limits_deg: dict[str, tuple[float, float]]
    arm_position_kp: list[float]
    arm_position_kd: list[float]
    hand_speed: int = 3
    confirmed_measured: bool = False

    @model_validator(mode="after")
    def validate_measured_hardware_config(self) -> HardwareCalibrationRequest:
        if not self.confirmed_measured:
            raise ValueError(
                "Confirm that every SuperArm value was measured before generating a hardware config"
            )
        if not self.arm_port.strip() or not self.hand_port.strip():
            raise ValueError("Arm CAN port and AmazingHand serial port are required")
        motor_config = {
            name: (send_id, receive_id, DM4340P_LEROBOT_TYPE)
            for name, (send_id, receive_id) in self.arm_motor_config.items()
        }
        validate_dm4340p_arm_motors(motor_config)
        validate_arm_joint_calibration(self.arm_joint_calibration)
        validate_arm_limits_and_gains(
            self.arm_joint_limits_deg,
            self.arm_position_kp,
            self.arm_position_kd,
        )
        if not SERVO_SPEED_MIN <= self.hand_speed <= SERVO_SPEED_MAX:
            raise ValueError(f"AmazingHand speed must be in [{SERVO_SPEED_MIN}, {SERVO_SPEED_MAX}]")
        return self


class SuperArmCalibrationStartRequest(BaseModel):
    """CAN identity data needed before the torque-disabled live calibration flow."""

    model_config = ConfigDict(extra="forbid")
    arm_port: str
    arm_motor_config: dict[str, tuple[int, int]]
    confirmed_torque_disabled_area: bool = False

    @model_validator(mode="after")
    def validate_start(self) -> SuperArmCalibrationStartRequest:
        if not self.confirmed_torque_disabled_area:
            raise ValueError("Confirm that the SuperArm is supported and safe to move manually")
        if not self.arm_port.strip():
            raise ValueError("Arm CAN port is required")
        validate_dm4340p_arm_motors(
            {
                name: (send_id, receive_id, DM4340P_LEROBOT_TYPE)
                for name, (send_id, receive_id) in self.arm_motor_config.items()
            }
        )
        return self


class PoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arm_rad: dict[str, float] | None = None
    hand_deg: dict[str, list[float]] | None = None


class SequenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[dict]


class PlayRequest(BaseModel):
    loop: bool = False


def api_error(exc: Exception) -> HTTPException:
    status = 404 if isinstance(exc, KeyError) else 409 if isinstance(exc, RuntimeError) else 422
    return HTTPException(status_code=status, detail=str(exc).strip("'"))


@router.get("/api/superarm/capabilities")
def capabilities(workspace_root: str | None = None):
    return service.capabilities(workspace_root)


@router.get("/api/superarm/hardware-readiness")
def hardware_readiness():
    return service.hardware_readiness()


@router.get("/api/superarm/so101-leader-readiness")
def so101_leader_readiness():
    return service.so101_leader_readiness()


@router.post("/api/superarm/hardware-config/preview")
def preview_hardware_config(request: HardwareCalibrationRequest):
    """Validate and render a local config; this endpoint never connects or torques motors."""
    config = {
        "_type": "superarm_dm4340p_amazinghand",
        "arm_port": request.arm_port.strip(),
        "arm_can_interface": "socketcan",
        "arm_use_can_fd": True,
        "arm_can_bitrate": 1_000_000,
        "arm_can_data_bitrate": 5_000_000,
        "arm_motor_config": {
            name: [send_id, receive_id, DM4340P_LEROBOT_TYPE]
            for name, (send_id, receive_id) in request.arm_motor_config.items()
        },
        "arm_joint_calibration": {
            name: [direction, offset_rad]
            for name, (direction, offset_rad) in request.arm_joint_calibration.items()
        },
        "arm_joint_limits_deg": {
            name: [lower, upper] for name, (lower, upper) in request.arm_joint_limits_deg.items()
        },
        "arm_position_kp": request.arm_position_kp,
        "arm_position_kd": request.arm_position_kd,
        "hand_port": request.hand_port.strip(),
        "hand_baudrate": 1_000_000,
        "hand_speed": request.hand_speed,
    }
    return {
        "configuration_valid": True,
        "connects_hardware": False,
        "motion_authorized": False,
        "filename": "superarm_dm4340p_amazinghand.yaml",
        "yaml": yaml.safe_dump(config, sort_keys=False),
    }


@router.post("/api/superarm/calibration/start")
def start_superarm_calibration(request: SuperArmCalibrationStartRequest):
    return superarm_calibration.start(request.arm_port.strip(), request.arm_motor_config)


@router.get("/api/superarm/calibration")
def superarm_calibration_status():
    return superarm_calibration.status()


@router.post("/api/superarm/calibration/capture-zero")
def capture_superarm_zero():
    try:
        return superarm_calibration.capture_zero()
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/api/superarm/calibration/stop")
def stop_superarm_calibration():
    return superarm_calibration.stop()


@router.get("/api/superarm/urdf")
def source_arm_urdf(workspace_root: str | None = None):
    try:
        return Response(
            content=service.source_arm_urdf_xml(workspace_root),
            media_type="application/xml",
        )
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/api/superarm/urdf/meshes/{filename}")
def source_arm_mesh(filename: str):
    try:
        return FileResponse(service.source_arm_mesh_path(filename))
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/api/superarm/mujoco-visual-manifest")
def amazinghand_visual_manifest(workspace_root: str | None = None, model_path: str | None = None):
    try:
        return service.amazinghand_visual_manifest(workspace_root, model_path)
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/api/superarm/mujoco-visual-assets/{mesh_name}")
def amazinghand_visual_asset(
    mesh_name: str,
    workspace_root: str | None = None,
    model_path: str | None = None,
):
    try:
        return FileResponse(service.amazinghand_visual_asset_path(mesh_name, workspace_root, model_path))
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/api/superarm/session")
def start_session(request: SessionRequest):
    try:
        return service.start_session(
            request.runtime,
            serial_port=request.serial_port,
            workspace_root=request.workspace_root,
            model_path=request.model_path,
            isaac_distribution_zip=request.isaac_distribution_zip,
            isaac_expected_sha256=request.isaac_expected_sha256,
            isaac_bridge_mode=request.isaac_bridge_mode,
            isaac_host=request.isaac_host,
            isaac_port=request.isaac_port,
            isaac_external_run_dir=request.isaac_external_run_dir,
        )
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/api/superarm/session")
def session_status():
    return service.status()


@router.get("/api/superarm/telemetry")
def telemetry():
    return service.telemetry()


@router.delete("/api/superarm/session")
def delete_session():
    return service.disconnect()


@router.put("/api/superarm/action")
def action(request: ActionRequest):
    try:
        return service.action(**request.model_dump())
    except Exception as exc:
        raise api_error(exc) from exc


@router.put("/api/superarm/logical-action")
def logical_action(request: LogicalActionRequest):
    try:
        return service.logical_action(request.values)
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/api/superarm/capture")
def capture(request: CaptureRequest):
    try:
        return service.capture(request.view, request.name)
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/api/superarm/capture/latest")
def latest_capture():
    try:
        return service.latest_capture()
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/api/superarm/emergency-stop")
def emergency_stop(request: EmergencyStopRequest = EmergencyStopRequest()):
    return service.emergency_stop(request.active)


@router.get("/api/superarm/poses")
def poses():
    return service.programs.list_poses()


@router.put("/api/superarm/poses/{name}")
def save_pose(name: str, request: PoseRequest):
    try:
        return service.programs.save_pose(name, request.model_dump(exclude_none=True))
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/api/superarm/poses/{name}/apply")
def apply_pose(name: str):
    pose = service.programs.list_poses().get(name)
    if pose is None:
        raise HTTPException(status_code=404, detail="Pose not found")
    try:
        return service.action(
            arm_rad=pose.get("arm_rad"),
            hand_deg=pose.get("hand_deg"),
            source="staged",
        )
    except Exception as exc:
        raise api_error(exc) from exc


@router.delete("/api/superarm/poses/{name}", status_code=204)
def delete_pose(name: str):
    try:
        service.programs.delete_pose(name)
        return Response(status_code=204)
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/api/superarm/sequences")
def sequences():
    return service.programs.list_sequences()


@router.put("/api/superarm/sequences/{name}")
def save_sequence(name: str, request: SequenceRequest):
    try:
        return service.programs.save_sequence(name, request.model_dump())
    except Exception as exc:
        raise api_error(exc) from exc


@router.delete("/api/superarm/sequences/{name}", status_code=204)
def delete_sequence(name: str):
    try:
        service.programs.delete_sequence(name)
        return Response(status_code=204)
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/api/superarm/sequences/{name}/play")
def play_sequence(name: str, request: PlayRequest = PlayRequest()):
    try:
        return service.play_sequence(name, loop=request.loop)
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/api/superarm/sequences/pause")
def pause_sequence():
    try:
        return service.pause_sequence()
    except Exception as exc:
        raise api_error(exc) from exc


@router.post("/api/superarm/sequences/stop")
def stop_sequence():
    return service.stop_sequence()


@router.post("/api/superarm/programs/import-upstream")
def import_upstream(path: str):
    try:
        return service.programs.import_upstream(path)
    except Exception as exc:
        raise api_error(exc) from exc


@router.get("/api/superarm/programs/export-upstream")
def export_upstream():
    return Response(
        yaml.safe_dump(service.programs.export_upstream(), sort_keys=False),
        media_type="application/yaml",
    )


@router.get("/api/superarm/video")
def video():
    if not service.runtime or not service.runtime.connected:
        raise HTTPException(status_code=409, detail="SuperArm runtime is disconnected")
    if not service.runtime.supports_video:
        raise HTTPException(
            status_code=409,
            detail="Continuous video is only available for MuJoCo; use the Isaac capture endpoint.",
        )

    def frames():
        last_sequence = -1
        while service.runtime and service.runtime.connected:
            sequence, frame = service.runtime.frame()
            if frame is None or sequence == last_sequence:
                time.sleep(0.005)
                continue
            last_sequence = sequence
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.websocket("/ws/superarm")
async def superarm_websocket(websocket: WebSocket):
    await websocket.accept()
    subscriber = service.subscribe()
    try:
        await websocket.send_json({"type": "runtime_status", **service.status()})
        while True:
            try:
                event = await asyncio.to_thread(subscriber.get, True, 0.1)
            except queue.Empty:
                event = service.telemetry()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        service.unsubscribe(subscriber)
