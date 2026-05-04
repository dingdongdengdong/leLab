"""Job lifecycle and registry for trainings (and, in future, other long-running
work). One JobRunner instance owns one subprocess; the JobRegistry owns the
overall state, including history persisted to disk under outputs/train/."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
import time
from queue import Empty, Queue
from typing import List, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from .training import TrainingRequest

logger = logging.getLogger(__name__)


JobState = Literal["running", "done", "failed", "interrupted"]


class TrainingMetrics(BaseModel):
    current_step: int = 0
    total_steps: int = 0
    current_loss: Optional[float] = None
    current_lr: Optional[float] = None
    grad_norm: Optional[float] = None
    eta_seconds: Optional[float] = None


class LogLine(BaseModel):
    timestamp: float
    message: str


class JobRecord(BaseModel):
    id: str
    name: str
    state: JobState
    config: TrainingRequest
    output_dir: str
    started_at: float
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    metrics: TrainingMetrics = TrainingMetrics()
    runner: Literal["local"] = "local"


@runtime_checkable
class JobRunner(Protocol):
    """Backend interface for running one job. LocalJobRunner is the only impl
    today; remote runners (SSH, Slurm) drop in here later. @runtime_checkable
    lets `isinstance(r, JobRunner)` work in tests / sanity checks."""

    def start(self, job_id: str, config: TrainingRequest, output_dir: str) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def returncode(self) -> Optional[int]: ...
    def stream_log_lines(self) -> List[LogLine]: ...


# tqdm progress: "Training:   1%|▏         | 125/10000 [02:02<2:36:10,  1.05step/s]"
_TQDM_RE = re.compile(
    r"Training:\s*\d+%[^|]*\|[^|]*\|\s*(\d+)/(\d+)\s*\[(?:[\d:]+)<([\d:]+)"
)


def _parse_duration(s: str) -> Optional[float]:
    """Parse tqdm's HH:MM:SS or MM:SS into seconds. Returns None on '?'."""
    parts = s.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    except ValueError:
        return None
    return None


def parse_metrics_into(line: str, metrics: TrainingMetrics) -> None:
    """Update `metrics` in-place from one stdout line.

    Two complementary sources:
      * tqdm progress for current_step + total_steps + ETA (~1s cadence).
      * 'INFO ... step:N smpl:... loss:X grdn:Y lr:Z ...' for loss/lr/grdn
        (only at log_freq cadence, default every 250 steps).
    """
    try:
        tqdm_match = _TQDM_RE.search(line)
        if tqdm_match:
            try:
                metrics.current_step = int(tqdm_match.group(1))
                total = int(tqdm_match.group(2))
                if total > 0:
                    metrics.total_steps = total
                eta = _parse_duration(tqdm_match.group(3))
                if eta is not None:
                    metrics.eta_seconds = eta
            except (ValueError, IndexError):
                pass

        if "step:" in line and "loss:" in line:
            try:
                metrics.current_step = int(line.split("step:")[1].split()[0].replace(",", ""))
            except ValueError:
                pass
            try:
                metrics.current_loss = float(line.split("loss:")[1].split()[0])
            except ValueError:
                pass
            if "lr:" in line:
                try:
                    metrics.current_lr = float(line.split("lr:")[1].split()[0])
                except ValueError:
                    pass
            if "grdn:" in line:
                try:
                    metrics.grad_norm = float(line.split("grdn:")[1].split()[0])
                except ValueError:
                    pass

    except Exception as exc:
        logger.debug("Error parsing log line %r: %s", line, exc)


