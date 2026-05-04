# Replay Dataset page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Replay Dataset page work end-to-end: list the user's HF datasets, browse episodes, play a chosen episode in the URDF viewer with synced camera videos. No physical robot.

**Architecture:** Backend exposes new HTTP endpoints for dataset/episode browsing and a streaming replay session (start / control / stop). A backend ticker thread emits joint values over the existing `/ws/joint-data` WebSocket; the URDF viewer applies them with no changes. Camera videos play directly from `https://huggingface.co/datasets/{repo}/resolve/main/...` URLs returned by the backend; the frontend nudges each `<video>`'s `currentTime` to track the backend's frame index. Replay is mutually exclusive with teleop and recording.

**Tech Stack:** FastAPI, Pydantic, `huggingface_hub`, `lerobot.datasets.LeRobotDatasetMetadata` / `LeRobotDataset`, `numpy`, React, Vite, shadcn/cmdk.

**Spec:** [`docs/superpowers/specs/2026-04-30-replay-page-redesign.md`](../specs/2026-04-30-replay-page-redesign.md)

**Note on "no test suite":** Per [CLAUDE.md](../../../CLAUDE.md), this repo has no pytest/jest setup and changes are validated by running `lelab` and exercising endpoints. Each task uses curl (backend) or browser checks (frontend) for verification instead of unit tests. The TDD spirit (verify behavior at each step) is preserved.

**Implementation note that diverges from the spec text:** The spec section "Backend → New module: `app/dataset_browser.py`" describes a hand-rolled `meta/info.json` + `meta/episodes.jsonl` parser. Modern LeRobot datasets (codebase v3) have moved `episodes` into chunked parquet files; legacy `.jsonl` is a fallback. We use `lerobot.datasets.dataset_metadata.LeRobotDatasetMetadata` instead, which downloads only `meta/` and provides `episodes`, `fps`, `video_keys`, `get_video_file_path()`, etc. across versions. End-user behavior matches the spec; this resolves the spec's "Open implementation details" note about format compatibility.

---

## File structure

**Backend (new):**
- `app/dataset_browser.py` — pure HF Hub queries (list user datasets, episode list, replay assets).

**Backend (rewritten):**
- `app/replaying.py` — replaces the deleted physical-robot flow with a streaming ticker session.

**Backend (modified):**
- `app/main.py` — drops old replay routes, adds `GET /datasets`, `GET /episodes/{repo:path}`, `POST /replay/start|control|stop`.

**Frontend (new):**
- `frontend/src/lib/replayApi.ts` — typed wrappers for new endpoints.
- `frontend/src/hooks/useReplayPlayback.ts` — playback state machine + WS frame listener.
- `frontend/src/components/replay/DatasetCombobox.tsx` — searchable dropdown with custom-repo escape hatch.
- `frontend/src/components/replay/EpisodeList.tsx` — scrollable episode list with formatted durations.
- `frontend/src/components/replay/VideoGrid.tsx` — one `<video>` per camera, refs forwarded for sync.
- `frontend/src/components/replay/PlaybackBar.tsx` — play/pause + seek slider + speed picker.

**Frontend (modified):**
- `frontend/src/components/replay/ReplayHeader.tsx` — status driven by `useReplayPlayback`.
- `frontend/src/pages/ReplayDataset.tsx` — drops mocks, wires the new components.

**Frontend (deleted):**
- `frontend/src/components/replay/DatasetSelector.tsx`
- `frontend/src/components/replay/EpisodePlayer.tsx`
- `frontend/src/components/replay/PlaybackControls.tsx`
- `frontend/src/components/replay/ReplayVisualizer.tsx` (functionality moved into the page + `VideoGrid`).

---

## Task 1: Backend — `app/dataset_browser.py`

**Files:**
- Create: `app/dataset_browser.py`

- [ ] **Step 1: Write the module**

Create `app/dataset_browser.py`:

```python
import logging
from typing import Any

from huggingface_hub import HfApi, whoami
from huggingface_hub.errors import HfHubHTTPError, LocalTokenNotFoundError

from lerobot.datasets.dataset_metadata import LeRobotDatasetMetadata

logger = logging.getLogger(__name__)

HF_RESOLVE_BASE = "https://huggingface.co/datasets"


def list_user_datasets() -> list[dict[str, Any]]:
    try:
        info = whoami()
    except (LocalTokenNotFoundError, HfHubHTTPError, OSError):
        return []

    authors = [info["name"]] + [o["name"] for o in info.get("orgs", [])]
    api = HfApi()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for author in authors:
        try:
            for ds in api.list_datasets(author=author, limit=200):
                if ds.id in seen:
                    continue
                seen.add(ds.id)
                out.append({
                    "repo_id": ds.id,
                    "last_modified": ds.last_modified.isoformat() if ds.last_modified else None,
                    "private": bool(getattr(ds, "private", False)),
                })
        except HfHubHTTPError as e:
            logger.warning(f"list_datasets({author}) failed: {e}")

    out.sort(key=lambda d: d["last_modified"] or "", reverse=True)
    return out


def _format_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60:02d}:{s % 60:02d}"
    if s < 86400:
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"
    days = s // 86400
    rem = s % 86400
    return f"{days}d {rem // 3600:02d}:{(rem % 3600) // 60:02d}"


def get_episode_list(repo_id: str) -> dict[str, Any]:
    meta = LeRobotDatasetMetadata(repo_id)
    fps = meta.fps
    eps = meta.episodes  # pandas DataFrame in v3, list-like in older

    out: list[dict[str, Any]] = []
    if eps is None:
        return {"fps": fps, "episodes": []}

    # `meta.episodes` is a pandas DataFrame for v3 datasets; iterate by row.
    for idx in range(len(eps)):
        row = eps.iloc[idx] if hasattr(eps, "iloc") else eps[idx]
        length = int(row["length"])
        tasks = row.get("tasks") if hasattr(row, "get") else row["tasks"]
        if hasattr(tasks, "tolist"):
            tasks = tasks.tolist()
        duration = length / fps if fps else 0.0
        out.append({
            "episode_index": idx,
            "length": length,
            "tasks": list(tasks) if tasks is not None else [],
            "duration_seconds": duration,
            "duration_human": _format_duration(duration),
        })

    return {"fps": fps, "total_episodes": meta.total_episodes, "episodes": out}


def get_replay_assets(repo_id: str, episode: int) -> dict[str, Any]:
    meta = LeRobotDatasetMetadata(repo_id)
    if episode < 0 or episode >= meta.total_episodes:
        raise IndexError(f"Episode {episode} out of range (0..{meta.total_episodes - 1})")

    action_names = meta.features["action"]["names"]
    if isinstance(action_names, dict):
        action_names = action_names.get("motors") or list(action_names.values())[0]
    joint_names = list(action_names)

    cameras = []
    for vid_key in meta.video_keys:
        rel_path = meta.get_video_file_path(episode, vid_key).as_posix()
        url = f"{HF_RESOLVE_BASE}/{repo_id}/resolve/main/{rel_path}"
        cameras.append({"key": vid_key, "url": url})

    row = meta.episodes.iloc[episode] if hasattr(meta.episodes, "iloc") else meta.episodes[episode]
    num_frames = int(row["length"])

    return {
        "joint_names": joint_names,
        "cameras": cameras,
        "fps": meta.fps,
        "num_frames": num_frames,
    }
```

