"""Validated API and runtime configuration for SuperArm Isaac SAC jobs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_DISTRIBUTION_SHA256 = "3bd316090d17f9903562139983a6c66731717f7246045ebdaf90610bf3e596d3"
DEFAULT_TASK = "SuperArmIsaacPickLift-v0"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ReinforcementLearningRequest(BaseModel):
    """One local, autonomous HIL-SERL/SAC run in Isaac Sim."""

    task: Literal["SuperArmIsaacPickLift-v0"] = DEFAULT_TASK
    runner: Literal["local"] = "local"
    seed: int = Field(default=1000, ge=0, le=2**32 - 1)
    episode_length_steps: int = Field(default=150, ge=10, le=10_000)
    training_steps: int = Field(default=20_000, ge=1, le=100_000_000)
    online_buffer_capacity: int = Field(default=100_000, ge=1)
    learning_starts: int = Field(default=100, ge=1)
    batch_size: int = Field(default=256, ge=1, le=65_536)
    actor_lr: float = Field(default=3e-4, gt=0, le=1.0)
    critic_lr: float = Field(default=3e-4, gt=0, le=1.0)
    temperature_lr: float = Field(default=3e-4, gt=0, le=1.0)
    checkpoint_frequency: int = Field(default=5_000, ge=1)
    distribution_zip: str
    distribution_sha256: str = DEFAULT_DISTRIBUTION_SHA256
    learner_port: int = Field(default=50051, ge=1024, le=65535)
    bridge_port: int = Field(default=8765, ge=1024, le=65535)
    camera_preview: bool = True
    resume_from: str | None = None

    @field_validator("distribution_sha256")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("distribution_sha256 must be 64 lowercase hexadecimal characters")
        return normalized

    @field_validator("distribution_zip")
    @classmethod
    def validate_distribution_path(cls, value: str) -> str:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("distribution_zip must be an absolute path")
        return str(path)

    @field_validator("resume_from")
    @classmethod
    def validate_resume_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ValueError("resume_from must be an absolute path")
        return str(path)

    @model_validator(mode="after")
    def validate_resource_contract(self) -> ReinforcementLearningRequest:
        if self.learning_starts > self.online_buffer_capacity:
            raise ValueError("learning_starts cannot exceed online_buffer_capacity")
        if self.batch_size > self.online_buffer_capacity:
            raise ValueError("batch_size cannot exceed online_buffer_capacity")
        if self.learner_port == self.bridge_port:
            raise ValueError("learner_port and bridge_port must be different")
        return self
