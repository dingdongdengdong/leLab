from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from isaacsim_validation.contracts import grasp_to_urdf_targets

from .superarm.mapping import degrees_to_mujoco


def _superarm_asset_root() -> str:
    env_path = os.environ.get("SUPERARM_ASSET_ROOT")
    if env_path:
        return env_path
    return str(Path.cwd())


def _remap_missing_container_path(path: Path) -> Path:
    if path.exists():
        return path
    return path


def _resolve_robot_config_path(record: dict) -> Path | None:
    config_path = record.get("superarm_config") or record.get("follower_config")
    if not isinstance(config_path, str) or not config_path.strip():
        return None
    path = Path(config_path)
    if not path.is_absolute():
        workspace = record.get("superarm_asset_root") or _superarm_asset_root()
        path = Path(workspace) / path
    return _remap_missing_container_path(path)


def _default_manual_leader_presets(joint_count: int) -> list[dict[str, Any]]:
    def pad(values: list[float]) -> list[float]:
        padded = values[:joint_count]
        padded.extend([0.0] * max(0, joint_count - len(padded)))
        return padded

    return [
        {"name": "Home zero", "action": pad([0.0, 0.0, 0.0, 0.0, 0.0])},
        {"name": "Positive reach", "action": pad([0.25, -0.20, 0.30, -0.35, 0.20])},
        {"name": "Negative reach", "action": pad([-0.25, 0.20, -0.30, 0.35, -0.20])},
        {"name": "Mixed elbow", "action": pad([0.40, 0.10, 0.15, -0.45, 0.30])},
    ]


def _amazinghand_presets(joint_count: int) -> list[dict[str, Any]]:
    def fit(values: list[float]) -> list[float]:
        fitted = values[:joint_count]
        fitted.extend([0.0] * max(0, joint_count - len(fitted)))
        return fitted

    return [
        {"name": "Open hand", "action": fit([0.05, 0.02] * 4)},
        {"name": "Half close", "action": fit([0.50, 0.56] * 4)},
        {"name": "Close hand", "action": fit([0.95, 1.10] * 4)},
    ]


def _combined_presets(motion_codes: dict[str, float]) -> list[dict[str, Any]]:
    home = [0.0, 0.0, 0.0, 0.0, 0.0]
    reach = [0.25, -0.20, 0.30, -0.35, 0.20]
    return [
        {"name": "Home / open", "action": [*home, motion_codes["open"]]},
        {"name": "Safe reach / open", "action": [*reach, motion_codes["open"]]},
        {
            "name": "Safe reach / half close",
            "action": [*reach, motion_codes["half_close"]],
        },
        {"name": "Home / close", "action": [*home, motion_codes["close"]]},
    ]


def _manual_leader_kind(raw_config: dict, record: dict) -> str:
    manual = raw_config.get("manual_leader") or {}
    if isinstance(manual, dict) and isinstance(manual.get("kind"), str):
        return manual["kind"]
    name = str(record.get("name") or "").lower()
    config_path = str(record.get("superarm_config") or record.get("follower_config") or "").lower()
    if "amazinghand" in name or "amazinghand" in config_path:
        return "amazinghand"
    return "default"


