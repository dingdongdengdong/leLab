from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from isaacsim_validation.contracts import ARM_JOINTS, HAND_JOINTS, PHYSICAL_JOINTS
from isaacsim_validation.run_lelab_isaac_e2e import (
    build_joint_snapshot,
    collect_static_visual_evidence,
    evaluate_hand_frames,
    snapshot_matches_command,
    write_hand_gif,
)


def _state(arm_error: float, hand_error: float) -> dict:
    def joints(names, error):
        return {
            name: {"position": float(index), "target": float(index) + error, "error": error}
            for index, name in enumerate(names)
        }

    return {
        "runtime": "isaac_sim",
        "physics_step": 200,
        "command_sequence": 3,
        "arm": joints(ARM_JOINTS, arm_error),
        "hand": joints(HAND_JOINTS, hand_error),
    }


def test_build_joint_snapshot_enforces_exact_thirteen_and_settle_thresholds() -> None:
    snapshot = build_joint_snapshot(_state(0.019, 0.009))

    assert list(snapshot["measured_positions"]) == list(PHYSICAL_JOINTS)
    assert snapshot["max_arm_error_rad"] == pytest.approx(0.019)
    assert snapshot["max_hand_error_rad"] == pytest.approx(0.009)
    assert snapshot["settled"] is True

    unsettled = build_joint_snapshot(_state(0.021, 0.009))
    assert unsettled["settled"] is False


def test_snapshot_match_requires_new_sequence_expected_targets_and_settled_state() -> None:
    expected = {name: float(index) for index, name in enumerate(PHYSICAL_JOINTS)}
    snapshot = build_joint_snapshot(_state(0.0, 0.0))
    snapshot["reported_targets"] = dict(expected)
    snapshot["command_sequence"] = 4

    assert snapshot_matches_command(snapshot, expected, previous_sequence=3) is True
    assert snapshot_matches_command(snapshot, expected, previous_sequence=4) is False
    wrong = dict(expected)
    wrong[PHYSICAL_JOINTS[-1]] += 0.1
    assert snapshot_matches_command(snapshot, wrong, previous_sequence=3) is False
    snapshot["settled"] = False
    assert snapshot_matches_command(snapshot, expected, previous_sequence=3) is False


def test_hand_frame_metrics_require_nonblank_and_adjacent_motion(tmp_path: Path) -> None:
    paths = []
    for index, color in enumerate(((20, 20, 20), (80, 20, 20), (160, 20, 20))):
        path = tmp_path / f"frame-{index}.png"
        image = Image.new("RGB", (32, 32), color)
        image.putpixel((index + 1, index + 1), (255, 255, 255))
        image.save(path)
        paths.append(path)

    metrics = evaluate_hand_frames(paths)

    assert all(frame["nonblank"] for frame in metrics["frames"])
    assert all(value > 0.5 for value in metrics["adjacent_mean_abs_diff"])
    assert metrics["passed"] is True


def test_write_hand_gif_preserves_all_three_frames(tmp_path: Path) -> None:
    paths = []
    for index in range(3):
        path = tmp_path / f"frame-{index}.png"
        Image.new("RGB", (16, 16), (index * 80, 10, 10)).save(path)
        paths.append(path)

    output = tmp_path / "open-half-close.gif"
    write_hand_gif(paths, output)

    with Image.open(output) as image:
        assert image.n_frames == 3
        assert image.size == (16, 16)


def test_collect_static_visual_evidence_keeps_visual_and_live_proof_separate(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for name, color in (
        ("whole", (20, 20, 20)),
        ("open", (30, 20, 20)),
        ("half-close", (90, 20, 20)),
        ("close", (170, 20, 20)),
    ):
        image = Image.new("RGB", (32, 32), color)
        image.putpixel((1, 1), (255, 255, 255))
        image.save(source / f"superarm-isaac60-zip-learning-{name}.png")
    (source / "superarm-isaac60-zip-learning-report.json").write_text(
        '{"status":"PASS","source":{"kind":"validated_distribution"}}\n',
        encoding="utf-8",
    )

    evidence = collect_static_visual_evidence(source, tmp_path / "run")

    assert evidence["proof_category"] == "prevalidated_static_isaac_visuals"
    assert evidence["is_live_session_capture"] is False
    assert len(evidence["hand_frames"]) == 3
    assert all(Path(path).is_file() for path in evidence["hand_frames"])
    assert Path(evidence["whole_frame"]).is_file()