- [ ] **Step 2: Sanity-check the module loads**

Run from the project root:

```bash
.venv/bin/python -c "from app.dataset_browser import list_user_datasets, get_episode_list, get_replay_assets; print('ok')"
```

Expected output: `ok` (no import error).

- [ ] **Step 3: Commit**

```bash
git add app/dataset_browser.py
git commit -m "feat(replay): add dataset_browser module for HF Hub queries"
```

---

## Task 2: Backend — Wire `/datasets` and `/episodes/{repo:path}` routes

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Add the two GET routes**

In `app/main.py`, add this import near the existing `from .hf_auth import handle_hf_auth_status` line:

```python
from . import dataset_browser
```

Then add two routes (place them after the existing `/hf-auth-status` route around line 254):

```python
@app.get("/datasets")
def datasets_list():
    """List datasets the logged-in HF user owns or shares with their orgs."""
    return dataset_browser.list_user_datasets()


@app.get("/episodes/{repo_id:path}")
def datasets_episodes(repo_id: str):
    """List episodes (with durations) for a LeRobot-format dataset."""
    try:
        return dataset_browser.get_episode_list(repo_id)
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"error": "Not a LeRobot-format dataset", "detail": str(e)})
    except Exception as e:
        logger.exception("episode list failed")
        return JSONResponse(status_code=500, content={"error": str(e)})
```

Add to imports at the top of the file (next to other FastAPI imports):

```python
from fastapi.responses import JSONResponse
```

- [ ] **Step 2: Restart `lelab --dev` and test the endpoints**

In one terminal: `lelab --dev`. In another:

```bash
curl -s http://localhost:8000/datasets | head -c 400
echo
curl -s http://localhost:8000/episodes/lerobot/aloha_sim_insertion_human | head -c 400
```

Expected:
- `/datasets`: a JSON array (or `[]` if not authenticated). Each item has `repo_id`, `last_modified`, `private`.
- `/episodes/...`: a JSON object with `fps`, `total_episodes`, and `episodes` list with `episode_index`, `length`, `duration_seconds`, `duration_human`.

If `/episodes/...` returns 404, try a different public LeRobot dataset (e.g. `lerobot/pusht`).

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat(replay): expose /datasets and /episodes endpoints"
```

---

## Task 3: Backend — Rewrite `app/replaying.py`

**Files:**
- Rewrite: `app/replaying.py`

- [ ] **Step 1: Replace the file**

Overwrite `app/replaying.py` with:

```python
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from pydantic import BaseModel

from lerobot.datasets.lerobot_dataset import LeRobotDataset

from . import dataset_browser

logger = logging.getLogger(__name__)


class StartReplayRequest(BaseModel):
    repo_id: str
    episode: int


class ReplayControlRequest(BaseModel):
    action: str  # "pause" | "resume" | "seek" | "set_speed"
    value: float | int | None = None


@dataclass
class ReplayState:
    active: bool = False
    repo_id: str | None = None
    episode: int | None = None
    frame: int = 0
    total_frames: int = 0
    fps: float = 30.0
    speed: float = 1.0
    paused: bool = False
    joint_names: list[str] = field(default_factory=list)


_state_lock = threading.Lock()
_state = ReplayState()
_actions: np.ndarray | None = None  # (T, J)
_stop_event = threading.Event()
_ticker_thread: threading.Thread | None = None


def _strip_pos_suffix(names: list[str]) -> list[str]:
    return [n[:-4] if n.endswith(".pos") else n for n in names]


def _ticker_loop(manager) -> None:
    global _state, _actions
    while not _stop_event.is_set():
        with _state_lock:
            paused = _state.paused
            frame = _state.frame
            total = _state.total_frames
            fps = _state.fps
            speed = _state.speed
            joint_names = _state.joint_names

        if paused or frame >= total:
            time.sleep(0.05)
            if frame >= total and not paused:
                with _state_lock:
                    _state.paused = True
            continue

        if _actions is None:
            time.sleep(0.05)
            continue

        row = _actions[frame]
        joints = {name: float(row[i]) for i, name in enumerate(joint_names)}
        manager.broadcast_joint_data_sync({
            "type": "joint_update",
            "joints": joints,
            "timestamp": frame / fps,
            "frame": frame,
        })

        with _state_lock:
            _state.frame = min(_state.frame + 1, _state.total_frames)

        # Sleep in slices so seek/stop responsiveness stays high at low speeds.
        target = 1.0 / max(fps * speed, 0.01)
        slept = 0.0
        while slept < target and not _stop_event.is_set():
            chunk = min(0.05, target - slept)
            time.sleep(chunk)
            slept += chunk


