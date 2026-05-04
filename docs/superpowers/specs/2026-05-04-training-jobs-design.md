# Training Jobs — Design

**Date:** 2026-05-04
**Status:** Approved

## Problem

The Training page today is a single-shot screen tied to a global `TrainingManager` singleton. While a training subprocess runs in the background it is invisible from anywhere else in the app — the moment the user navigates to Landing they lose access to start/stop, progress, and logs unless they go back to the Training page. They cannot see history of past runs without browsing `outputs/train/` on disk. Multiple trainings can't coexist as first-class records (the singleton allows only one). And while a local job is "background" by virtue of being a separate subprocess, the UX treats training as foreground because the Monitoring tab is the only entry point.

## Goal

- Treat each training run as a persistent first-class **Job** with a stable id, on-disk metadata, and lifecycle states.
- Surface running and recently-finished jobs as cards on Landing so the user can see them at a glance, click to inspect, and stop or dismiss them.
- Free the user to do non-training things (recording, calibration, replay) while a training runs in the background, without losing the training's progress display.
- Lay a clean abstraction so future remote-runner backends (SSH, Slurm, etc.) drop in without restructuring the API or the frontend.

## Scope

In: local-only training jobs, in-process supervision, file-backed history, Landing Jobs section, per-job monitoring page. Out: remote runners, an explicit job queue, recording/teleop as jobs, persisting in-flight jobs across `lelab` restarts.

## Architecture

### Backend reorganisation

- **New** `app/jobs.py` — owns the `JobRegistry`, the `JobRunner` Protocol, `LocalJobRunner` implementation, and the Pydantic models used by the API. Single source of truth for job lifecycle.
- **Reduced** `app/training.py` — keeps the `TrainingRequest` model and the `_build_training_command(request, output_dir)` helper (renamed/refactored from the existing `TrainingManager._build_training_command` so it takes `output_dir` explicitly and has no `self`). Removes the singleton, `TrainingStatus` model (replaced by `TrainingMetrics` in `jobs.py`), and all subprocess management — that logic moves into `LocalJobRunner`.
- **Updated** `app/main.py` — removes the four old training routes (`/start-training`, `/stop-training`, `/training-status`, `/training-logs`) and registers the new `/jobs/...` family. The existing imports from `app.training` change correspondingly.

### Core types (in `app/jobs.py`)

```python
from typing import Literal, Optional, Protocol, Iterator
from pydantic import BaseModel
from .training import TrainingRequest

class TrainingMetrics(BaseModel):
    current_step: int = 0
    total_steps: int = 0
    current_loss: Optional[float] = None
    current_lr: Optional[float] = None
    grad_norm: Optional[float] = None
    eta_seconds: Optional[float] = None

JobState = Literal["running", "done", "failed", "interrupted"]

class JobRecord(BaseModel):
    id: str
    name: str
    state: JobState
    config: TrainingRequest
    output_dir: str
    started_at: float
    ended_at: Optional[float] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None  # set when state == "failed" with a non-Popen reason
    metrics: TrainingMetrics = TrainingMetrics()
    runner: Literal["local"] = "local"

class JobRunner(Protocol):
    def start(self, job_id: str, config: TrainingRequest, output_dir: str) -> None: ...
    def stop(self) -> None: ...
    def is_running(self) -> bool: ...
    def stream_log_lines(self) -> Iterator[str]: ...
```

### `LocalJobRunner` (in `app/jobs.py`)

Owns one `subprocess.Popen` for the lifetime of one job. Internally:
- `start(job_id, config, output_dir)`: builds the CLI command via `training._build_training_command(config, output_dir)`, spawns `subprocess.Popen` with `stdout=PIPE`, `stderr=STDOUT`, `universal_newlines=True`, `bufsize=1`, and `env` extended with `PYTHONUNBUFFERED=1`. Spawns a daemon thread that reads stdout line-by-line and pushes lines into an internal `queue.Queue` plus invokes a parser callback for metrics.
- `stop()`: `process.terminate()`, then `process.wait(timeout=10)`, then `process.kill()` on timeout — same shape as today.
- `is_running()`: `process is not None and process.poll() is None`.
- `stream_log_lines()`: yields whatever has accumulated in the queue since the last call (drain-on-read), then returns. Non-blocking.

Metric parsing — the existing tqdm + `step:N loss:X` parsing logic moves out of `TrainingManager._parse_log_line` and into a free function `_parse_metrics_into(line: str, metrics: TrainingMetrics) -> None` in `app/jobs.py`. The runner thread invokes it on each line.

### `JobRegistry` (in `app/jobs.py`)

Module-level singleton, file-backed via per-job `outputs/train/{id}/job.json`.