class LocalJobRunner:
    """Run a training as a local subprocess.

    The runner is single-shot: instantiate a fresh one per job. Lifetime of
    the underlying subprocess is bounded by this object's existence in memory.
    """

    def __init__(self, metrics: TrainingMetrics) -> None:
        self._metrics = metrics
        self._process: Optional[subprocess.Popen] = None
        self._log_queue: "Queue[LogLine]" = Queue()
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(
        self,
        job_id: str,
        config: TrainingRequest,
        output_dir: str,
    ) -> None:
        if self._process is not None:
            raise RuntimeError("LocalJobRunner already started")

        # Build the command via the helper that lives in training.py.
        from .training import build_training_command  # avoid import cycle at module load
        cmd = build_training_command(config, output_dir)
        logger.info("Starting job %s: %s", job_id, " ".join(cmd))

        # PYTHONUNBUFFERED makes the child's stdout flush per line. Without it
        # block-buffering hides log lines from our parser for many seconds.
        child_env = os.environ.copy()
        child_env["PYTHONUNBUFFERED"] = "1"

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
            env=child_env,
        )

        self._monitor_thread = threading.Thread(
            target=self._pump_stdout, name=f"job-{job_id}-stdout", daemon=True
        )
        self._monitor_thread.start()

    def stop(self) -> None:
        if self._process is None or self._process.poll() is not None:
            return
        self._stop_event.set()
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                logger.warning("Subprocess did not terminate in 10s, killing")
                self._process.kill()
                self._process.wait()
        except Exception as exc:
            logger.exception("Error stopping subprocess: %s", exc)

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def returncode(self) -> Optional[int]:
        if self._process is None:
            return None
        return self._process.poll()

    def stream_log_lines(self) -> List[LogLine]:
        """Drain whatever has accumulated since the last call."""
        out: List[LogLine] = []
        try:
            while True:
                out.append(self._log_queue.get_nowait())
        except Empty:
            pass
        return out

    # -- internals --

    def _pump_stdout(self) -> None:
        assert self._process is not None
        try:
            for line in iter(self._process.stdout.readline, ""):
                if self._stop_event.is_set():
                    break
                stripped = line.rstrip()
                if not stripped:
                    continue
                parse_metrics_into(stripped, self._metrics)
                # Cap queue so a chatty subprocess can't grow memory unbounded.
                if self._log_queue.qsize() >= 1000:
                    try:
                        self._log_queue.get_nowait()
                    except Empty:
                        pass
                self._log_queue.put(LogLine(timestamp=time.time(), message=stripped))
        except Exception as exc:
            logger.exception("Error reading subprocess stdout: %s", exc)


import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict


_PERSIST_THROTTLE_SECONDS = 1.0


def _generate_job_id(policy_type: str, dataset_repo_id: str) -> str:
    """Build a sortable, collision-free job id from policy type and dataset slug."""
    from .training import _SLUG_RE
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dataset_slug = _SLUG_RE.sub("_", dataset_repo_id).strip("_") or "dataset"
    return f"{policy_type}_{dataset_slug}_{timestamp}"


def _job_dir(output_root: Path, job_id: str) -> Path:
    return output_root / job_id


def _job_meta_path(output_root: Path, job_id: str) -> Path:
    return _job_dir(output_root, job_id) / "job.json"


class JobAlreadyRunningError(Exception):
    """Raised when start() is called while another local job is running."""


class JobNotFoundError(Exception):
    """Raised when a lookup hits an unknown id."""


class JobNotRunningError(Exception):
    """Raised when stop() is called on a non-running job."""