def handle_start_replay(req: StartReplayRequest, manager) -> dict[str, Any]:
    global _state, _actions, _ticker_thread

    # Concurrency guard: refuse if teleop or recording is active.
    from .teleoperating import teleoperation_active
    from .recording import recording_active
    if teleoperation_active or recording_active:
        return {"success": False, "message": "Stop teleoperation or recording first."}

    with _state_lock:
        if _state.active:
            return {"success": False, "message": "Replay already active. Stop it first."}

    try:
        assets = dataset_browser.get_replay_assets(req.repo_id, req.episode)
    except Exception as e:
        logger.exception("get_replay_assets failed")
        return {"success": False, "message": f"Could not resolve dataset assets: {e}"}

    try:
        ds = LeRobotDataset(req.repo_id, episodes=[req.episode], download_videos=False)
    except Exception as e:
        logger.exception("LeRobotDataset load failed")
        return {"success": False, "message": f"Failed to load episode: {e}"}

    try:
        action_col = ds.hf_dataset["action"]
    except Exception:
        # Older LeRobotDataset attribute layout
        action_col = [ds[i]["action"] for i in range(len(ds))]
    actions_np = np.asarray([np.asarray(a, dtype=np.float32) for a in action_col], dtype=np.float32)

    joint_names = _strip_pos_suffix(assets["joint_names"])

    _stop_event.clear()
    with _state_lock:
        _state = ReplayState(
            active=True,
            repo_id=req.repo_id,
            episode=req.episode,
            frame=0,
            total_frames=int(actions_np.shape[0]),
            fps=float(assets["fps"]),
            speed=1.0,
            paused=False,
            joint_names=joint_names,
        )
    _actions = actions_np

    _ticker_thread = threading.Thread(target=_ticker_loop, args=(manager,), daemon=True)
    _ticker_thread.start()

    return {
        "success": True,
        "joint_names": joint_names,
        "cameras": assets["cameras"],
        "fps": float(assets["fps"]),
        "num_frames": int(actions_np.shape[0]),
    }


def handle_replay_control(req: ReplayControlRequest) -> dict[str, Any]:
    with _state_lock:
        if not _state.active:
            return {"success": False, "message": "No active replay session."}

        if req.action == "pause":
            _state.paused = True
        elif req.action == "resume":
            if _state.frame >= _state.total_frames:
                _state.frame = 0
            _state.paused = False
        elif req.action == "seek":
            if req.value is None:
                return {"success": False, "message": "seek requires a value (frame index)."}
            target = max(0, min(int(req.value), max(_state.total_frames - 1, 0)))
            _state.frame = target
        elif req.action == "set_speed":
            if req.value is None:
                return {"success": False, "message": "set_speed requires a value."}
            _state.speed = max(0.25, min(float(req.value), 16.0))
        else:
            return {"success": False, "message": f"Unknown action: {req.action}"}

    return {"success": True}


def handle_stop_replay() -> dict[str, Any]:
    global _state, _actions, _ticker_thread

    _stop_event.set()
    thread = _ticker_thread
    if thread is not None:
        thread.join(timeout=1.5)
    _ticker_thread = None

    with _state_lock:
        _state = ReplayState()
    _actions = None
    return {"success": True}


def handle_replay_status() -> dict[str, Any]:
    with _state_lock:
        return {
            "active": _state.active,
            "repo_id": _state.repo_id,
            "episode": _state.episode,
            "frame": _state.frame,
            "total_frames": _state.total_frames,
            "fps": _state.fps,
            "speed": _state.speed,
            "paused": _state.paused,
        }


def cleanup() -> None:
    handle_stop_replay()
```

- [ ] **Step 2: Sanity-check the module loads**

```bash
.venv/bin/python -c "from app.replaying import handle_start_replay, handle_replay_control, handle_stop_replay, StartReplayRequest, ReplayControlRequest; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add app/replaying.py
git commit -m "refactor(replay): replace physical-robot flow with streaming ticker"
```

---

## Task 4: Backend — Wire `/replay/start`, `/replay/control`, `/replay/stop`

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Replace replay imports and routes**

In `app/main.py`, find the replay import block (around lines 49-56):

```python
from .replaying import (
    ReplayRequest,
    handle_start_replay,
    handle_stop_replay,
    handle_replay_status,
    handle_replay_logs,
)
```

Replace with:

```python
from .replaying import (
    StartReplayRequest,
    ReplayControlRequest,
    handle_start_replay,
    handle_replay_control,
    handle_stop_replay,
    handle_replay_status,
)
```

Then find the existing replay routes section (search for `# REPLAY ENDPOINTS` around line 367):

```python
@app.post("/start-replay")
def start_replay(request: ReplayRequest):
    """Start a replay session"""
    return handle_start_replay(request)


@app.post("/stop-replay")
def stop_replay():
    """Stop the current replay session"""
    return handle_stop_replay()


@app.get("/replay-status")
def replay_status():
    """Get the current replay status"""
    return handle_replay_status()


@app.get("/replay-logs")
def replay_logs():
    """Get recent replay logs"""
    return handle_replay_logs()
```

Replace the entire block with:

```python
@app.post("/replay/start")
def replay_start(request: StartReplayRequest):
    """Start streaming an episode's actions over /ws/joint-data."""
    return handle_start_replay(request, manager)


@app.post("/replay/control")
def replay_control(request: ReplayControlRequest):
    """Mutate the active replay session (pause/resume/seek/set_speed)."""
    return handle_replay_control(request)


@app.post("/replay/stop")
def replay_stop():
    """Stop the active replay session."""
    return handle_stop_replay()


@app.get("/replay/status")
def replay_status():
    """Get the current replay session state."""
    return handle_replay_status()
```

- [ ] **Step 2: Restart `lelab --dev` and end-to-end test the replay session**

In one terminal: `lelab --dev`. In another (use a small public LeRobot dataset that exists):