- `__init__()`: ensures `outputs/train/` exists; loads disk records.
- `_load_from_disk()`: scans `outputs/train/*/job.json`. For each record whose `state == "running"`, rewrites it to `interrupted` and persists — those subprocesses died with the previous `lelab` process; they're no longer running.
- `start(config: TrainingRequest) -> JobRecord`: rejects if any record in the in-memory map has `state == "running"`. Generates the id (same slug logic as `_generate_output_dir`: `{policy}_{dataset_slug}_{YYYY-MM-DD_HH-MM-SS}`) and `name` (`f"{policy.upper()} · {dataset_repo_id}"`). Creates `outputs/train/{id}/`, writes initial `job.json`, instantiates a `LocalJobRunner`, calls `runner.start(...)`, registers the runner in memory, returns the record.
- `list(limit: int = 10) -> List[JobRecord]`: returns the most recent N records sorted by `started_at` desc.
- `get(job_id: str) -> JobRecord`: returns from in-memory if running (so `metrics` is fresh from the runner), else loads from disk.
- `stop(job_id: str)`: must be running; calls `runner.stop()`, registry's monitor loop will pick up the exit and finalise.
- `drain_logs(job_id: str) -> List[LogEntry]`: returns logs from the runner's queue if the job is running in this process; for finished/interrupted jobs returns `[]`.
- `delete(job_id: str)`: must NOT be running; removes `outputs/train/{id}/` recursively, drops from in-memory cache.

A registry-level monitor thread (one for the whole process, not per-job since concurrency is 1) polls `runner.is_running()` once per second; on transition `running → not running` it reads `process.returncode`, sets the record's state to `done` (rc==0) or `failed` (rc!=0), sets `ended_at`, persists `job.json`, and removes the runner from the in-memory map. The metrics that were live-updating during the run remain in `record.metrics` — the same "preserve final metrics" behaviour we already have today.

### API endpoints (in `app/main.py`)

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| `POST` | `/jobs/training` | `TrainingRequest` | `JobRecord` (201). `409` if a local job is already `running`. |
| `GET` | `/jobs` | `limit` (default 10) | `{ jobs: List[JobRecord] }`. Sorted by `started_at` desc. |
| `GET` | `/jobs/{id}` | — | `JobRecord` (200). `404` if unknown. |
| `GET` | `/jobs/{id}/logs` | — | `{ logs: List[{timestamp: float, message: str}] }`. Drains pending lines on read. Empty for non-running jobs. |
| `POST` | `/jobs/{id}/stop` | — | `JobRecord` after stop. `409` if not in `running` state. `404` if unknown. |
| `DELETE` | `/jobs/{id}` | — | `204`. `409` if `running`. `404` if unknown. |

### Frontend

#### Landing — new Jobs section

A new `JobsSection` component, mounted at the top of `Landing.tsx`'s feature region (above the existing tiles). On mount it polls `GET /jobs?limit=10` every 5 s, plus once immediately. Renders:

- Section header "Jobs" + a small refresh icon-button.
- If `jobs.length === 0`: a single `<p>` "No training jobs yet. Start one from the Training page." (`text-slate-500 text-sm`).
- Otherwise: a `grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4` of `<JobCard>`, sorted newest first.

#### `JobCard` component

One Card per job, ~280–340 px wide. Inside:

- **Status pill** top-right — `Running` (green-400 dot, `animate-pulse`), `Done` (slate-400 check), `Failed` (red-400 X), `Interrupted` (amber-400 warning).
- **Title** — `{POLICY_UPPER} · {dataset_repo_id}`. Truncated with `truncate` and full-text `title` attribute.
- **Subtitle** — relative time. While running: `"started 2m ago"`. Otherwise: `"ended 5m ago"` or `"interrupted"`.
- **Progress bar** — same gradient + percentage-on-bar pattern as the Monitoring page progress card, but thinner (`h-5`). Reads `metrics.current_step / metrics.total_steps`.
- **Action button** (right side):
  - `running` → square Stop icon. Click → confirm popover ("Stop this run?") → `POST /jobs/{id}/stop`.
  - finished states → X icon. Click → confirm popover ("Delete this run? This wipes the output directory.") → `DELETE /jobs/{id}`.
- **Card body itself is clickable** → `navigate("/training/{id}")`. Action button uses `e.stopPropagation()` so it doesn't navigate.

#### Training page rework

The page splits into two route configurations sharing the same `Training.tsx` page component, distinguished by `useParams<{ jobId?: string }>()`:

- `/training` (no `jobId`) — Configuration mode. Renders the `TrainingExtraGate` (unchanged), then `EssentialsCard` + `AdvancedCard` + a Start button (no longer floating; lives at the bottom of the form). On Start: `POST /jobs/training`, on success `navigate("/training/${response.id}")`.
  - The Start button is disabled with tooltip "Another training is already running" if `GET /jobs?limit=1` returns a record with `state == "running"`. (One-shot fetch on mount; refreshed after a failed Start that returned 409.)