def build_manual_leader_config(record: dict) -> dict[str, Any]:
    robot_backend = record.get("robot_backend") or "superarm_mujoco"
    config_path = _resolve_robot_config_path(record)
    if config_path is None or not config_path.exists():
        raise FileNotFoundError("SuperArm config file is missing.")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    joint_names = raw.get("joint_names") or []
    if not isinstance(joint_names, list) or not all(isinstance(name, str) for name in joint_names):
        raise ValueError("SuperArm config does not define joint_names.")

    manual = raw.get("manual_leader") if isinstance(raw.get("manual_leader"), dict) else {}
    kind = _manual_leader_kind(raw, record)
    hand_motions: list[dict[str, Any]] = []
    physical_joint_names = raw.get("physical_joint_names") or list(joint_names)
    if kind == "superarm_amazinghand":
        if len(joint_names) != 6 or joint_names[-1] != "amazinghand_motion":
            raise ValueError("Combined SuperArm config must define five arm joints plus amazinghand_motion.")
        arm_limits = raw.get("arm_limits")
        if not isinstance(arm_limits, dict):
            raise ValueError("Combined SuperArm config does not define arm_limits.")
        sliders = []
        for joint_name in joint_names[:5]:
            limit = arm_limits.get(joint_name)
            if not isinstance(limit, dict):
                raise ValueError(f"Combined SuperArm config is missing limits for {joint_name}.")
            sliders.append(
                {
                    "name": joint_name,
                    "label": joint_name,
                    "min": float(limit["min"]),
                    "max": float(limit["max"]),
                    "step": float(limit.get("step", 0.01)),
                    "default": float(limit.get("default", 0.0)),
                }
            )
        hand_joint_names = [name for name in physical_joint_names if name not in joint_names[:5]]
        configured_motions = raw.get("hand_motions")
        if not isinstance(configured_motions, list) or not configured_motions:
            raise ValueError("Combined SuperArm config does not define hand_motions.")
        for motion in configured_motions:
            degrees = motion.get("degrees") if isinstance(motion, dict) else None
            if not isinstance(degrees, int | float):
                raise ValueError("Each AmazingHand motion must define one fixed servo angle.")
            if robot_backend == "superarm_isaac":
                targets_by_name = grasp_to_urdf_targets(float(motion["code"]))
                targets = [targets_by_name[name] for name in hand_joint_names]
            else:
                targets = [
                    degrees_to_mujoco(1 if name.endswith("motor1") else 2, float(degrees))
                    for name in hand_joint_names
                ]
            hand_motions.append(
                {
                    "name": str(motion["name"]),
                    "label": str(motion["name"]).replace("_", " ").title(),
                    "code": float(motion["code"]),
                    "joint_targets": dict(zip(hand_joint_names, map(float, targets), strict=True)),
                }
            )
        motion_codes = {motion["name"]: motion["code"] for motion in hand_motions}
        if not {"open", "half_close", "close"}.issubset(motion_codes):
            raise ValueError("Combined SuperArm hand_motions must include open, half_close, and close.")
        presets = _combined_presets(motion_codes)
    elif kind == "amazinghand":
        slider_min = float(manual.get("slider_min", 0.0))
        slider_max = float(manual.get("slider_max", 1.2))
        slider_step = float(manual.get("slider_step", 0.01))
        presets = _amazinghand_presets(len(joint_names))
    else:
        slider_min = float(manual.get("slider_min", -1.57))
        slider_max = float(manual.get("slider_max", 1.57))
        slider_step = float(manual.get("slider_step", 0.01))
        presets = _default_manual_leader_presets(len(joint_names))

    if kind != "superarm_amazinghand":
        sliders = [
            {
                "name": joint_name,
                "label": joint_name,
                "min": slider_min,
                "max": slider_max,
                "step": slider_step,
                "default": 0.0,
            }
            for joint_name in joint_names
        ]
    start_request = {
        "leader_port": record.get("leader_port") or "unused",
        "follower_port": record.get("follower_port") or "unused",
        "leader_config": record.get("leader_config") or "unused",
        "follower_config": record.get("follower_config") or str(config_path),
        "robot_backend": robot_backend,
        "superarm_config": str(config_path),
        "superarm_asset_root": record.get("superarm_asset_root") or _superarm_asset_root(),
        "mujoco_model_path": record.get("mujoco_model_path"),
    }
    if robot_backend == "superarm_isaac":
        start_request.update(
            {
                "isaac_distribution_zip": record.get("isaac_distribution_zip"),
                "isaac_expected_sha256": record.get("isaac_expected_sha256"),
                "isaac_bridge_mode": record.get("isaac_bridge_mode") or "managed",
                "isaac_host": record.get("isaac_host") or "127.0.0.1",
                "isaac_port": int(record.get("isaac_port") or 8765),
                "isaac_external_run_dir": record.get("isaac_external_run_dir"),
            }
        )
    return {
        "status": "success",
        "robot_name": record["name"],
        "robot_backend": robot_backend,
        "joint_names": joint_names,
        "physical_joint_names": physical_joint_names,
        "sliders": sliders,
        "hand_motions": hand_motions,
        "presets": presets,
        "start_endpoint": "/move-arm",
        "action_endpoint": "/send-joint-action",
        "stop_endpoint": "/stop-teleoperation",
        "start_request": start_request,
    }