```bash
# Pick a small public dataset that's known to work; aloha_sim_insertion_human is ~2 episodes.
REPO=lerobot/aloha_sim_insertion_human

# Episode list
curl -s "http://localhost:8000/episodes/${REPO}" | python3 -c "import sys,json; d=json.load(sys.stdin); print('episodes:', len(d['episodes']), 'fps:', d['fps'])"

# Start replay (downloads parquet on first run; can take a moment)
curl -s -X POST "http://localhost:8000/replay/start" \
  -H "Content-Type: application/json" \
  -d "{\"repo_id\": \"${REPO}\", \"episode\": 0}"
echo

# Status after a couple of seconds
sleep 2
curl -s "http://localhost:8000/replay/status"
echo

# Pause
curl -s -X POST "http://localhost:8000/replay/control" \
  -H "Content-Type: application/json" \
  -d '{"action": "pause"}'
echo

# Stop
curl -s -X POST "http://localhost:8000/replay/stop"
echo
```

Expected:
- `episodes:` line prints a sensible episode count and fps.
- `/replay/start` returns `{"success": true, "joint_names": [...], "cameras": [...], "fps": ..., "num_frames": ...}`.
- `/replay/status` shows `"active": true` and `"frame"` advancing between calls.
- Pause and stop both return `{"success": true}`.

Also check the server logs for any errors and watch a running `lelab --dev`'s `/ws/joint-data` channel (e.g. open the browser dev tools on the page, or use `wscat -c ws://localhost:8000/ws/joint-data`) — `joint_update` messages with a `frame` field should appear during play.

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat(replay): add /replay/start|control|stop|status routes"
```

---

## Task 5: Frontend — `frontend/src/lib/replayApi.ts`

**Files:**
- Create: `frontend/src/lib/replayApi.ts`

- [ ] **Step 1: Write the typed API wrappers**

```ts
export interface DatasetItem {
  repo_id: string;
  last_modified: string | null;
  private: boolean;
}

export interface EpisodeItem {
  episode_index: number;
  length: number;
  tasks: string[];
  duration_seconds: number;
  duration_human: string;
}

export interface EpisodeListResponse {
  fps: number;
  total_episodes: number;
  episodes: EpisodeItem[];
}

export interface CameraItem {
  key: string;
  url: string;
}

export interface StartReplayResponse {
  success: boolean;
  message?: string;
  joint_names?: string[];
  cameras?: CameraItem[];
  fps?: number;
  num_frames?: number;
}

export interface ReplayStatus {
  active: boolean;
  repo_id: string | null;
  episode: number | null;
  frame: number;
  total_frames: number;
  fps: number;
  speed: number;
  paused: boolean;
}

type Fetcher = (url: string, options?: RequestInit) => Promise<Response>;

export async function listDatasets(baseUrl: string, fetcher: Fetcher): Promise<DatasetItem[]> {
  const r = await fetcher(`${baseUrl}/datasets`);
  if (!r.ok) throw new Error(`GET /datasets failed: ${r.status}`);
  return r.json();
}

export async function listEpisodes(baseUrl: string, fetcher: Fetcher, repoId: string): Promise<EpisodeListResponse> {
  const r = await fetcher(`${baseUrl}/episodes/${repoId}`);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.error || `GET /episodes failed: ${r.status}`);
  }
  return r.json();
}

export async function startReplay(
  baseUrl: string,
  fetcher: Fetcher,
  repoId: string,
  episode: number
): Promise<StartReplayResponse> {
  const r = await fetcher(`${baseUrl}/replay/start`, {
    method: "POST",
    body: JSON.stringify({ repo_id: repoId, episode }),
  });
  return r.json();
}

export async function controlReplay(
  baseUrl: string,
  fetcher: Fetcher,
  action: "pause" | "resume" | "seek" | "set_speed",
  value?: number
): Promise<{ success: boolean; message?: string }> {
  const r = await fetcher(`${baseUrl}/replay/control`, {
    method: "POST",
    body: JSON.stringify({ action, value }),
  });
  return r.json();
}

export async function stopReplay(baseUrl: string, fetcher: Fetcher): Promise<{ success: boolean }> {
  const r = await fetcher(`${baseUrl}/replay/stop`, { method: "POST" });
  return r.json();
}
```

- [ ] **Step 2: Type-check by importing in a scratch file (optional smoke check)**

The Vite dev server will catch type errors when consumers import these. No standalone check needed — verify in subsequent tasks.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/replayApi.ts
git commit -m "feat(replay): typed wrappers for replay backend endpoints"
```

---

## Task 6: Frontend — `useReplayPlayback` hook

**Files:**
- Create: `frontend/src/hooks/useReplayPlayback.ts`

- [ ] **Step 1: Write the hook**

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { useApi } from "@/contexts/ApiContext";
import {
  CameraItem,
  controlReplay as apiControlReplay,
  listDatasets as apiListDatasets,
  listEpisodes as apiListEpisodes,
  startReplay as apiStartReplay,
  stopReplay as apiStopReplay,
  StartReplayResponse,
} from "@/lib/replayApi";

export type ReplayStatus = "idle" | "loading" | "playing" | "paused" | "ended" | "error";

export interface ReplaySessionState {
  status: ReplayStatus;
  repoId: string | null;
  episode: number | null;
  frame: number;
  totalFrames: number;
  fps: number;
  speed: number;
  paused: boolean;
  cameras: CameraItem[];
  jointNames: string[];
  error: string | null;
}

const INITIAL: ReplaySessionState = {
  status: "idle",
  repoId: null,
  episode: null,
  frame: 0,
  totalFrames: 0,
  fps: 30,
  speed: 1,
  paused: false,
  cameras: [],
  jointNames: [],
  error: null,
};

const SYNC_THRESHOLD_S = 0.2;