- `/training/:jobId` — Monitoring mode. Polls `GET /jobs/{id}` every 1 s and `GET /jobs/{id}/logs` every 1 s while `state == "running"`. Renders:
  - A header strip with the job's `name`, status pill, started-at time, and a "← Back to Jobs" link.
  - The existing `MonitoringStats` (unchanged internals) reading from `metrics`.
  - The existing `TrainingLogs` (unchanged internals) reading from the polled logs.
  - A contextual button: Stop (when running) or Delete (when finished/failed/interrupted).
  - When the job is finished, polling stops; the page shows the frozen final metrics and the full log buffer.
- The existing in-page Tabs (`Configuration` / `Monitoring`) and the floating Start/Stop button go away — they're no longer the right shape.

#### Routing

`App.tsx` adds `<Route path="/training/:jobId" element={<Training />} />` alongside the existing `<Route path="/training" element={<Training />} />`. Same component, two routes.

#### Deprecation

- `TrainingTabs.tsx`, `TrainingControls.tsx`, `MonitoringTab.tsx` are deleted (replaced by route-driven branching inside `Training.tsx`).
- `TrainingHeader.tsx` is kept and its status indicator is dropped (the header doesn't know about a specific job; status lives on the JobCard / Monitoring header).

### Recording / teleop / calibration unchanged

These features have always used their own state and never shared anything with `TrainingManager`. Today the only thing preventing recording-during-training from feeling natural is the Training page's monopoly on the Monitoring tab — that is exactly what this change fixes.

## Data flow

1. User on `/training` clicks Start.
2. Frontend `POST /jobs/training` with the TrainingRequest body.
3. Backend `JobRegistry.start()` validates (no other running local job), generates `id` and `output_dir`, writes initial `job.json` with `state="running"`, instantiates `LocalJobRunner`, spawns subprocess, registers runner. Returns `JobRecord`.
4. Frontend navigates to `/training/{id}`. Polling begins.
5. While the subprocess writes log lines, the runner's monitor thread enqueues them and updates `record.metrics`. Frontend polls every 1 s.
6. On subprocess exit, registry's monitor thread sets `state` to `done`/`failed`, persists `job.json`, removes runner from memory. Final metrics remain in the record.
7. Frontend's poll picks up `state != "running"` and stops polling, leaving the final view in place.
8. Concurrently, on Landing, `JobsSection` polls `/jobs?limit=10` every 5 s and reflects all of the above in card form.

## Error handling

- 409 on `POST /jobs/training` when a local job is already running — frontend surfaces a toast "Another training is running. Stop it first." and refreshes its in-memory "running flag."
- 409 on `POST /jobs/{id}/stop` when not running — silently treated as success on the frontend (the user just clicked stop on a card whose state changed during the click).
- 409 on `DELETE /jobs/{id}` when running — toast "Stop the job before deleting."
- 404 on any `/jobs/{id}` route — toast "Job no longer exists" + remove from any in-memory list and navigate to Landing if on `/training/:jobId`.
- Subprocess crashes during start (Popen raises) — registry persists `state="failed"`, `exit_code=null`, surfaces the error message in `JobRecord.error_message: Optional[str]`. Frontend shows it in the Monitoring header for failed jobs.
- `lelab` restart with a running job → on next boot, registry's `_load_from_disk()` rewrites the record to `interrupted`. Card on Landing shows the amber `Interrupted` pill, the metrics frozen at whatever `job.json` had at last persist.

`job.json` is persisted (a) at start, (b) on every metric update *throttled to once per second* to avoid I/O storms, (c) on stop / exit, (d) at registry shutdown if we add that hook. The throttle is the runner's responsibility.

## Out of scope

- Remote runners. The `JobRunner` interface anticipates them; no second implementation lands here.
- A job queue. Concurrency is 1; second start request returns 409.
- Recording / teleop / calibration as jobs. Only training.
- Detached local subprocesses that survive `lelab` restart.
- A dedicated history page or filtering. Last-10 on Landing, that's it.
- Renaming a job after creation, or user-supplied names at creation. Auto-naming only.
- Resuming a training run from a checkpoint via the UI. The CLI's `--resume` flag still works for users who need it, but the UI doesn't expose it for now.

## Acceptance

- Visiting `/` while no jobs exist shows a Jobs section with the empty-state line.
- Clicking Start on `/training` creates a job, navigates to `/training/{id}`, and the Progress / Loss / LR / Logs all populate as before.
- Going back to `/` while training runs shows a Running card with live progress %.
- Clicking the running card returns the user to `/training/{id}` with the same live monitoring view.
- Clicking the Stop icon on a running card stops the subprocess; card flips to `Done` / `Failed` within ~1 s.
- Restarting `lelab` while a training was running → the card shows `Interrupted` after restart; metrics are the last-persisted snapshot.
- Clicking the X on a finished card and confirming wipes `outputs/train/{id}/` and removes the card.
- Starting a recording dataset session while a training runs works (always did) — but now the running training is visible from the Recording page's "← Back to Home" path.
