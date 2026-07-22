"""Stable Isaac Sim URDF import settings for the learning asset package."""

from __future__ import annotations


def urdf_import_settings() -> dict[str, bool | float | str]:
    """Return settings that produce a fixed-base, layered SimReady package."""
    return {
        "fix_base": True,
        "joint_drive_type": "force",
        "joint_target_type": "position",
        "override_joint_stiffness": 180.0,
        "override_joint_damping": 18.0,
        "run_asset_transformer": True,
        "run_multi_physics_conversion": True,
    }