export const useReplayPlayback = () => {
  const { baseUrl, wsBaseUrl, fetchWithHeaders } = useApi();
  const [state, setState] = useState<ReplaySessionState>(INITIAL);
  const stateRef = useRef(state);
  stateRef.current = state;

  const videoRefs = useRef<HTMLVideoElement[]>([]);
  const wsRef = useRef<WebSocket | null>(null);

  const setVideoRefs = useCallback((els: (HTMLVideoElement | null)[]) => {
    videoRefs.current = els.filter((e): e is HTMLVideoElement => e !== null);
  }, []);

  // Drive videos in response to backend frame ticks.
  const onTick = useCallback((frame: number) => {
    setState((s) => (s.frame === frame ? s : { ...s, frame }));

    const fps = stateRef.current.fps || 30;
    const expected = frame / fps;
    for (const v of videoRefs.current) {
      if (Number.isFinite(v.duration) && Math.abs(v.currentTime - expected) > SYNC_THRESHOLD_S) {
        try { v.currentTime = expected; } catch { /* ignored */ }
      }
    }
  }, []);

  // Subscribe to /ws/joint-data — read only the `frame` field.
  useEffect(() => {
    const ws = new WebSocket(`${wsBaseUrl}/ws/joint-data`);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "joint_update" && typeof msg.frame === "number") {
          onTick(msg.frame);
        }
      } catch { /* ignored */ }
    };
    return () => { ws.close(); wsRef.current = null; };
  }, [wsBaseUrl, onTick]);

  const start = useCallback(async (repoId: string, episode: number): Promise<StartReplayResponse> => {
    // If a session is already active, stop it first so backend isn't blocked by its own session.
    if (stateRef.current.status === "playing" || stateRef.current.status === "paused") {
      await apiStopReplay(baseUrl, fetchWithHeaders);
    }
    setState((s) => ({ ...s, status: "loading", error: null, repoId, episode }));
    const resp = await apiStartReplay(baseUrl, fetchWithHeaders, repoId, episode);
    if (!resp.success) {
      setState((s) => ({ ...s, status: "error", error: resp.message || "Failed to start replay" }));
      return resp;
    }
    setState({
      status: "playing",
      repoId,
      episode,
      frame: 0,
      totalFrames: resp.num_frames || 0,
      fps: resp.fps || 30,
      speed: 1,
      paused: false,
      cameras: resp.cameras || [],
      jointNames: resp.joint_names || [],
      error: null,
    });
    // Kick off video playback.
    setTimeout(() => {
      videoRefs.current.forEach((v) => {
        v.playbackRate = 1;
        v.play().catch(() => { /* autoplay block tolerated */ });
      });
    }, 50);
    return resp;
  }, [baseUrl, fetchWithHeaders]);

  const pause = useCallback(async () => {
    await apiControlReplay(baseUrl, fetchWithHeaders, "pause");
    videoRefs.current.forEach((v) => v.pause());
    setState((s) => ({ ...s, paused: true, status: "paused" }));
  }, [baseUrl, fetchWithHeaders]);

  const resume = useCallback(async () => {
    await apiControlReplay(baseUrl, fetchWithHeaders, "resume");
    videoRefs.current.forEach((v) => v.play().catch(() => { /* ignored */ }));
    setState((s) => ({ ...s, paused: false, status: "playing" }));
  }, [baseUrl, fetchWithHeaders]);

  const seek = useCallback(async (frame: number) => {
    await apiControlReplay(baseUrl, fetchWithHeaders, "seek", frame);
    setState((s) => ({ ...s, frame }));
  }, [baseUrl, fetchWithHeaders]);

  const setSpeed = useCallback(async (value: number) => {
    await apiControlReplay(baseUrl, fetchWithHeaders, "set_speed", value);
    videoRefs.current.forEach((v) => { v.playbackRate = value; });
    setState((s) => ({ ...s, speed: value }));
  }, [baseUrl, fetchWithHeaders]);

  const stop = useCallback(async () => {
    await apiStopReplay(baseUrl, fetchWithHeaders);
    videoRefs.current.forEach((v) => v.pause());
    setState(INITIAL);
  }, [baseUrl, fetchWithHeaders]);

  // Stop on unmount.
  useEffect(() => {
    return () => {
      // Best-effort fire-and-forget; stop is idempotent on the backend.
      apiStopReplay(baseUrl, fetchWithHeaders).catch(() => {});
    };
  }, [baseUrl, fetchWithHeaders]);

  return {
    state,
    setVideoRefs,
    start,
    pause,
    resume,
    seek,
    setSpeed,
    stop,
    listDatasets: () => apiListDatasets(baseUrl, fetchWithHeaders),
    listEpisodes: (repoId: string) => apiListEpisodes(baseUrl, fetchWithHeaders, repoId),
  };
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/hooks/useReplayPlayback.ts
git commit -m "feat(replay): useReplayPlayback hook"
```

---

## Task 7: Frontend — `DatasetCombobox` component

**Files:**
- Create: `frontend/src/components/replay/DatasetCombobox.tsx`

- [ ] **Step 1: Write the combobox**

```tsx
import React from "react";
import { Check, ChevronsUpDown, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { cn } from "@/lib/utils";
import { DatasetItem } from "@/lib/replayApi";

interface Props {
  datasets: DatasetItem[];
  loading: boolean;
  value: string | null;
  onChange: (repoId: string | null) => void;
}

const REPO_ID_RE = /^[\w.\-]+\/[\w.\-]+$/;

const DatasetCombobox: React.FC<Props> = ({ datasets, loading, value, onChange }) => {
  const [open, setOpen] = React.useState(false);
  const [customMode, setCustomMode] = React.useState(false);
  const [customValue, setCustomValue] = React.useState("");

  const submitCustom = () => {
    const v = customValue.trim();
    if (REPO_ID_RE.test(v)) {
      onChange(v);
      setCustomMode(false);
    }
  };

  if (customMode) {
    return (
      <div className="flex gap-2">
        <Input
          autoFocus
          value={customValue}
          onChange={(e) => setCustomValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submitCustom(); }}
          placeholder="org/dataset-name"
          className="bg-gray-800 border-gray-600 text-white"
        />
        <Button onClick={submitCustom} disabled={!REPO_ID_RE.test(customValue.trim())}>
          Use
        </Button>
        <Button variant="ghost" onClick={() => setCustomMode(false)}>
          Cancel
        </Button>
      </div>
    );
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between bg-gray-800 border-gray-600 text-white hover:bg-gray-700"
        >
          {value ?? (loading ? "Loading datasets…" : "Select a dataset…")}
          <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0 bg-gray-800 border-gray-700" align="start">
        <Command className="bg-gray-800 text-white">
          <CommandInput placeholder="Search datasets…" className="text-white" />
          <CommandList>
            <CommandEmpty>{loading ? "Loading…" : "No datasets."}</CommandEmpty>
            <CommandGroup>
              {datasets.map((d) => (
                <CommandItem
                  key={d.repo_id}
                  value={d.repo_id}
                  onSelect={(v) => { onChange(v); setOpen(false); }}
                  className="text-white aria-selected:bg-gray-700"
                >
                  <Check className={cn("mr-2 h-4 w-4", value === d.repo_id ? "opacity-100" : "opacity-0")} />
                  <span className="flex-1">{d.repo_id}</span>
                  {d.private && <span className="text-xs text-amber-400">private</span>}
                </CommandItem>
              ))}
            </CommandGroup>
            <CommandGroup>
              <CommandItem
                onSelect={() => { setCustomMode(true); setOpen(false); }}
                className="text-purple-300 aria-selected:bg-gray-700"
              >
                <Pencil className="mr-2 h-4 w-4" />
                Use custom repo ID…
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
};

export default DatasetCombobox;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/replay/DatasetCombobox.tsx
git commit -m "feat(replay): DatasetCombobox component"
```

---

## Task 8: Frontend — `EpisodeList` component

**Files:**
- Create: `frontend/src/components/replay/EpisodeList.tsx`

- [ ] **Step 1: Write the list**

```tsx
import React from "react";
import { ListVideo } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";
import { EpisodeItem } from "@/lib/replayApi";

interface Props {
  episodes: EpisodeItem[];
  selected: number | null;
  loading: boolean;
  error: string | null;
  onSelect: (episodeIndex: number) => void;
}

const EpisodeList: React.FC<Props> = ({ episodes, selected, loading, error, onSelect }) => {
  return (
    <Card className="bg-gray-900 border-gray-700 flex flex-col h-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-3 text-white">
          <ListVideo className="w-5 h-5 text-purple-400" />
          Episodes
        </CardTitle>
      </CardHeader>
      <CardContent className="flex-1 flex flex-col">
        <ScrollArea className="flex-1 min-h-[12rem] pr-4 border border-gray-700 rounded-lg">
          <div className="p-2 space-y-1">
            {loading && <div className="text-center text-gray-500 py-8">Loading episodes…</div>}
            {error && <div className="text-center text-red-400 py-8">{error}</div>}
            {!loading && !error && episodes.length === 0 && (
              <div className="text-center text-gray-500 py-8">Pick a dataset to see episodes.</div>
            )}
            {!loading && !error && episodes.map((ep) => (
              <button
                key={ep.episode_index}
                onClick={() => onSelect(ep.episode_index)}
                className={cn(
                  "w-full text-left p-2 rounded-md transition-colors text-sm flex items-center justify-between",
                  selected === ep.episode_index
                    ? "bg-purple-500/20 text-purple-300"
                    : "hover:bg-gray-800 text-gray-300"
                )}
              >
                <span>Episode {ep.episode_index}</span>
                <span className="font-mono text-xs text-gray-500">{ep.duration_human}</span>
              </button>
            ))}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  );
};

export default EpisodeList;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/replay/EpisodeList.tsx
git commit -m "feat(replay): EpisodeList component"
```

---

## Task 9: Frontend — `VideoGrid` component

**Files:**
- Create: `frontend/src/components/replay/VideoGrid.tsx`

- [ ] **Step 1: Write the grid**

```tsx
import React, { useEffect, useRef } from "react";
import { VideoOff } from "lucide-react";
import { CameraItem } from "@/lib/replayApi";

interface Props {
  cameras: CameraItem[];
  registerRefs: (els: (HTMLVideoElement | null)[]) => void;
}

const VideoGrid: React.FC<Props> = ({ cameras, registerRefs }) => {
  const refs = useRef<(HTMLVideoElement | null)[]>([]);

  useEffect(() => {
    registerRefs(refs.current);
  }, [cameras, registerRefs]);

  if (cameras.length === 0) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="aspect-video bg-gray-900 rounded-lg border border-gray-800 flex flex-col items-center justify-center p-2">
            <VideoOff className="h-8 w-8 text-gray-600 mb-2" />
            <span className="text-gray-500 text-xs">No video</span>
          </div>
        ))}
      </div>
    );
  }

  const cols = cameras.length === 1 ? "grid-cols-1" : cameras.length === 2 ? "grid-cols-2" : "grid-cols-2 md:grid-cols-4";

  return (
    <div className={`grid ${cols} gap-4`}>
      {cameras.map((cam, i) => (
        <div key={cam.key} className="aspect-video bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
          <video
            ref={(el) => { refs.current[i] = el; }}
            src={cam.url}
            preload="metadata"
            muted
            playsInline
            className="w-full h-full object-cover"
          />
          <div className="px-2 py-1 text-xs text-gray-400 bg-black/40 truncate">{cam.key}</div>
        </div>
      ))}
    </div>
  );
};

