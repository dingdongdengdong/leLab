"""Read-only host readiness checks for local SuperArm Isaac RL."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

from .config import DEFAULT_DISTRIBUTION_SHA256

DEFAULT_RL_IMAGE = "nvcr.io/nvidia/isaac-sim:6.0.1"
DEFAULT_RL_DISPLAY = ":100"
_DISPLAY_RE = re.compile(r":(?P<number>\d+)(?:\.\d+)?")


def _port_free(port: int) -> bool:
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False


def _display_socket_available(display: str) -> bool:
    match = _DISPLAY_RE.fullmatch(display)
    if match is None:
        return False
    return Path(f"/tmp/.X11-unix/X{match.group('number')}").is_socket()


def check_rl_readiness(
    distribution_zip: str,
    learner_port: int = 50051,
    bridge_port: int = 8765,
    *,
    image: str | None = None,
    display: str | None = None,
) -> dict:
    image = image or os.environ.get("ISAAC_SIM_RL_IMAGE", DEFAULT_RL_IMAGE)
    display = display or os.environ.get("ISAAC_SIM_RL_DISPLAY", DEFAULT_RL_DISPLAY)
    path = Path(distribution_zip).expanduser()
    checksum = None
    if path.is_file():
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        checksum = digest.hexdigest()
    checks = {
        "nvidia_driver": shutil.which("nvidia-smi") is not None,
        "docker": shutil.which("docker") is not None,
        "isaac_sim_6_0_1_image": False,
        "rl_x11_display": _display_socket_available(display),
        "validated_distribution": checksum == DEFAULT_DISTRIBUTION_SHA256,
        "hilserl_dependencies": all(
            importlib.util.find_spec(name) is not None
            for name in ("grpc", "transformers", "lerobot", "gymnasium")
        ),
        "learner_port_free": _port_free(learner_port),
        "bridge_port_free": _port_free(bridge_port),
    }
    if checks["docker"]:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        checks["isaac_sim_6_0_1_image"] = result.returncode == 0
    return {
        "ready": all(checks.values()),
        "checks": checks,
        "distribution_zip": str(path),
        "distribution_sha256": checksum,
        "isaac_image": image,
        "display": display,
    }
