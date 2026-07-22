from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from isaacsim_validation.visuals import crop_hand_closeup, image_has_detail


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
