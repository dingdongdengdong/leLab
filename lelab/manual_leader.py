from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def _superarm_ws_path() -> str:
    env_path = os.environ.get("SUPERARM_WS_PATH")
    if env_path:
        return env_path
    container_path = Path("/workspaces/superarm_ws")
    if container_path.exists():
        return str(container_path)
    repo_path = Path(__file__).resolve().parents[3]
    if (repo_path / "isaacsim_test" / "lerobot").exists():
        return str(repo_path)
    return str(container_path)


def _remap_missing_container_path(path: Path) -> Path:
    if path.exists():
        return path
    marker = Path("/workspaces/superarm_ws")
    try:
        relative = path.relative_to(marker)
    except ValueError:
        return path
    local_path = Path(_superarm_ws_path()) / relative
    return local_path if local_path.exists() else path


def _resolve_robot_config_path(record: dict) -> Path | None:
    config_path = record.get("isaacsim_config") or record.get("follower_config")
    if not isinstance(config_path, str) or not config_path.strip():
        return None
    path = Path(config_path)
    if not path.is_absolute():
        workspace = record.get("superarm_ws_path") or _superarm_ws_path()
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


def _manual_leader_kind(raw_config: dict, record: dict) -> str:
    manual = raw_config.get("manual_leader") or {}
    if isinstance(manual, dict) and isinstance(manual.get("kind"), str):
        return manual["kind"]
    name = str(record.get("name") or "").lower()
    config_path = str(record.get("isaacsim_config") or record.get("follower_config") or "").lower()
    if "amazinghand" in name or "amazinghand" in config_path:
        return "amazinghand"
    return "default"


def build_manual_leader_config(record: dict) -> dict[str, Any]:
    config_path = _resolve_robot_config_path(record)
    if config_path is None or not config_path.exists():
        raise FileNotFoundError("Isaac Sim config file is missing.")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    joint_names = raw.get("joint_names") or []
    if not isinstance(joint_names, list) or not all(isinstance(name, str) for name in joint_names):
        raise ValueError("Isaac Sim config does not define joint_names.")

    manual = raw.get("manual_leader") if isinstance(raw.get("manual_leader"), dict) else {}
    kind = _manual_leader_kind(raw, record)
    if kind == "amazinghand":
        slider_min = float(manual.get("slider_min", 0.0))
        slider_max = float(manual.get("slider_max", 1.2))
        slider_step = float(manual.get("slider_step", 0.01))
        presets = _amazinghand_presets(len(joint_names))
    else:
        slider_min = float(manual.get("slider_min", -1.57))
        slider_max = float(manual.get("slider_max", 1.57))
        slider_step = float(manual.get("slider_step", 0.01))
        presets = _default_manual_leader_presets(len(joint_names))

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
        "robot_backend": "isaacsim_rpo_arm",
        "isaacsim_config": str(config_path),
        "superarm_ws_path": record.get("superarm_ws_path") or _superarm_ws_path(),
    }
    return {
        "status": "success",
        "robot_name": record["name"],
        "robot_backend": "isaacsim_rpo_arm",
        "joint_names": joint_names,
        "sliders": sliders,
        "presets": presets,
        "start_endpoint": "/move-arm",
        "action_endpoint": "/send-joint-action",
        "stop_endpoint": "/stop-teleoperation",
        "start_request": start_request,
    }
