"""Atomic versioned SuperArm pose/sequence persistence and upstream import."""

from __future__ import annotations

import math
import os
import re
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from .mapping import ARM_JOINTS, UI_FINGERS, named_to_upstream_positions, upstream_positions_to_named

DEFAULT_PROGRAM_PATH = (
    Path.home() / ".cache" / "huggingface" / "lerobot" / "amazinghand" / "programs.yaml"
)
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_ ]{1,50}$")


def validate_program_name(name: str) -> str:
    if not NAME_PATTERN.fullmatch(name) or name != name.strip():
        raise ValueError("Name must be 1-50 letters, digits, spaces, or underscores without edge spaces")
    return name


class ProgramStore:
    def __init__(self, path: str | Path = DEFAULT_PROGRAM_PATH) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()
        created = not self.path.exists()
        if created:
            self._write({"schema_version": 1, "poses": {}, "sequences": {}})
        bundled = Path(__file__).resolve().parent / "data" / "amazinghandcontrol_hand_config_2a59fd8.yaml"
        current = self._read()
        if bundled.is_file() and (created or (not current["poses"] and not current["sequences"])):
            self.import_upstream(bundled)

    def _read(self) -> dict[str, Any]:
        with self._lock:
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if raw.get("schema_version") != 1:
            raise ValueError("Unsupported SuperArm program schema_version")
        raw.setdefault("poses", {})
        raw.setdefault("sequences", {})
        return raw

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rendered = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
        with self._lock:
            fd, temporary = tempfile.mkstemp(prefix="programs-", suffix=".yaml", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(rendered)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

    def all(self) -> dict[str, Any]:
        return deepcopy(self._read())

    def list_poses(self) -> dict[str, Any]:
        return deepcopy(self._read()["poses"])

    def save_pose(self, name: str, pose: dict[str, Any]) -> dict[str, Any]:
        validate_program_name(name)
        normalized: dict[str, Any] = {}
        if "arm_rad" in pose and pose["arm_rad"] is not None:
            unknown = set(pose["arm_rad"]) - set(ARM_JOINTS)
            if unknown:
                raise ValueError(f"Unknown arm joints: {sorted(unknown)}")
            normalized["arm_rad"] = {key: float(value) for key, value in pose["arm_rad"].items()}
            if any(not math.isfinite(value) or value < -1.57 or value > 1.57 for value in normalized["arm_rad"].values()):
                raise ValueError("Arm pose values must be finite and within [-1.57, 1.57]")
        if "hand_deg" in pose and pose["hand_deg"] is not None:
            unknown = set(pose["hand_deg"]) - set(UI_FINGERS)
            if unknown:
                raise ValueError(f"Unknown fingers: {sorted(unknown)}")
            normalized["hand_deg"] = {
                key: [float(item) for item in value] for key, value in pose["hand_deg"].items()
            }
            if any(
                len(values) != 2
                or any(not math.isfinite(value) or value < -40 or value > 110 for value in values)
                for values in normalized["hand_deg"].values()
            ):
                raise ValueError("Each hand pose requires two finite values within [-40, 110]")
        if not normalized:
            raise ValueError("Pose must include arm_rad or hand_deg")
        data = self._read()
        data["poses"][name] = normalized
        self._write(data)
        return normalized

    def delete_pose(self, name: str) -> None:
        data = self._read()
        if name not in data["poses"]:
            raise KeyError(name)
        del data["poses"][name]
        self._write(data)

    def list_sequences(self) -> dict[str, Any]:
        return deepcopy(self._read()["sequences"])

    def save_sequence(self, name: str, sequence: dict[str, Any]) -> dict[str, Any]:
        validate_program_name(name)
        steps = sequence.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ValueError("Sequence must have at least one step")
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("Sequence steps must be objects")
            if "sleep_s" in step:
                if len(step) != 1 or float(step["sleep_s"]) < 0:
                    raise ValueError("Sleep steps require one nonnegative sleep_s")
            elif "pose" in step:
                if float(step.get("transition_s", 0.0)) < 0 or float(step.get("hold_s", 0.0)) < 0:
                    raise ValueError("Sequence timings cannot be negative")
                speed = step.get("hand_speed", 3)
                speeds = speed if isinstance(speed, list) else [int(speed)]
                if len(speeds) not in {1, 8} or any(int(value) < 1 or int(value) > 6 for value in speeds):
                    raise ValueError("hand_speed must be one value or eight values between 1 and 6")
            else:
                raise ValueError("Step must contain pose or sleep_s")
        normalized = {"steps": deepcopy(steps)}
        data = self._read()
        data["sequences"][name] = normalized
        self._write(data)
        return normalized

    def delete_sequence(self, name: str) -> None:
        data = self._read()
        if name not in data["sequences"]:
            raise KeyError(name)
        del data["sequences"][name]
        self._write(data)

    def import_upstream(self, upstream_yaml: str | Path) -> dict[str, int]:
        source = yaml.safe_load(Path(upstream_yaml).read_text(encoding="utf-8")) or {}
        data = self._read()
        imported_poses = 0
        for name, raw in source.get("poses", {}).items():
            data["poses"][name] = {"hand_deg": upstream_positions_to_named(raw["positions"])}
            imported_poses += 1
        imported_sequences = 0
        for name, raw in source.get("sequences", {}).items():
            steps: list[dict[str, Any]] = []
            for raw_step in raw.get("steps", []):
                text = str(raw_step).strip()
                if text.upper().startswith("SLEEP:"):
                    steps.append({"sleep_s": float(text.split(":", 1)[1].rstrip("sS"))})
                    continue
                pose_and_speed, separator, delay_text = text.partition("|")
                pose, colon, speeds_text = pose_and_speed.partition(":")
                speeds = [int(value) for value in speeds_text.split(",")] if colon else [3] * 8
                steps.append(
                    {
                        "pose": pose,
                        "transition_s": 0.0,
                        "hold_s": float(delay_text.rstrip("sS")) if separator else 0.0,
                        "hand_speed": speeds[0] if speeds and len(set(speeds)) == 1 else speeds,
                    }
                )
            data["sequences"][name] = {"steps": steps}
            imported_sequences += 1
        self._write(data)
        return {"poses": imported_poses, "sequences": imported_sequences}

    def export_upstream(self) -> dict[str, Any]:
        data = self._read()
        poses: dict[str, Any] = {}
        for name, pose in data["poses"].items():
            if "arm_rad" in pose or "hand_deg" not in pose:
                continue
            poses[name] = {"positions": named_to_upstream_positions(pose["hand_deg"])}
        sequences: dict[str, Any] = {}
        for name, sequence in data["sequences"].items():
            rendered: list[str] = []
            compatible = True
            for step in sequence["steps"]:
                if "sleep_s" in step:
                    rendered.append(f"SLEEP:{float(step['sleep_s']):g}s")
                    continue
                pose_name = step["pose"]
                if pose_name not in poses:
                    compatible = False
                    break
                speed = step.get("hand_speed", 3)
                speeds = speed if isinstance(speed, list) else [int(speed)] * 8
                rendered.append(
                    f"{pose_name}:{','.join(str(value) for value in speeds)}|{float(step.get('hold_s', 0)):g}s"
                )
            if compatible:
                sequences[name] = {"steps": rendered}
        return {"poses": poses, "sequences": sequences}
