# Copyright 2025 The HuggingFace Inc. team. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import math
import threading
import time
from typing import Any

from pydantic import BaseModel

try:
    from lerobot.robots.so_follower import SO101Follower, SO101FollowerConfig
    from lerobot.teleoperators.so_leader import SO101Leader, SO101LeaderConfig
except ModuleNotFoundError:
    # Keep the module importable in lightweight test/dev environments.  Real
    # SO-101 use still requires LeRobot to be installed; tests monkeypatch
    # these names with fakes.
    class _MissingLeRobot:
        def __init__(self, *args, **kwargs) -> None:
            raise ModuleNotFoundError("LeRobot is required for SO-101 teleoperation")

    SO101Follower = _MissingLeRobot
    SO101Leader = _MissingLeRobot

    class SO101FollowerConfig:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

    class SO101LeaderConfig:
        def __init__(self, **kwargs) -> None:
            self.__dict__.update(kwargs)

from .utils.config import setup_calibration_files
from .utils.devices import safe_disconnect_device

logger = logging.getLogger(__name__)

# sts3215 motor resolution; lerobot's _normalize uses (resolution - 1).
_STS3215_MAX_RES = 4095

# SO-101 URDF (so101_new_calib.urdf) is authored with the all-zeros pose at the
# arm's sleep position, not the "middle of range" pose where calibration's
# set_half_turn_homings is performed. To make the URDF track the real arm:
#   URDF_value = sign * (motor_normalized_deg - motor_at_urdf_zero_deg)
# where motor_at_urdf_zero_deg = (urdf_zero_ticks - mid) * 360 / max_res, and
# `urdf_zero_ticks` is the raw Present_Position when the robot is at sleep.
# That tick value is a property of the SO-101 mechanics + URDF design, so it's
# constant across calibrations as long as the user pressed ENTER at the "middle
# of range" pose during set_half_turn_homings.
# Joints not listed here use lerobot's default convention (URDF = motor).
_SO101_URDF_CORRECTIONS = {
    # motor_name: (sign, urdf_zero_present_position_ticks)
    "shoulder_lift": (+1, 3252),
    "elbow_flex": (+1, 1029),
}

# Global variables for teleoperation state
teleoperation_active = False
teleoperation_thread: threading.Thread | None = None
current_robot = None
current_teleop = None
# Guards the start path; the worker owns disconnect so stop() does not race.
_state_lock = threading.Lock()


class TeleoperateRequest(BaseModel):
    leader_port: str
    follower_port: str
    leader_config: str
    follower_config: str
    robot_backend: str = "so101"
    superarm_config: str | None = None
    superarm_asset_root: str | None = None
    mujoco_model_path: str | None = None


class JointActionRequest(BaseModel):
    action: list[float] | dict[str, float]


def get_joint_positions_from_robot(robot) -> dict[str, float]:
    """
    Extract current joint positions from the robot and convert to URDF joint format.

    Args:
        robot: The robot instance (SO101Follower)

    Returns:
        Dictionary mapping URDF joint names to radian values
    """
    get_visualization_joints = getattr(robot, "get_visualization_joints", None)
    if callable(get_visualization_joints):
        return {
            str(name): float(value)
            for name, value in get_visualization_joints().items()
        }

    try:
        observation = robot.get_observation()
    except AttributeError:
        observation = robot.capture_observation().get("observation.state", [])

    # Generic LeRobot custom robot path. Custom robots expose feature keys and
    # do not use the SO-101 URDF
    # joint names.
    if isinstance(observation, dict) and not any(
        f"{name}.pos" in observation
        for name in ("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper")
    ):
        return {
            key.removesuffix(".pos"): float(value)
            for key, value in observation.items()
            if key.endswith(".pos")
        }

    # Some custom backends return the LeRobot
    # state vector directly.  Convert it back to named joints using config
    # metadata instead of falling through to the SO-101 calibration path.
    if not isinstance(observation, dict):
        joint_names = list(getattr(getattr(robot, "config", None), "joint_names", []) or [])
        values = list(observation)
        if joint_names:
            return {name: float(values[idx]) if idx < len(values) else 0.0 for idx, name in enumerate(joint_names)}

    motor_to_urdf_mapping = {
        "shoulder_pan": "Rotation",
        "shoulder_lift": "Pitch",
        "elbow_flex": "Elbow",
        "wrist_flex": "Wrist_Pitch",
        "wrist_roll": "Wrist_Roll",
        "gripper": "Jaw",
    }

    try:
        calibration = robot.calibration or {}

        joint_positions: dict[str, float] = {}
        debug_rows = []
        for motor_name, urdf_joint_name in motor_to_urdf_mapping.items():
            motor_key = f"{motor_name}.pos"
            if motor_key not in observation:
                logger.warning(f"Motor {motor_key} not found in observation")
                joint_positions[urdf_joint_name] = 0.0
                continue

            raw_deg = observation[motor_key]
            angle_degrees = raw_deg
            correction = _SO101_URDF_CORRECTIONS.get(motor_name)
            if correction is not None and motor_name in calibration:
                sign, urdf_zero_ticks = correction
                cal = calibration[motor_name]
                mid = (cal.range_min + cal.range_max) / 2
                motor_at_urdf_zero = (urdf_zero_ticks - mid) * 360 / _STS3215_MAX_RES
                angle_degrees = sign * (raw_deg - motor_at_urdf_zero)

            joint_positions[urdf_joint_name] = angle_degrees * math.pi / 180.0
            debug_rows.append(
                f"{motor_name:14s} raw={raw_deg:+8.2f}° → {urdf_joint_name:11s} = {angle_degrees:+8.2f}°"
            )

        # Throttled debug print (~once per second at 20 Hz broadcast).
        now = time.time()
        if now - getattr(get_joint_positions_from_robot, "_last_log", 0) > 1.0:
            get_joint_positions_from_robot._last_log = now
            logger.info("[joint-debug]\n  " + "\n  ".join(debug_rows))

        return joint_positions
    except Exception as e:
        logger.error(f"Error getting joint positions: {e}")
        return dict.fromkeys(motor_to_urdf_mapping.values(), 0.0)


