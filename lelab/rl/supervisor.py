"""Lifecycle supervisor for learner-first LeRobot SAC plus managed Isaac actor."""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from lelab.superarm.isaac_runtime import IsaacSimRuntime

from .config import ReinforcementLearningRequest
from .runtime_config import write_lerobot_config


def _wait_port(port: int, process: subprocess.Popen, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"learner exited before gRPC readiness ({process.returncode})")
        with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return
        time.sleep(0.2)
    raise TimeoutError(f"learner gRPC port {port} was not ready in {timeout}s")


def _pump(label: str, process: subprocess.Popen) -> None:
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{label}] {line.rstrip()}", flush=True)


def run(request: ReinforcementLearningRequest, output_dir: Path) -> int:
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    runtime = IsaacSimRuntime(
        request.distribution_zip,
        port=request.bridge_port,
        expected_sha256=request.distribution_sha256,
        session_root=output_dir / "isaac",
    )
    children: list[subprocess.Popen] = []
    try:
        print('LELAB_RL_METRIC {"learner_status":"starting","isaac_status":"starting"}', flush=True)
        runtime.connect()
        if runtime._client is None:  # guarded handoff of the authenticated bridge
            raise RuntimeError("Isaac bridge client unavailable after startup")
        token = runtime._configured_token or runtime._token_path.read_text().strip()
        runtime._client.close()
        runtime._client = None
        runtime._connected = False
        config_path = write_lerobot_config(request, output_dir)
        env = os.environ.copy()
        env.update({
            "LELAB_RL_BRIDGE_TOKEN": token,
            "LELAB_RL_FRAME_ROOT": str(runtime.run_dir),
            "LELAB_RL_BRIDGE_PORT": str(request.bridge_port),
            "LELAB_RL_EPISODE_LENGTH_STEPS": str(request.episode_length_steps),
            "PYTHONUNBUFFERED": "1",
        })
        learner = subprocess.Popen(
            [sys.executable, "-m", "lerobot.rl.learner", "--config_path", str(config_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
        )
        children.append(learner)
        threading.Thread(target=_pump, args=("learner", learner), daemon=True).start()
        _wait_port(request.learner_port, learner)
        print('LELAB_RL_METRIC {"learner_status":"ready","actor_status":"starting","isaac_status":"ready"}', flush=True)
        actor = subprocess.Popen(
            [sys.executable, "-m", "lelab.rl.actor", "--config_path", str(config_path)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
        )
        children.append(actor)
        threading.Thread(target=_pump, args=("actor", actor), daemon=True).start()
        print('LELAB_RL_METRIC {"actor_status":"running"}', flush=True)
        while not stop.wait(0.25):
            if actor.poll() is not None or learner.poll() is not None:
                return actor.returncode or learner.returncode or 0
        return 130
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in reversed(children):
            with contextlib.suppress(subprocess.TimeoutExpired):
                child.wait(timeout=8)
            if child.poll() is None:
                child.kill()
        with contextlib.suppress(Exception):
            runtime.close()
        print('LELAB_RL_METRIC {"actor_status":"stopped","learner_status":"stopped","isaac_status":"stopped"}', flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request-json", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    request = ReinforcementLearningRequest.model_validate_json(Path(args.request_json).read_text())
    raise SystemExit(run(request, Path(args.output_dir)))


if __name__ == "__main__":
    main()
