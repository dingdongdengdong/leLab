"""Generate the upstream LeRobot actor/learner configuration for Isaac RL."""

from __future__ import annotations

import json
from pathlib import Path

from .config import ReinforcementLearningRequest


def build_lerobot_config(request: ReinforcementLearningRequest, output_dir: Path) -> dict:
    state_min = [-3.2] * 5 + [-20.0] * 5 + [0.0] + [-2.0] * 12
    state_max = [3.2] * 5 + [20.0] * 5 + [2.0] + [2.0] * 12
    return {
        "seed": request.seed,
        "dataset": None,
        "online_ratio": 1.0,
        "output_dir": str(output_dir),
        "resume": bool(request.resume_from),
        "steps": request.training_steps,
        "batch_size": request.batch_size,
        "policy": {
            "type": "gaussian_actor",
            "device": "cuda",
            "input_features": {
                "observation.image.workspace": {"type": "VISUAL", "shape": [3, 256, 256]},
                "observation.state": {"type": "STATE", "shape": [23]},
            },
            "output_features": {"action": {"type": "ACTION", "shape": [5]}},
            "num_discrete_actions": 3,
            "online_steps": request.training_steps,
            "online_buffer_capacity": request.online_buffer_capacity,
            "online_step_before_learning": request.learning_starts,
            "actor_learner_config": {
                "learner_host": "127.0.0.1",
                "learner_port": request.learner_port,
                "policy_parameters_push_frequency": 4,
            },
            "normalization_mapping": {
                "VISUAL": "MEAN_STD", "STATE": "MIN_MAX", "ACTION": "MIN_MAX"
            },
            "dataset_stats": {
                "observation.image.workspace": {
                    "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225]
                },
                "observation.state": {"min": state_min, "max": state_max},
                "action": {"min": [-1.0] * 5, "max": [1.0] * 5},
            },
        },
        "algorithm": {
            "type": "sac",
            "actor_lr": request.actor_lr,
            "critic_lr": request.critic_lr,
            "temperature_lr": request.temperature_lr,
        },
        "env": {
            "type": "gym_manipulator",
            "name": "gym_hil",
            "task": request.task,
            "fps": 10,
            "processor": {
                "control_mode": "autonomous",
                "gripper": {"use_gripper": True, "gripper_penalty": -0.02},
                "reset": {
                    "fixed_reset_joint_positions": None,
                    "reset_time_s": 0.0,
                    "control_time_s": request.episode_length_steps / 10.0,
                    "terminate_on_success": True,
                },
                "image_preprocessing": None,
            },
            "features": {
                "observation.image.workspace": {"type": "VISUAL", "shape": [3, 256, 256]},
                "observation.state": {"type": "STATE", "shape": [23]},
                "action": {"type": "ACTION", "shape": [5]},
            },
            "features_map": {
                "observation.image.workspace": "observation.image.workspace",
                "observation.state": "observation.state",
                "action": "action",
            },
        },
        "job_name": "superarm-isaac-hilserl",
        "log_freq": 10,
        "save_freq": request.checkpoint_frequency,
        "save_checkpoint": True,
        "num_workers": 0,
    }


def write_lerobot_config(request: ReinforcementLearningRequest, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "hilserl_config.json"
    path.write_text(json.dumps(build_lerobot_config(request, output_dir), indent=2) + "\n")
    return path