def get_visual_pose_from_robot(robot) -> dict[str, Any] | None:
    """Read an optional exact visual-pose frame from a custom robot backend."""
    get_visualization_pose = getattr(robot, "get_visualization_pose", None)
    if not callable(get_visualization_pose):
        return None
    visual_pose = get_visualization_pose()
    return dict(visual_pose) if isinstance(visual_pose, dict) else None


def _create_superarm_mujoco_robot(request: TeleoperateRequest):
    from .superarm.robot import SuperArmMujocoRobot, SuperArmMujocoRobotConfig

    return SuperArmMujocoRobot(
        SuperArmMujocoRobotConfig(
            id="lelab_web",
            model_path=request.mujoco_model_path,
        )
    )


def _handle_start_superarm_teleoperation(request: TeleoperateRequest, websocket_manager=None) -> dict[str, Any]:
    global teleoperation_active, teleoperation_thread, current_robot, current_teleop

    robot = None
    try:
        robot = _create_superarm_mujoco_robot(request)
        logger.info("Connecting SuperArm MuJoCo backend...")
        robot.connect()
        current_robot = robot
        current_teleop = None

        def superarm_worker():
            global teleoperation_active, current_robot, current_teleop

            logger.info("Starting SuperArm MuJoCo telemetry loop...")
            try:
                last_broadcast_time = 0.0
                broadcast_interval = 0.05
                while teleoperation_active:
                    current_time = time.time()
                    if current_time - last_broadcast_time >= broadcast_interval:
                        joint_positions = get_joint_positions_from_robot(robot)
                        joint_data = {
                            "type": "joint_update",
                            "joints": joint_positions,
                            "timestamp": current_time,
                            "robot_backend": "superarm_mujoco",
                        }
                        visual_pose = get_visual_pose_from_robot(robot)
                        if visual_pose is not None:
                            joint_data["visual_pose"] = visual_pose
                        if websocket_manager and websocket_manager.active_connections:
                            websocket_manager.broadcast_joint_data_sync(joint_data)
                        last_broadcast_time = current_time
                    time.sleep(0.01)
            except Exception as e:
                logger.error(f"Error during SuperArm MuJoCo telemetry loop: {e}")
            finally:
                _safe_disconnect(robot)
                logger.info("SuperArm MuJoCo teleoperation stopped")
                teleoperation_active = False
                current_robot = None
                current_teleop = None

        teleoperation_thread = threading.Thread(
            target=superarm_worker, name="superarm-mujoco-teleoperation", daemon=True
        )
        teleoperation_thread.start()
        return {
            "success": True,
            "message": "SuperArm MuJoCo backend connected successfully",
            "robot_backend": "superarm_mujoco",
            "joint_positions": get_joint_positions_from_robot(robot),
        }
    except Exception as e:
        _safe_disconnect(robot)
        teleoperation_active = False
        current_robot = None
        current_teleop = None
        logger.error(f"Failed to start SuperArm MuJoCo backend: {e}")
        return {"success": False, "message": str(e), "robot_backend": "superarm_mujoco"}


