from __future__ import annotations

import pytest

from isaacsim_validation.contracts import (
    ARM_JOINTS,
    FIXED_GRASP_DEGREES,
    HAND_JOINTS,
    PHYSICAL_JOINTS,
    expand_logical_action,
    grasp_to_urdf_targets,
    resolve_grasp_code,
    validate_physical_targets,
)
from lelab.superarm.actions import MOTION_DEGREES, action_to_isaac_targets, resolve_motion_code


def test_grasp_is_quantized_to_three_fixed_motions():
    assert resolve_grasp_code(0.0) == 0.0
    assert resolve_grasp_code(0.25) == 0.5
    assert resolve_grasp_code(0.75) == 1.0
    assert resolve_grasp_code(2.0) == 1.0
    assert resolve_motion_code(0.25) == 0.5
    assert resolve_motion_code(0.75) == 1.0
    assert MOTION_DEGREES == FIXED_GRASP_DEGREES


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.249, 0.0),
        (0.250, 0.5),
        (0.251, 0.5),
        (0.749, 0.5),
        (0.750, 1.0),
        (0.751, 1.0),
    ],
)
def test_lelab_and_isaac_quantizer_boundaries_match(value: float, expected: float):
    assert resolve_motion_code(value) == expected
    assert resolve_grasp_code(value) == expected


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


def test_physical_targets_require_the_exact_thirteen_joint_names():
    shuffled = dict(reversed(list(expand_logical_action([0.1, -0.2, 0.3, -0.4, 0.5, 1.0]).items())))

    validated = validate_physical_targets(shuffled)

    assert tuple(validated) == PHYSICAL_JOINTS
    assert validated["finger1_motor2"] == pytest.approx(1.10)

    with pytest.raises(ValueError, match="exactly 13"):
        validate_physical_targets({"joint_rev_1": 0.0})

    extra = dict(validated)
    extra["unexpected"] = 0.0
    with pytest.raises(ValueError, match="extra=.*unexpected"):
        validate_physical_targets(extra)


def test_physical_targets_reject_non_finite_values():
    targets = expand_logical_action([0.0] * 6)
    targets["finger4_motor2"] = float("nan")

    with pytest.raises(ValueError, match="finite"):
        validate_physical_targets(targets)

    for invalid in (True, "0.0"):
        targets = expand_logical_action([0.0] * 6)
        targets["finger4_motor2"] = invalid
        with pytest.raises(ValueError, match="number"):
            validate_physical_targets(targets)


def test_lelab_isaac_action_wrapper_keeps_six_logical_controls():
    targets = action_to_isaac_targets([2.0, -2.0, 0.3, -0.4, 0.5, 0.8])

    assert tuple(targets) == PHYSICAL_JOINTS
    assert targets["joint_rev_1"] == pytest.approx(1.57)
    assert targets["joint_rev_2"] == pytest.approx(-1.57)
    assert targets["finger1_motor1"] == pytest.approx(0.95)
    assert targets["finger1_motor2"] == pytest.approx(1.10)
