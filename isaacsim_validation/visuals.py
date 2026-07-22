"""Visual-evidence helpers shared by host tests and the Isaac runner."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

DIRECT_CAMERA_METHODS = frozenset(
    {
        "isaacsim_camera_rgba",
        "replicator_render_product",
        "static_replicator_from_physics_snapshot",
    }
)
GRASP_FRAME_NAMES = ("open", "half_close", "close")
EXPECTED_PASSIVE_LINKAGE_PART_COUNT = 88
EXPECTED_PASSIVE_LINKAGE_PARTS_PER_FINGER = 22
EXPECTED_PASSIVE_LINKAGE_FINGERS = (1, 2, 3, 4)
FORBIDDEN_PASSIVE_LINKAGE_NAME_FRAGMENTS = ("_shell", "proximal_shell", "distal_shell")
FORBIDDEN_PASSIVE_LINKAGE_SCHEMA_FRAGMENTS = (
    "physics",
    "rigidbody",
    "rigid_body",
    "collision",
    "collider",
    "mass",
    "joint",
)
OPEN_MOTOR_TARGETS = (0.05, 0.02)
CLOSE_MOTOR_TARGETS = (0.95, 1.10)
MOTOR_STATE_TOLERANCE = 0.08
TRANSFORM_EPSILON = 1e-7


def zip_learning_visual_boundary() -> str:
    """Return the exact visual-proof boundary for ZIP passive-linkage renders."""

    return (
        "The hand stage uses source closed-loop-informed, shell-free structural visuals whose follower "
        "poses follow measured Isaac joints in exported physics snapshots. Frame cores and passive "
        "linkages move with the measured eight hand DOFs; rounded outer shells disabled. This is source "
        "structural visual evidence only: no closed-loop PhysX, contact, or hardware claim is made."
    )


def image_has_detail(path: Path, *, minimum_stddev: float = 2.0) -> bool:
    """Return whether an image contains more than a nearly uniform background."""
    if not path.is_file() or path.stat().st_size == 0:
        return False
    with Image.open(path).convert("RGB") as frame:
        return max(ImageStat.Stat(frame).stddev) >= minimum_stddev


def crop_hand_closeup(source: Path, target: Path) -> dict:
    """Create a labeled close-up crop from the deterministic whole-robot frame."""
    if not image_has_detail(source):
        raise RuntimeError(f"source frame has no visible detail: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source).convert("RGB") as frame:
        width, height = frame.size
        crop_box = (
            round(width * 0.30),
            0,
            round(width * 0.70),
            round(height * 0.48),
        )
        closeup = frame.crop(crop_box)
        closeup.thumbnail((1280, 720), Image.Resampling.LANCZOS)
        closeup.save(target)
    if not image_has_detail(target):
        raise RuntimeError(f"hand close-up has no visible detail: {target}")
    return {
        "path": str(target),
        "bytes": target.stat().st_size,
        "method": "crop_from_whole_robot_isaac_frame",
        "source": str(source),
        "crop_box_pixels": list(crop_box),
    }


def _rms_difference(left: Path, right: Path) -> float:
    with Image.open(left).convert("RGB") as left_frame, Image.open(right).convert("RGB") as right_frame:
        if left_frame.size != right_frame.size:
            raise RuntimeError("direct grasp frames must use the same camera resolution")
        histogram = ImageStat.Stat(ImageChops.difference(left_frame, right_frame))
        return max(float(value) for value in histogram.rms)


def validate_direct_grasp_frames(
    frames: list[dict],
    *,
    minimum_adjacent_rms: float = 1.0,
) -> dict:
    """Require visible open/half/close frames captured directly from one camera."""
    names = [frame.get("name") for frame in frames]
    if names != list(GRASP_FRAME_NAMES):
        raise RuntimeError(f"expected direct grasp frames {GRASP_FRAME_NAMES}, got {names}")
    if any(frame.get("method") not in DIRECT_CAMERA_METHODS for frame in frames):
        raise RuntimeError("grasp evidence must come from a direct camera, not a crop")

    paths = [Path(str(frame.get("path", ""))) for frame in frames]
    if len(set(paths)) != len(paths):
        raise RuntimeError("each grasp state must have its own direct camera frame")
    if any(not image_has_detail(path) for path in paths):
        raise RuntimeError("one or more direct grasp frames has no visible detail")

    differences = [_rms_difference(paths[index], paths[index + 1]) for index in range(2)]
    if any(value < minimum_adjacent_rms for value in differences):
        raise RuntimeError(
            f"hand visuals did not visibly change between every adjacent grasp state: RMS={differences}"
        )
    return {
        "passed": True,
        "frame_names": names,
        "adjacent_rms_difference": differences,
        "minimum_adjacent_rms": minimum_adjacent_rms,
    }


def validate_passive_linkage_visual_summary(summary: Mapping) -> dict:
    """Validate a shell-free passive-linkage visual follower summary.

    The input is deliberately plain data so default CI can exercise the
    structural contract without importing pxr or Isaac Sim.
    """

    parts = list(summary.get("parts") or ())
    if len(parts) != EXPECTED_PASSIVE_LINKAGE_PART_COUNT:
        raise RuntimeError(
            f"passive linkage visuals must contain {EXPECTED_PASSIVE_LINKAGE_PART_COUNT} parts, "
            f"found {len(parts)}"
        )
    if summary.get("visual_part_count", EXPECTED_PASSIVE_LINKAGE_PART_COUNT) != (
        EXPECTED_PASSIVE_LINKAGE_PART_COUNT
    ):
        raise RuntimeError("passive linkage visual_part_count must be 88")

    per_finger: Counter[int] = Counter()
    physics_schema_count = 0
    for part in parts:
        finger = int(part.get("finger", -1))
        per_finger[finger] += 1
        _reject_shell_name(part)
        if _has_forbidden_physics_schema(part):
            physics_schema_count += 1

    expected_per_finger = dict.fromkeys(
        EXPECTED_PASSIVE_LINKAGE_FINGERS,
        EXPECTED_PASSIVE_LINKAGE_PARTS_PER_FINGER,
    )
    actual_per_finger = dict(sorted(per_finger.items()))
    if actual_per_finger != expected_per_finger:
        raise RuntimeError(f"passive linkage visuals must contain 22 parts per finger: {actual_per_finger}")
    declared_per_finger = summary.get("parts_per_finger")
    if declared_per_finger is not None:
        declared = _normalize_parts_per_finger(declared_per_finger)
        if declared != expected_per_finger:
            raise RuntimeError(f"passive linkage visuals must declare 22 parts per finger: {declared}")
    _require_zero(summary, "excluded_shell_visual_count", "shell visuals")
    _require_zero(summary, "shell_visual_count", "shell visuals")
    _require_zero(summary, "added_rigid_body_count", "physics rigid bodies")
    _require_zero(summary, "added_collider_count", "physics collision schemas")
    _require_zero(summary, "added_joint_count", "physics joint schemas")
    _require_zero(summary, "added_mass_count", "physics mass schemas")
    _require_zero(summary, "validated_added_physics_prim_count", "physics schemas")
    _require_zero(summary, "physics_schema_count", "physics/collision/joint schemas")
    if physics_schema_count:
        raise RuntimeError("passive linkage followers contain physics/collision/joint schemas")

    return {
        "passed": True,
        "visual_part_count": EXPECTED_PASSIVE_LINKAGE_PART_COUNT,
        "parts_per_finger": expected_per_finger,
        "shell_visual_count": 0,
        "physics_schema_count": 0,
    }


def validate_passive_linkage_motion_sequence(states: Sequence[Mapping]) -> dict:
    """Require open/half/close follower transforms to move every finger."""

    names = [state.get("name") for state in states]
    if names != list(GRASP_FRAME_NAMES):
        raise RuntimeError(f"expected passive linkage states {GRASP_FRAME_NAMES}, got {names}")
    contracts = [_contract_for_state(state) for state in states]
    summaries = [validate_passive_linkage_visual_summary(contract) for contract in contracts]
    changed = {
        finger: _finger_transform_changed(contracts[0], contracts[-1], finger)
        for finger in EXPECTED_PASSIVE_LINKAGE_FINGERS
    }
    unchanged = [f"finger{finger}" for finger, did_change in changed.items() if not did_change]
    if unchanged:
        raise RuntimeError(f"passive linkage followers did not move for {', '.join(unchanged)}")
    return {
        "passed": True,
        "frame_names": names,
        "per_snapshot": summaries,
        "changed_fingers": [finger for finger, did_change in changed.items() if did_change],
    }


def validate_independent_finger_linkage_sequence(open_state: Mapping, states: Sequence[Mapping]) -> dict:
    """Require each independent snapshot to close only its target finger.

    The measured motor-state check prevents visual-only fabrication: each
    per-finger evidence state must carry readback values from Isaac.
    """

    open_contract = _contract_for_state(open_state)
    validate_passive_linkage_visual_summary(open_contract)
    seen: set[int] = set()
    entries = []
    for state in states:
        target = int(state.get("target_finger", -1))
        if target not in EXPECTED_PASSIVE_LINKAGE_FINGERS:
            raise RuntimeError(f"invalid target_finger for independent visual evidence: {target}")
        if target in seen:
            raise RuntimeError(f"duplicate independent visual evidence for finger{target}")
        seen.add(target)
        contract = _contract_for_state(state)
        validate_passive_linkage_visual_summary(contract)
        _validate_independent_measured_state(state, target)
        changed = [
            finger
            for finger in EXPECTED_PASSIVE_LINKAGE_FINGERS
            if _finger_transform_changed(open_contract, contract, finger)
        ]
        if changed != [target]:
            bad = ", ".join(f"finger{finger}" for finger in changed if finger != target)
            if not _finger_transform_changed(open_contract, contract, target):
                bad = f"finger{target}"
            raise RuntimeError(
                f"independent finger{target} visual evidence changed unexpected followers: {bad or changed}"
            )
        entries.append({"target_finger": target, "changed_fingers": changed})
    if seen != set(EXPECTED_PASSIVE_LINKAGE_FINGERS):
        raise RuntimeError(f"expected independent visual evidence for fingers 1..4, got {sorted(seen)}")
    return {"passed": True, "states": entries}


def _contract_for_state(state: Mapping) -> Mapping:
    contract = state.get("passive_linkage_contract")
    if not isinstance(contract, Mapping):
        raise RuntimeError(f"missing passive_linkage_contract for state {state.get('name')!r}")
    return contract


def _reject_shell_name(part: Mapping) -> None:
    fields = (
        part.get("source_prim", ""),
        part.get("reference_prim", ""),
        part.get("xform_path", ""),
        part.get("name", ""),
        part.get("path", ""),
    )
    joined = " ".join(str(value).lower() for value in fields)
    if any(fragment in joined for fragment in FORBIDDEN_PASSIVE_LINKAGE_NAME_FRAGMENTS):
        raise RuntimeError(f"passive linkage visuals include an excluded shell source: {joined}")


def _has_forbidden_physics_schema(part: Mapping) -> bool:
    schema_values = [
        part.get("type_name", ""),
        *(part.get("applied_schemas") or ()),
        *(part.get("schemas") or ()),
        *(part.get("applied_api_schemas") or ()),
        *(part.get("api_schemas") or ()),
    ]
    joined = " ".join(str(value).replace("API", "").lower() for value in schema_values)
    return any(fragment in joined for fragment in FORBIDDEN_PASSIVE_LINKAGE_SCHEMA_FRAGMENTS)


def _require_zero(summary: Mapping, key: str, description: str) -> None:
    value = int(summary.get(key, 0) or 0)
    if value != 0:
        raise RuntimeError(f"passive linkage followers contain {description}: {value}")


def _normalize_parts_per_finger(value) -> dict[int, int]:
    if isinstance(value, Mapping):
        return {int(finger): int(count) for finger, count in value.items()}
    raise RuntimeError("passive linkage visuals must declare 22 parts per finger")


def _finger_transform_changed(left: Mapping, right: Mapping, finger: int) -> bool:
    left_parts = _parts_by_finger_and_source(left, finger)
    right_parts = _parts_by_finger_and_source(right, finger)
    if left_parts.keys() != right_parts.keys():
        raise RuntimeError(f"passive linkage part keys changed for finger{finger}")
    return any(
        _transform_distance(left_parts[key], right_parts[key]) > TRANSFORM_EPSILON for key in left_parts
    )


def _parts_by_finger_and_source(contract: Mapping, finger: int) -> dict[int, Mapping]:
    return {
        int(part["source_index"]): part
        for part in contract.get("parts", ())
        if int(part.get("finger", -1)) == finger
    }


def _transform_distance(left: Mapping, right: Mapping) -> float:
    left_values = [*_finite_vector(left.get("translate"), 3), *_finite_vector(left.get("orient"), 4)]
    right_values = [*_finite_vector(right.get("translate"), 3), *_finite_vector(right.get("orient"), 4)]
    return max(
        abs(left_value - right_value)
        for left_value, right_value in zip(left_values, right_values, strict=True)
    )


def _finite_vector(value, length: int) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != length:
        raise RuntimeError(f"expected finite transform vector of length {length}")
    vector = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in vector):
        raise RuntimeError("passive linkage transform contains a non-finite value")
    return vector


def _validate_independent_measured_state(state: Mapping, target_finger: int) -> None:
    measured = state.get("measured")
    if not isinstance(measured, Mapping):
        raise RuntimeError(f"independent finger{target_finger} evidence is missing measured Isaac joints")
    for finger in EXPECTED_PASSIVE_LINKAGE_FINGERS:
        motor1 = _finite_measured(measured, f"finger{finger}_motor1")
        motor2 = _finite_measured(measured, f"finger{finger}_motor2")
        if finger == target_finger:
            if (
                abs(motor1 - CLOSE_MOTOR_TARGETS[0]) > MOTOR_STATE_TOLERANCE
                or abs(motor2 - CLOSE_MOTOR_TARGETS[1]) > MOTOR_STATE_TOLERANCE
            ):
                raise RuntimeError(f"finger{finger} is not at measured close values")
        elif (
            abs(motor1 - OPEN_MOTOR_TARGETS[0]) > MOTOR_STATE_TOLERANCE
            or abs(motor2 - OPEN_MOTOR_TARGETS[1]) > MOTOR_STATE_TOLERANCE
        ):
            raise RuntimeError(f"finger{finger} is not preserved at measured open values")


def _finite_measured(measured: Mapping, key: str) -> float:
    value = float(measured[key])
    if not math.isfinite(value):
        raise RuntimeError(f"measured Isaac joint {key} is not finite")
    return value