def _safe_disconnect(device) -> None:
    """Disconnect a robot/teleop device, swallowing (but logging) any error.

    Used on the connection-failure cleanup path so one device's failure can't
    leave the other holding its serial port open.
    """
    safe_disconnect_device(device, logger)


def _other_feature_busy_messages() -> list[str]:
    """Return blockers from mutually-exclusive LeLab modes.

    Importing record/rollout can require optional LeRobot dependencies.  In a
    lightweight API test environment those modules may be unavailable; that
    should not make the teleoperate module itself unusable.
    """
    messages: list[str] = []
    try:
        from . import record as _record

        if _record.recording_active:
            messages.append("Recording is currently active. Stop it first.")
    except ModuleNotFoundError:
        pass
    try:
        from . import rollout as _rollout

        if _rollout.inference_active:
            messages.append("Inference is currently active. Stop it first.")
    except ModuleNotFoundError:
        pass
    return messages


def handle_start_teleoperation(request: TeleoperateRequest, websocket_manager=None) -> dict[str, Any]:
    """Handle start teleoperation request.

    Connects to both arms *synchronously* so that a connection failure (arm
    unplugged, port busy, power off) is reported back to the caller, rather than
    dying silently in the worker thread while the API has already claimed
    success. Only the teleoperation loop runs in the background thread.
    """
    global teleoperation_active, teleoperation_thread, current_robot, current_teleop

    with _state_lock:
        if teleoperation_active:
            return {"success": False, "message": "Teleoperation is already active"}
        busy_messages = _other_feature_busy_messages()
        if busy_messages:
            return {"success": False, "message": busy_messages[0]}
        teleoperation_active = True

    if request.robot_backend == "superarm_mujoco":
        return _handle_start_superarm_teleoperation(request, websocket_manager)
    if request.robot_backend != "so101":
        teleoperation_active = False
        return {"success": False, "message": f"Unsupported robot_backend: {request.robot_backend}"}

    robot = None
    teleop_device = None
    try:
        logger.info(
            f"Starting teleoperation with leader port: {request.leader_port}, follower port: {request.follower_port}"
        )

        # Setup calibration files
        leader_config_name, follower_config_name = setup_calibration_files(
            request.leader_config, request.follower_config
        )

        # Create robot and teleop configs
        robot_config = SO101FollowerConfig(
            port=request.follower_port,
            id=follower_config_name,
        )

        teleop_config = SO101LeaderConfig(
            port=request.leader_port,
            id=leader_config_name,
        )

        # Connect synchronously. If either device fails to connect, clean up the
        # other (so its serial port is released) and report the error — do NOT
        # leave the caller thinking teleoperation started.
        logger.info("Initializing robot and teleop device...")
        robot = SO101Follower(robot_config)
        teleop_device = SO101Leader(teleop_config)

        # Connect each arm separately so the error names which one failed and
        # tells the user what to do, instead of a generic "failed to start".
        logger.info("Connecting to follower arm...")
        try:
            robot.bus.connect()
        except Exception as e:
            raise RuntimeError(
                f"Could not connect to the follower arm on {request.follower_port}. "
                "Make sure it's plugged in and powered on, then try again."
            ) from e

        logger.info("Connecting to leader arm...")
        try:
            teleop_device.bus.connect()
        except Exception as e:
            raise RuntimeError(
                f"Could not connect to the leader arm on {request.leader_port}. "
                "Make sure it's plugged in and powered on, then try again."
            ) from e

        # Write calibration to motors' memory
        logger.info("Writing calibration to motors...")
        robot.bus.write_calibration(robot.calibration)
        teleop_device.bus.write_calibration(teleop_device.calibration)

        # Connect cameras and configure motors
        logger.info("Connecting cameras and configuring motors...")
        for cam in robot.cameras.values():
            cam.connect()
        robot.configure()
        teleop_device.configure()
        logger.info("Successfully connected to both devices")

        current_robot = robot
        current_teleop = teleop_device

        # Stream the arms in the background; the worker owns disconnect so stop()
        # does not race the serial bus from the request thread.
        def teleoperation_worker():
            global teleoperation_active, current_robot, current_teleop

            logger.info("Starting teleoperation loop...")
            try:
                last_broadcast_time = 0
                broadcast_interval = 0.05  # 20 FPS

                while teleoperation_active:
                    action = teleop_device.get_action()
                    robot.send_action(action)

                    current_time = time.time()
                    if current_time - last_broadcast_time >= broadcast_interval:
                        try:
                            joint_positions = get_joint_positions_from_robot(robot)
                            joint_data = {
                                "type": "joint_update",
                                "joints": joint_positions,
                                "timestamp": current_time,
                            }
                            if websocket_manager and websocket_manager.active_connections:
                                websocket_manager.broadcast_joint_data_sync(joint_data)
                            last_broadcast_time = current_time
                        except Exception as e:
                            logger.error(f"Error broadcasting joint data: {e}")

                    time.sleep(0.001)
            except Exception as e:
                logger.error(f"Error during teleoperation loop: {e}")
            finally:
                _safe_disconnect(robot)
                _safe_disconnect(teleop_device)
                logger.info("Teleoperation stopped")
                teleoperation_active = False
                current_robot = None
                current_teleop = None

        teleoperation_thread = threading.Thread(
            target=teleoperation_worker, name="teleoperation-worker", daemon=True
        )
        teleoperation_thread.start()

        return {
            "success": True,
            "message": "Teleoperation started successfully",
            "leader_port": request.leader_port,
            "follower_port": request.follower_port,
        }

    except Exception as e:
        # Connection (or setup) failed before the loop started: release any
        # device that did open, reset state, and surface the error.
        _safe_disconnect(robot)
        _safe_disconnect(teleop_device)
        teleoperation_active = False
        current_robot = None
        current_teleop = None
        logger.error(f"Failed to start teleoperation: {e}")
        # str(e) is already a user-facing message for the connection failures
        # raised above; the toast title supplies the "error starting" context.
        return {"success": False, "message": str(e)}