class JobRegistry:
    """Owns the registry of training jobs and their persistence.

    On instantiation, scans outputs/train/ for existing job.json files and
    rewrites any record marked 'running' to 'interrupted' (since this is a
    fresh lelab process — we no longer own those subprocesses).
    """

    def __init__(self, output_root: Path) -> None:
        self._output_root = output_root
        self._output_root.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._records: Dict[str, JobRecord] = {}
        self._runners: Dict[str, LocalJobRunner] = {}
        self._last_persist_at: Dict[str, float] = {}

        self._stop_watchdog = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None

        self._load_from_disk()
        self._start_watchdog()

    # -- public API --

    def list(self, limit: int = 10) -> List[JobRecord]:
        with self._lock:
            records = list(self._records.values())
        records.sort(key=lambda r: r.started_at, reverse=True)
        return records[:limit]

    def get(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._records.get(job_id)
        if record is None:
            raise JobNotFoundError(job_id)
        return record

    def start(self, config: TrainingRequest) -> JobRecord:
        with self._lock:
            for r in self._records.values():
                if r.state == "running":
                    raise JobAlreadyRunningError(r.id)

            job_id = _generate_job_id(config.policy_type, config.dataset_repo_id)
            output_dir = str(_job_dir(self._output_root, job_id))
            name = f"{config.policy_type.upper()} · {config.dataset_repo_id}"
            record = JobRecord(
                id=job_id,
                name=name,
                state="running",
                config=config,
                output_dir=output_dir,
                started_at=time.time(),
            )

            _job_dir(self._output_root, job_id).mkdir(parents=True, exist_ok=True)
            self._records[job_id] = record
            self._persist(record, force=True)

            runner = LocalJobRunner(record.metrics)
            try:
                runner.start(job_id, config, output_dir)
            except Exception as exc:
                logger.exception("Failed to start subprocess for job %s", job_id)
                record.state = "failed"
                record.ended_at = time.time()
                record.error_message = f"Failed to spawn subprocess: {exc}"
                self._persist(record, force=True)
                raise

            self._runners[job_id] = runner
            return record

    def stop(self, job_id: str) -> JobRecord:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            runner = self._runners.get(job_id)
        if record.state != "running" or runner is None:
            raise JobNotRunningError(job_id)
        runner.stop()
        # The watchdog will finalise the record (state, ended_at, exit_code).
        # Wait briefly so the caller sees the new state in the response.
        for _ in range(20):
            time.sleep(0.1)
            with self._lock:
                if record.state != "running":
                    return record
        return record

    def drain_logs(self, job_id: str) -> List[LogLine]:
        with self._lock:
            if job_id not in self._records:
                raise JobNotFoundError(job_id)
            runner = self._runners.get(job_id)
        if runner is None:
            return []
        return runner.stream_log_lines()

    def delete(self, job_id: str) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                raise JobNotFoundError(job_id)
            if record.state == "running":
                raise JobNotRunningError(job_id)
            self._records.pop(job_id, None)
            self._runners.pop(job_id, None)
            self._last_persist_at.pop(job_id, None)
        try:
            shutil.rmtree(_job_dir(self._output_root, job_id))
        except FileNotFoundError:
            pass

    def shutdown(self) -> None:
        """For tests / orderly process exit. Not wired to FastAPI lifespan today."""
        self._stop_watchdog.set()

    # -- internals --

    def _load_from_disk(self) -> None:
        for job_dir in self._output_root.glob("*/"):
            meta = job_dir / "job.json"
            if not meta.exists():
                continue
            try:
                data = json.loads(meta.read_text())
                record = JobRecord.model_validate(data)
            except Exception as exc:
                logger.warning("Skipping malformed job.json at %s: %s", meta, exc)
                continue
            if record.state == "running":
                record.state = "interrupted"
                if record.ended_at is None:
                    record.ended_at = time.time()
                self._write_meta(record)
            self._records[record.id] = record

    def _start_watchdog(self) -> None:
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, name="job-registry-watchdog", daemon=True
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        while not self._stop_watchdog.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.exception("Watchdog tick failed: %s", exc)
            self._stop_watchdog.wait(1.0)

    def _tick(self) -> None:
        with self._lock:
            running_ids = [jid for jid, r in self._records.items() if r.state == "running"]

        for jid in running_ids:
            with self._lock:
                runner = self._runners.get(jid)
                record = self._records.get(jid)
            if runner is None or record is None:
                continue
            if runner.is_running():
                # Persist metric snapshot at most once per second.
                self._persist(record, force=False)
                continue

            # Subprocess exited since the last tick. Finalise.
            rc = runner.returncode()
            with self._lock:
                record.state = "done" if rc == 0 else "failed"
                record.ended_at = time.time()
                record.exit_code = rc
                if rc != 0 and record.error_message is None:
                    record.error_message = f"Subprocess exited with code {rc}"
                self._runners.pop(jid, None)
            self._persist(record, force=True)

    def _persist(self, record: JobRecord, force: bool) -> None:
        now = time.time()
        last = self._last_persist_at.get(record.id, 0.0)
        if not force and (now - last) < _PERSIST_THROTTLE_SECONDS:
            return
        self._last_persist_at[record.id] = now
        self._write_meta(record)

    def _write_meta(self, record: JobRecord) -> None:
        path = _job_meta_path(self._output_root, record.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2))


# Module-level singleton. The output root is the project's outputs/train/.
_DEFAULT_OUTPUT_ROOT = Path("outputs/train")
job_registry = JobRegistry(_DEFAULT_OUTPUT_ROOT)

__all__ = [
    "JobState",
    "TrainingMetrics",
    "LogLine",
    "JobRecord",
    "JobRunner",
    "LocalJobRunner",
    "JobRegistry",
    "JobAlreadyRunningError",
    "JobNotFoundError",
    "JobNotRunningError",
    "job_registry",
    "parse_metrics_into",
]
