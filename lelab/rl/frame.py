"""Validated atomic frame-file descriptors for host/container exchange."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .contracts import IMAGE_SHAPE, validate_rgb_frame


def validate_frame_descriptor(descriptor: dict[str, Any], allowed_root: str | Path) -> Path:
    if set(descriptor) != {"path", "width", "height", "channels", "sequence"}:
        raise ValueError("frame descriptor has unexpected fields")
    if (descriptor["width"], descriptor["height"], descriptor["channels"]) != (
        IMAGE_SHAPE[1],
        IMAGE_SHAPE[0],
        IMAGE_SHAPE[2],
    ):
        raise ValueError("frame descriptor dimensions do not match the workspace camera")
    if isinstance(descriptor["sequence"], bool) or not isinstance(descriptor["sequence"], int):
        raise ValueError("frame descriptor sequence must be an integer")
    root = Path(allowed_root).resolve(strict=True)
    path = Path(descriptor["path"])
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=True)
    if resolved.is_symlink() or not resolved.is_file() or resolved.parent != root:
        raise ValueError("frame path must be a direct regular file beneath the allowed root")
    return resolved


def read_frame(descriptor: dict[str, Any], allowed_root: str | Path) -> np.ndarray:
    path = validate_frame_descriptor(descriptor, allowed_root)
    with Image.open(path) as image:
        frame = np.asarray(image.convert("RGB"), dtype=np.uint8)
    return validate_rgb_frame(frame)


def write_frame_atomic(frame: np.ndarray, destination: str | Path) -> Path:
    array = validate_rgb_frame(frame)
    path = Path(destination)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    Image.fromarray(array, mode="RGB").save(tmp, format="PNG")
    os.replace(tmp, path)
    return path