def handle_stop_teleoperation() -> dict[str, Any]:
    """Handle stop teleoperation request.

    Signals the worker via `teleoperation_active = False` and waits for it to
    exit. The worker owns the disconnect call, so this avoids racing the
    serial bus from the request thread.
    """
    global teleoperation_active, teleoperation_thread

    if not teleoperation_active:
        return {"success": False, "message": "No teleoperation session is active"}

    logger.info("Stop teleoperation triggered from web interface")
    teleoperation_active = False

    worker = teleoperation_thread
    if worker is not None and worker.is_alive():
        worker.join(timeout=5.0)
        if worker.is_alive():
            logger.warning("Teleoperation worker did not exit within 5s")
    teleoperation_thread = None

    return {"success": True, "message": "Teleoperation stopped successfully"}


def handle_teleoperation_status() -> dict[str, Any]:
    """Handle teleoperation status request"""
    return {
        "teleoperation_active": teleoperation_active,
        "available_controls": {
            "stop_teleoperation": teleoperation_active,
        },
        "message": "Teleoperation status retrieved successfully",
    }


def handle_get_joint_positions() -> dict[str, Any]:
    """Handle get current robot joint positions request"""
    global current_robot

    if not teleoperation_active or current_robot is None:
        return {"success": False, "message": "No active teleoperation session"}

    try:
        joint_positions = get_joint_positions_from_robot(current_robot)
        return {"success": True, "joint_positions": joint_positions, "timestamp": time.time()}
    except Exception as e:
        logger.error(f"Error getting joint positions: {e}")
        return {"success": False, "message": f"Failed to get joint positions: {str(e)}"}


def handle_send_joint_action(request: JointActionRequest) -> dict[str, Any]:
    """Send one action to the active robot backend.

    For `robot_backend=superarm_mujoco`, `action` is the canonical six-value
    vector or a mapping keyed by the canonical LeRobot features.
    """
    global current_robot

    if not teleoperation_active or current_robot is None:
        return {"success": False, "message": "No active teleoperation session"}
    try:
        sent = current_robot.send_action(request.action)
        physical_targets = get_joint_positions_from_robot(current_robot)
        return {
            "success": True,
            "sent_action": sent.tolist() if hasattr(sent, "tolist") else sent,
            "resolved_logical_action": sent.tolist() if hasattr(sent, "tolist") else sent,
            "physical_targets": physical_targets,
            "joint_positions": physical_targets,
            "timestamp": time.time(),
        }
    except Exception as e:
        logger.error(f"Error sending joint action: {e}")
        return {"success": False, "message": f"Failed to send joint action: {str(e)}"}
