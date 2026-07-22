"""Shared predicates for LeLab SuperArm simulation backends."""

SUPERARM_BACKENDS = frozenset({"superarm_mujoco", "superarm_isaac"})


def is_superarm_backend(value: object) -> bool:
    return value in SUPERARM_BACKENDS
