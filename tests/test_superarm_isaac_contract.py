from __future__ import annotations

import pytest

from isaacsim_validation.contracts import (
    ARM_JOINTS,
    HAND_JOINTS,
    expand_logical_action,
    grasp_to_urdf_targets,
    resolve_grasp_code,
)


def test_grasp_is_quantized_to_three_fixed_motions():
    assert resolve_grasp_code(0.0) == 0.0
    assert resolve_grasp_code(0.25) == 0.5
    assert resolve_grasp_code(0.75) == 1.0
    assert resolve_grasp_code(2.0) == 1.0


def test_fixed_grasp_expands_to_eight_monotonic_urdf_targets():
    opened = grasp_to_urdf_targets(0.0)
    half = grasp_to_urdf_targets(0.5)
    closed = grasp_to_urdf_targets(1.0)

    assert tuple(opened) == HAND_JOINTS
    for joint in HAND_JOINTS:
        assert opened[joint] < half[joint] < closed[joint]
    assert closed["finger1_motor1"] == pytest.approx(0.95)
    assert closed["finger1_motor2"] == pytest.approx(1.10)


def test_six_logical_actions_expand_to_thirteen_urdf_joints():
    expanded = expand_logical_action([0.1, -0.2, 0.3, -0.4, 0.5, 0.5])

    assert tuple(expanded) == (*ARM_JOINTS, *HAND_JOINTS)
    assert [expanded[name] for name in ARM_JOINTS] == [0.1, -0.2, 0.3, -0.4, 0.5]


def test_action_contract_rejects_wrong_width():
    with pytest.raises(ValueError, match="expected six"):
        expand_logical_action([0.0] * 13)
