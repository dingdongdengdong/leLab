"""Truthful website guide for the staged SuperArm leader/follower rollout."""

from __future__ import annotations

from typing import Any

ACTION_CONTRACT = "five arm radians plus one fixed AmazingHand grasp code"


def build_control_paths() -> list[dict[str, Any]]:
    """Describe implemented and preparation-only website control paths."""

    return [
        {
            "id": "manual_to_sim",
            "order": 1,
            "title": "Manual leader (sliders) to software follower",
            "leader": "Manual Web Leader sliders",
            "follower": "SuperArm + AmazingHand simulation",
            "simulation_targets": ["MuJoCo", "Isaac Sim 6.0"],
            "website_status": "available",
            "entry_route": "/manual-leader",
            "recording_input_mode": "manual",
            "action_contract": ACTION_CONTRACT,
            "steps": [
                "Select the SuperArm + AmazingHand MuJoCo or Isaac Sim robot on the dashboard.",
                "Open Manual Web Leader for direct control, or open Record and select Manual Web Leader for an episode.",
                "Start the simulation follower before sending any slider command.",
                "Move one arm slider at a time, then test open, half-close, and close as whole-hand motions.",
                "Stop the follower and inspect the six logical action values before continuing to a physical leader.",
            ],
            "acceptance": [
                "five measured arm values update",
                "one grasp code selects a fixed hand motion",
                "no real motor bus is opened",
            ],
        },
        {
            "id": "so101_to_sim",
            "order": 2,
            "title": "Real SO-101 leader to software follower",
            "leader": "LeRobot SO101Leader over Feetech serial",
            "follower": "SuperArm + AmazingHand simulation",
            "simulation_targets": ["MuJoCo first", "Isaac Sim 6.0 after the dry run"],
            "website_status": "available",
            "entry_route": "/calibration",
            "recording_input_mode": "so101",
            "action_contract": ACTION_CONTRACT,
            "steps": [
                "Connect the SO-101 leader only; keep the real SuperArm follower unpowered.",
                "Calibrate the leader on /calibration as Leader (Teleoperator) and keep its calibration ID.",
                "Select a SuperArm simulation record, open Record, and choose SO101 Leader.",
                "Enter the leader serial port and calibration ID, then record a short MuJoCo dry-run episode.",
                "Verify joint_rev_1 through joint_rev_5 and the quantized AmazingHand grasp code before trying Isaac Sim.",
            ],
            "acceptance": [
                "SO-101 calibration loads without recalibration",
                "all five mapped arm joints follow with the configured signs and offsets",
                "SO-101 gripper selects open, half-close, or close rather than eight independent hand joints",
            ],
        },
        {
            "id": "so101_to_real",
            "order": 3,
            "title": "Real SO-101 leader to real SuperArm follower",
            "leader": "LeRobot SO101Leader over Feetech serial",
            "follower": "DM4340P CAN arm plus AmazingHand SCS0009 serial",
            "simulation_targets": [],
            "website_status": "preparation_only",
            "entry_route": "/hardware-setup",
            "recording_input_mode": None,
            "action_contract": ACTION_CONTRACT,
            "steps": [
                "Complete the five-joint SuperArm calibration and replace every invalid CAN/configuration placeholder with measured values.",
                "Validate the DM4340P arm and AmazingHand serial hand independently with torque-limited bench tests.",
                "Confirm limits, direction, zero offsets, gains, emergency stop, watchdog hold, and readback for all five arm joints plus the grasp command.",
                "Do not select this path for recording yet: the website does not register the real SuperArm adapter as a follower backend.",
                "Enable website recording only after a dedicated hardware robot record, connection flow, and hardware-in-loop acceptance suite are implemented.",
            ],
            "acceptance": [
                "real follower backend is registered and selectable",
                "torque-limited motion and readback pass on hardware",
                "emergency stop and command-timeout hold pass before recording",
            ],
            "blocker": (
                "The protocol-safe hardware adapter and calibration/readiness pages exist, "
                "but website teleoperation/recording does not yet register it as a real follower backend."
            ),
        },
    ]
