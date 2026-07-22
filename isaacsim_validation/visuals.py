"""Visual-evidence helpers shared by host tests and the Isaac runner."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageStat

DIRECT_CAMERA_METHODS = frozenset({"isaacsim_camera_rgba", "replicator_render_product"})
GRASP_FRAME_NAMES = ("open", "half_close", "close")


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