export default VideoGrid;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/replay/VideoGrid.tsx
git commit -m "feat(replay): VideoGrid component"
```

---

## Task 10: Frontend — `PlaybackBar` component

**Files:**
- Create: `frontend/src/components/replay/PlaybackBar.tsx`

- [ ] **Step 1: Write the playback bar**

```tsx
import React from "react";
import { Pause, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";

interface Props {
  paused: boolean;
  frame: number;
  totalFrames: number;
  fps: number;
  speed: number;
  disabled: boolean;
  onPlay: () => void;
  onPause: () => void;
  onStop: () => void;
  onSeek: (frame: number) => void;
  onSpeedChange: (speed: number) => void;
}

const SPEEDS = [1, 2, 4, 10];

const formatTime = (seconds: number): string => {
  if (!Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 3600) return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  const h = Math.floor(s / 3600);
  return `${String(h).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

const PlaybackBar: React.FC<Props> = ({
  paused, frame, totalFrames, fps, speed, disabled,
  onPlay, onPause, onStop, onSeek, onSpeedChange,
}) => {
  const current = fps ? frame / fps : 0;
  const total = fps ? totalFrames / fps : 0;

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 flex flex-col gap-3">
      <div className="flex items-center gap-3">
        {paused ? (
          <Button size="icon" onClick={onPlay} disabled={disabled}><Play className="h-4 w-4" /></Button>
        ) : (
          <Button size="icon" onClick={onPause} disabled={disabled}><Pause className="h-4 w-4" /></Button>
        )}
        <Button size="icon" variant="outline" onClick={onStop} disabled={disabled}>
          <Square className="h-4 w-4" />
        </Button>
        <div className="flex-1">
          <Slider
            min={0}
            max={Math.max(0, totalFrames - 1)}
            step={1}
            value={[frame]}
            disabled={disabled || totalFrames === 0}
            onValueChange={(v) => onSeek(v[0])}
          />
        </div>
        <div className="font-mono text-xs text-gray-300 w-32 text-right">
          {formatTime(current)} / {formatTime(total)}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400">Speed</span>
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => onSpeedChange(s)}
            disabled={disabled}
            className={cn(
              "px-2 py-1 rounded text-xs",
              speed === s ? "bg-purple-500/30 text-purple-200" : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            )}
          >
            {s}×
          </button>
        ))}
        <span className="ml-auto font-mono text-xs text-gray-500">
          Frame {frame} / {Math.max(totalFrames - 1, 0)}
        </span>
      </div>
    </div>
  );
};

export default PlaybackBar;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/replay/PlaybackBar.tsx
git commit -m "feat(replay): PlaybackBar component"
```

---

## Task 11: Frontend — Update `ReplayHeader.tsx`

**Files:**
- Modify: `frontend/src/components/replay/ReplayHeader.tsx`

- [ ] **Step 1: Replace the file**

Overwrite `frontend/src/components/replay/ReplayHeader.tsx`:

```tsx
import React from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import Logo from "@/components/Logo";
import { ReplayStatus } from "@/hooks/useReplayPlayback";

interface Props {
  status: ReplayStatus;
  repoId: string | null;
  episode: number | null;
}

const STATUS_DOT: Record<ReplayStatus, string> = {
  idle: "bg-slate-500",
  loading: "bg-blue-500 animate-pulse",
  playing: "bg-green-500",
  paused: "bg-amber-500",
  ended: "bg-slate-500",
  error: "bg-red-500",
};

const STATUS_LABEL: Record<ReplayStatus, string> = {
  idle: "Idle",
  loading: "Loading…",
  playing: "Playing",
  paused: "Paused",
  ended: "Ended",
  error: "Error",
};

const ReplayHeader: React.FC<Props> = ({ status, repoId, episode }) => {
  const navigate = useNavigate();
  const detail = status === "playing" || status === "paused"
    ? ` • ${repoId} ep ${episode}`
    : "";

  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-4 text-3xl">
        <Button variant="ghost" size="icon" onClick={() => navigate("/")} className="text-slate-400 hover:bg-slate-800 hover:text-white rounded-lg">
          <ArrowLeft className="w-5 h-5" />
        </Button>
        <Logo />
        <h1 className="font-bold text-white text-2xl">Replay Dataset</h1>
      </div>
      <div className="flex items-center gap-3">
        <div className={`w-3 h-3 rounded-full ${STATUS_DOT[status]}`}></div>
        <span className="font-semibold text-gray-400">
          {STATUS_LABEL[status]}{detail}
        </span>
      </div>
    </div>
  );
};

export default ReplayHeader;
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/replay/ReplayHeader.tsx
git commit -m "refactor(replay): drive ReplayHeader status from useReplayPlayback"
```

---

## Task 12: Frontend — Rewrite `ReplayDataset.tsx` (full integration)

**Files:**
- Rewrite: `frontend/src/pages/ReplayDataset.tsx`

- [ ] **Step 1: Replace the page**

Overwrite `frontend/src/pages/ReplayDataset.tsx`:

```tsx
import React, { useEffect, useState } from "react";
import ReplayHeader from "@/components/replay/ReplayHeader";
import DatasetCombobox from "@/components/replay/DatasetCombobox";
import EpisodeList from "@/components/replay/EpisodeList";
import VideoGrid from "@/components/replay/VideoGrid";
import PlaybackBar from "@/components/replay/PlaybackBar";
import UrdfViewer from "@/components/UrdfViewer";
import UrdfProcessorInitializer from "@/components/UrdfProcessorInitializer";
import { useReplayPlayback } from "@/hooks/useReplayPlayback";
import { DatasetItem, EpisodeItem } from "@/lib/replayApi";

const ReplayDataset: React.FC = () => {
  const replay = useReplayPlayback();

  const [datasets, setDatasets] = useState<DatasetItem[]>([]);
  const [datasetsLoading, setDatasetsLoading] = useState(true);

  const [selectedRepo, setSelectedRepo] = useState<string | null>(null);
  const [episodes, setEpisodes] = useState<EpisodeItem[]>([]);
  const [episodesLoading, setEpisodesLoading] = useState(false);
  const [episodesError, setEpisodesError] = useState<string | null>(null);
  const [selectedEpisode, setSelectedEpisode] = useState<number | null>(null);

  // Load datasets on mount.
  useEffect(() => {
    setDatasetsLoading(true);
    replay.listDatasets()
      .then(setDatasets)
      .catch(() => setDatasets([]))
      .finally(() => setDatasetsLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load episodes when repo changes.
  useEffect(() => {
    setSelectedEpisode(null);
    setEpisodes([]);
    setEpisodesError(null);
    if (!selectedRepo) return;
    setEpisodesLoading(true);
    replay.listEpisodes(selectedRepo)
      .then((r) => setEpisodes(r.episodes))
      .catch((e) => setEpisodesError(e.message || "Failed to load episodes"))
      .finally(() => setEpisodesLoading(false));
  }, [selectedRepo]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-start replay when an episode is picked. The hook's `start` internally stops any running session first.
  useEffect(() => {
    if (selectedRepo && selectedEpisode !== null) {
      replay.start(selectedRepo, selectedEpisode);
    }
  }, [selectedRepo, selectedEpisode]); // eslint-disable-line react-hooks/exhaustive-deps

  const { state } = replay;
  const disabled = state.status === "idle" || state.status === "loading";

  return (
    <div className="min-h-screen bg-black text-white flex flex-col p-4 sm:p-6 lg:p-8 gap-6">
      <ReplayHeader status={state.status} repoId={state.repoId} episode={state.episode} />

      <div className="grid lg:grid-cols-2 gap-6">
        <DatasetCombobox
          datasets={datasets}
          loading={datasetsLoading}
          value={selectedRepo}
          onChange={setSelectedRepo}
        />
        <EpisodeList
          episodes={episodes}
          selected={selectedEpisode}
          loading={episodesLoading}
          error={episodesError}
          onSelect={setSelectedEpisode}
        />
      </div>

      <div className="bg-gray-900 rounded-lg p-4 border border-gray-700 min-h-[50vh]">
        <UrdfProcessorInitializer />
        <UrdfViewer />
      </div>

      <VideoGrid cameras={state.cameras} registerRefs={replay.setVideoRefs} />

      <PlaybackBar
        paused={state.paused}
        frame={state.frame}
        totalFrames={state.totalFrames}
        fps={state.fps}
        speed={state.speed}
        disabled={disabled}
        onPlay={replay.resume}
        onPause={replay.pause}
        onStop={replay.stop}
        onSeek={replay.seek}
        onSpeedChange={replay.setSpeed}
      />

      {state.error && (
        <div className="rounded-md border border-red-700 bg-red-950/40 text-red-200 p-3 text-sm">
          {state.error}
        </div>
      )}
    </div>
  );
};

export default ReplayDataset;
```

- [ ] **Step 2: Run `lelab --dev` and validate the page in the browser**

Open http://localhost:8080/replay-dataset in the browser. Verify:

1. The dataset combobox loads — your own datasets appear (or none if not logged in to HF). "Use custom repo ID…" appears at the bottom of the dropdown.
2. Pick a known small public dataset by typing its repo ID via "Use custom repo ID…" (e.g. `lerobot/aloha_sim_insertion_human`). The episode list populates with durations.
3. Click an episode. Within a few seconds the URDF arm should start moving and one or more videos should appear and play in the grid below.
4. Pause: arm stops moving, videos pause. Resume: both continue.
5. Drag the seek slider: arm jumps to that frame, videos jump too within ~0.2s.
6. Change speed to 4×: both arm and video play 4× faster (frame counter increments faster, video `playbackRate` reflects it).
7. Stop: arm and videos stop, page returns to idle state.
8. Navigate back to the landing page and back to /replay-dataset — no orphaned backend session (check `curl http://localhost:8000/replay/status` shows `"active": false`).

If a video tile shows but never plays, check the browser network tab — the `<video>` request to `huggingface.co/datasets/.../resolve/main/...` may be 401 (private dataset, not yet supported) or 404 (template path mismatch — check the backend logs).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ReplayDataset.tsx
git commit -m "feat(replay): wire new replay page end-to-end"
```

---

## Task 13: Cleanup — Delete unused replay components

**Files:**
- Delete: `frontend/src/components/replay/DatasetSelector.tsx`
- Delete: `frontend/src/components/replay/EpisodePlayer.tsx`
- Delete: `frontend/src/components/replay/PlaybackControls.tsx`
- Delete: `frontend/src/components/replay/ReplayVisualizer.tsx`

- [ ] **Step 1: Verify nothing imports these files**

```bash
grep -rn "DatasetSelector\|EpisodePlayer\|PlaybackControls\|ReplayVisualizer" frontend/src --include="*.ts" --include="*.tsx"
```

Expected: no matches (only definitions, which we're about to delete).

- [ ] **Step 2: Delete the files**

```bash
git rm frontend/src/components/replay/DatasetSelector.tsx \
       frontend/src/components/replay/EpisodePlayer.tsx \
       frontend/src/components/replay/PlaybackControls.tsx \
       frontend/src/components/replay/ReplayVisualizer.tsx
```

- [ ] **Step 3: Reload the dev server, sanity-check the page still loads**

Open http://localhost:8080/replay-dataset. The page should still render and behave the same as Task 12.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(replay): remove unused mock components"
```

---

## Self-review notes (filled in after writing the plan)

**Spec coverage:**
- Goal — covered across tasks 1-12.
- Non-goals — preserved (no physical robot path, no private-dataset proxy, no multi-episode chains, no editing).
- User flow steps 1-7 — Task 12 wires every one: mount → datasets, pick → episodes, pick episode → start, controls (pause/seek/speed), unmount → stop.
- Backend "list_user_datasets" — Task 1.
- Backend "get_episode_list" — Task 1 + Task 2 (route).
- Backend "get_replay_assets" — Task 1.
- Backend "rewrite app/replaying.py" — Task 3 + Task 4 (routes).
- Concurrency guard — Task 3.
- Joint name `.pos` stripping — Task 3 (`_strip_pos_suffix`).
- WS frame field — Task 3 (ticker), Task 6 (hook reads it).
- Videos: direct HF URLs — Task 1 (`get_replay_assets`), Task 9 (consumer).
- Playback controls (play/pause/seek/speed) — Tasks 6, 10, 12.
- Status indicator — Task 11.
- Error handling for unauthenticated `/datasets` (returns `[]`) — Task 1.
- Error handling for non-LeRobot datasets (404) — Task 2.
- Cleanup of dead components — Task 13.

**Type consistency:**
- `StartReplayRequest` / `ReplayControlRequest` Pydantic shapes match the `replayApi.ts` POST bodies (`repo_id`, `episode`; `action`, `value`).
- `StartReplayResponse` matches `handle_start_replay` return shape (`success`, `joint_names`, `cameras`, `fps`, `num_frames`, optional `message`).
- `CameraItem` shape (`{key, url}`) matches `get_replay_assets` output.
- WS message shape `{type: "joint_update", joints, timestamp, frame}` matches the existing `useRealTimeJoints` hook (it ignores `frame`, which is what we want) and the new `useReplayPlayback` hook (which reads `frame`).
