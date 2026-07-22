"""Visual-evidence helpers shared by host tests and the Isaac runner."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageStat


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
