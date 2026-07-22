from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from isaacsim_validation.visuals import (
    crop_hand_closeup,
    image_has_detail,
    validate_direct_grasp_frames,
)


def test_blank_frame_is_rejected(tmp_path: Path):
    frame = tmp_path / "blank.png"
    Image.new("RGB", (1280, 720), (210, 210, 210)).save(frame)

    assert not image_has_detail(frame)


def test_hand_crop_is_derived_from_visible_isaac_frame(tmp_path: Path):
    source = tmp_path / "whole.png"
    target = tmp_path / "hand.png"
    frame = Image.new("RGB", (1280, 720), (210, 210, 210))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((520, 40, 760, 300), fill=(40, 60, 80))
    frame.save(source)

    evidence = crop_hand_closeup(source, target)

    assert image_has_detail(target)
    assert evidence["method"] == "crop_from_whole_robot_isaac_frame"
    assert evidence["source"] == str(source)


def test_hand_crop_rejects_blank_source(tmp_path: Path):
    source = tmp_path / "blank.png"
    Image.new("RGB", (1280, 720), (210, 210, 210)).save(source)

    with pytest.raises(RuntimeError, match="source frame has no visible detail"):
        crop_hand_closeup(source, tmp_path / "hand.png")


def test_direct_grasp_sequence_requires_three_changed_camera_frames(tmp_path: Path):
    frames = []
    for index, name in enumerate(("open", "half_close", "close")):
        path = tmp_path / f"hand_{name}.png"
        frame = Image.new("RGB", (320, 240), (210, 210, 210))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((80 + index * 20, 40, 160 + index * 20, 190), fill=(30, 60, 90))
        frame.save(path)
        frames.append(
            {
                "name": name,
                "path": str(path),
                "method": "replicator_render_product",
            }
        )

    result = validate_direct_grasp_frames(frames)

    assert result["passed"] is True
    assert result["frame_names"] == ["open", "half_close", "close"]
    assert min(result["adjacent_rms_difference"]) > 1.0


def test_direct_grasp_sequence_rejects_crop_evidence(tmp_path: Path):
    path = tmp_path / "hand.png"
    frame = Image.new("RGB", (320, 240), (210, 210, 210))
    ImageDraw.Draw(frame).rectangle((80, 40, 160, 190), fill=(30, 60, 90))
    frame.save(path)
    frames = [
        {"name": name, "path": str(path), "method": "crop_from_whole_robot_isaac_frame"}
        for name in ("open", "half_close", "close")
    ]

    with pytest.raises(RuntimeError, match="direct camera"):
        validate_direct_grasp_frames(frames)


def test_direct_grasp_sequence_rejects_static_visuals(tmp_path: Path):
    frames = []
    for name in ("open", "half_close", "close"):
        path = tmp_path / f"hand_{name}.png"
        frame = Image.new("RGB", (320, 240), (210, 210, 210))
        ImageDraw.Draw(frame).rectangle((80, 40, 160, 190), fill=(30, 60, 90))
        frame.save(path)
        frames.append({"name": name, "path": str(path), "method": "replicator_render_product"})

    with pytest.raises(RuntimeError, match="did not visibly change"):
        validate_direct_grasp_frames(frames)
