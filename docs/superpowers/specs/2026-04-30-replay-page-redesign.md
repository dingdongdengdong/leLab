# Replay Dataset page — Hub-backed playback

**Status:** spec
**Date:** 2026-04-30
**Author:** Nicolas Rabault (with Claude)

## Goal

Turn the Replay Dataset page from a mock-data placeholder into a working tool that lets the user browse their Hugging Face datasets, pick an episode, and see it play back in the URDF viewer with synced camera videos. No physical robot involved in this iteration — the existing `lerobot.scripts.lerobot_replay`-based flow is removed; "send actions to the physical follower" is a separate future feature.

## Non-goals

- Driving the physical SO-101 follower from a recorded episode. (Future feature; will add a separate endpoint that consumes frames from the same playback session.)
- Streaming videos for **private** datasets the user has access to via HF token. The browser has no token, so direct HF resolve URLs would 401. v1 supports public datasets only; private-dataset support comes via a backend proxy later.
- Multi-episode chained playback. v1 plays one episode at a time.
- Editing or trimming episodes.

## User flow

1. User opens **/replay-dataset**.
2. Dataset combobox shows the logged-in user's datasets and their orgs' datasets, plus a "Use custom repo ID..." entry that switches the same control to a text input.
3. After picking a dataset, an episode list appears with one row per episode showing index and duration (formatted `HH:MM:SS` so multi-day episodes read cleanly).
4. User picks an episode, presses **Play**.
5. URDF viewer animates from the dataset's action stream; one video per camera plays in a grid below the viewer, synced to the same playback timeline.
6. User can pause, scrub the seek bar, change speed (1×, 2×, 4×, 10×), or stop.
7. Navigating away or unmounting the page stops the backend session automatically.

## Architecture overview

```
┌────────────────────────┐     ┌──────────────────────────┐
│ Frontend               │     │ Backend                  │
│                        │     │                          │
│ Replay page            │ GET │ /hf-auth-status (exists) │
│  ├ DatasetCombobox ────┼────►│ /datasets       (NEW)    │
│  ├ EpisodeList ────────┼────►│ /episodes/{repo}(NEW)    │
│  ├ URDF viewer ◄───────┼─WS──│ /ws/joint-data  (exists) │
│  ├ VideoGrid    ◄──────┼─────│ direct HF resolve URLs   │
│  └ PlaybackBar  ───────┼────►│ /replay/start   (NEW)    │
│                        │POST │ /replay/control (NEW)    │
│                        │POST │ /replay/stop    (NEW)    │
└────────────────────────┘     └──────────────────────────┘
```

A single backend session owns replay state. A ticker thread emits joint values over the existing `/ws/joint-data` broadcast queue at `fps × speed`. The frontend's existing `useRealTimeJoints` hook applies them to the URDF viewer with no changes. Camera videos stream straight from `https://huggingface.co/datasets/{repo}/resolve/main/...` URLs returned by the backend; the frontend nudges each `<video>`'s `currentTime` to track the backend's frame index when drift exceeds a small threshold.

Concurrency: replay is mutually exclusive with teleop and recording, using the same "return `{success: false, message}` if another mode is active" pattern those modules already use. They all share the URDF viewer and the WS broadcast channel; nothing else makes sense.

## Backend

### New module: `app/dataset_browser.py`

Pure HF Hub queries, no robot. Reuses the `whoami()` call already in `app/hf_auth.py`.

**`list_user_datasets() -> list[dict]`**

1. `info = whoami()`. If unauthenticated, return `[]` (the page should fall back to the free-form input only).
2. Authors to query: `[info["name"]] + [o["name"] for o in info.get("orgs", [])]`.
3. For each author, `HfApi().list_datasets(author=author, limit=200)`. Aggregate.
4. Return `[{repo_id, last_modified, private}]` sorted by `last_modified` descending.

No format filter — we don't try to detect "is this a LeRobot dataset?" at list time. If the user picks a non-LeRobot repo, `/episodes/{repo}` will fail cleanly when `meta/episodes.jsonl` isn't found, and the UI surfaces that error.

**`get_episode_list(repo_id: str) -> dict`**

1. `info_path = hf_hub_download(repo_id, "meta/info.json", repo_type="dataset")`. Read `fps` and total `total_episodes`.
2. `episodes_path = hf_hub_download(repo_id, "meta/episodes.jsonl", repo_type="dataset")`. Parse one JSON per line.
3. Return `{fps, episodes: [{episode_index, length, tasks, duration_seconds}]}` where `duration_seconds = length / fps`.

**`get_replay_assets(repo_id: str, episode: int) -> dict`**

1. Read the cached `meta/info.json`.
2. Find action feature: the entry under `features` whose key is `action`. Capture its `names` (joint names in dataset order).
3. Find video features: every entry where `dtype == "video"`. Capture each one's key.
4. Resolve video URL for each camera: `info["video_path"]` is a templated path like `videos/chunk-{episode_chunk:03d}/{video_key}/episode_{episode_index:06d}.mp4`. Compute `episode_chunk = episode // chunks_size` (also from `info.json`), substitute, prefix with `https://huggingface.co/datasets/{repo_id}/resolve/main/`.
5. Return `{joint_names, cameras: [{key, url}], fps, num_frames}` where `num_frames` comes from the matching entry in `episodes.jsonl`.

This computes URLs from the dataset's declared template rather than guessing — works across LeRobot dataset versions that share the same `info.json` schema.

### Rewrite: `app/replaying.py`

**Delete** the existing `ReplayRequest` / `run_replay_directly` / `lerobot.scripts.lerobot_replay` flow. It drives the physical follower, isn't reachable from the new page, and the future "send to robot" feature will need a different shape (frame-by-frame from the current playback session, not whole-episode from a CLI config) so the existing code is not reusable as-is. Per CLAUDE.md "no dead code".

**New shape:**

```python
@dataclass
class ReplayState:
    active: bool = False
    repo_id: str | None = None
    episode: int | None = None
    frame: int = 0
    total_frames: int = 0
    fps: float = 30.0
    speed: float = 1.0  # multiplier
    paused: bool = False
    joint_names: list[str] = field(default_factory=list)
    actions: np.ndarray | None = None  # (T, J) float32, in-memory for the current episode

# guarded by a Lock; stop_event is a threading.Event
```

**`handle_start_replay(req, manager)`**

1. If `replay_state.active` or another mode (teleop/record) is active, refuse.
2. Resolve assets via `dataset_browser.get_replay_assets(req.repo_id, req.episode)` — gets joint names and the list of camera URLs.
3. Download just this episode's parquet via `hf_hub_download` using `info["data_path"]` template (e.g. `data/chunk-XXX/episode_YYYYY.parquet`).
4. Read the parquet with `pyarrow`. Pull the `action` column as `np.ndarray` shape `(T, J)`.
5. Populate state, start ticker thread, return `{success, joint_names, cameras, fps, num_frames}`.

**Ticker thread**

```python
while not stop_event.is_set():
    if state.paused:
        time.sleep(0.05)
        continue
    if state.frame >= state.total_frames:
        state.paused = True
        continue
    row = state.actions[state.frame]
    joints = dict(zip(state.joint_names, row.tolist()))
    manager.broadcast_joint_data_sync({
        "type": "joint_update",
        "joints": joints,
        "timestamp": state.frame / state.fps,
        "frame": state.frame,
    })
    state.frame += 1
    time.sleep(1.0 / (state.fps * state.speed))
```

The `frame` field on the broadcast is what the frontend uses to drive video sync — it's strictly increasing during play, jumps on seek, and lets the frontend reason about playback position without a second channel.

**`handle_replay_control(action, value)`**

`action ∈ {"pause", "resume", "seek", "set_speed"}`.

- `pause` / `resume` toggle `state.paused`.
- `seek` clamps `value` (a frame index) to `[0, total_frames - 1]` and assigns to `state.frame`.
- `set_speed` clamps `value` to `[0.25, 16.0]` and assigns to `state.speed`.

**`handle_stop_replay()`**

Set `stop_event`, join ticker thread (timeout ~1s), reset state. Idempotent.

### Joint name mapping

The dataset's action feature declares `names` like `["shoulder_pan.pos", "shoulder_lift.pos", "elbow_flex.pos", "wrist_flex.pos", "wrist_roll.pos", "gripper.pos"]` (newer datasets) or just `["shoulder_pan", ...]`. The URDF's joint names are `shoulder_pan`, `shoulder_lift`, etc.

When forming the `joints` dict, strip a trailing `.pos` from each name. If a remapping is needed beyond that, do it once at session start in a small helper, not per-tick.

### `app/main.py` wiring

Add five routes following existing patterns:

- `GET  /datasets` → `dataset_browser.list_user_datasets()`
- `GET  /episodes/{repo_id:path}` → `dataset_browser.get_episode_list(repo_id)`
- `POST /replay/start` → `replaying.handle_start_replay(req, manager)`
- `POST /replay/control` → `replaying.handle_replay_control(req)`
- `POST /replay/stop` → `replaying.handle_stop_replay()`

`/start-replay`, `/stop-replay`, `/replay-status`, `/replay-logs` are removed (along with their underlying handlers in the deleted code).

## Frontend

### Page restructure

`frontend/src/pages/ReplayDataset.tsx`:
- Remove `mockDatasets` / `mockEpisodes`.
- Layout (top → bottom): `ReplayHeader` (status indicator), `DatasetCombobox` + `EpisodeList` (side by side), `UrdfViewer` (full width), `VideoGrid` (responsive grid), `PlaybackBar` (sticky-ish at the bottom of the page).

### New components under `frontend/src/components/replay/`

**`DatasetCombobox.tsx`**

- Searchable combobox (use existing `cmdk`/shadcn pieces — already in the project).
- Items: result of `GET /datasets`, sorted by `last_modified` desc, showing `repo_id` and a relative timestamp.
- Sentinel item at the bottom: "Use custom repo ID...". Selecting it swaps the trigger into a text input that validates `org/name` shape on blur.
- Emits `onChange(repo_id | null)`.

**`EpisodeList.tsx`** (replaces `EpisodePlayer.tsx`)

- Fetches `GET /episodes/{repo}` whenever the selected repo changes (debounced 300 ms).
- Renders a scrollable list, one row per episode: `Episode {n}` + duration formatted via a small helper:
  - `< 60s` → `Xs`
  - `< 1h` → `MM:SS`
  - `< 24h` → `HH:MM:SS`
  - `>= 24h` → `Nd HH:MM`
- Click sets selected episode (lifted state in the page).

**`VideoGrid.tsx`**

- Renders one tile per camera in `replay_assets.cameras` using a responsive grid (1 col / 2 col / 4 col by breakpoint).
- Each tile = `<video>` with `preload="metadata"`, `muted`, no native controls (the page-level PlaybackBar drives them).
- Forwards an array of refs upward so the playback hook can call `currentTime`/`playbackRate` on each.

**`PlaybackBar.tsx`** (replaces `PlaybackControls.tsx`)

- Play/pause button (state from the hook below).
- Frame-based seek slider, label formats current/total as time.
- Speed picker (segmented buttons: 1× / 2× / 4× / 10×).
- "Frame N / M" readout.

### New hook: `frontend/src/hooks/useReplayPlayback.ts`

Owns client-side coordination.

State: `{ status, repoId, episode, frame, totalFrames, fps, speed, paused, cameras, joint_names }`.

Actions (each posts to the matching backend route):
- `start(repoId, episode)` → `POST /replay/start`. Stores response in state. Auto-starts video elements.
- `pause()` / `resume()` → `POST /replay/control`. Updates `paused`.
- `seek(frame)` → `POST /replay/control`. Optimistically updates `frame`, lets the next WS tick correct.
- `setSpeed(value)` → `POST /replay/control`. Updates `speed` and applies `playbackRate = value` to every video ref.
- `stop()` → `POST /replay/stop`.

WebSocket integration: the URDF viewer already subscribes to `/ws/joint-data`. To avoid coupling, expose a small "playback frame channel" as a separate listener — easiest is to have `useReplayPlayback` open its own WS to the same endpoint and read only the `frame`/`timestamp` fields, ignoring the joints. (The existing hook just forwards joints to `setJointValue`; it doesn't touch `frame`.)

Sync logic: on each tick, compute `expected = frame / fps`. For each video ref, if `Math.abs(video.currentTime - expected) > 0.2`, set `video.currentTime = expected`. On `paused` change, call `video.pause()` / `video.play()`. On `speed` change, set `video.playbackRate = speed`.

Cleanup on unmount: `await stop()`. Backend will idempotently reset.

### `ReplayHeader.tsx` tweak

Show one of:
- Idle (gray dot)
- Playing `{repo_id}` ep `{n}` (green dot)
- Paused (yellow dot)

State source: same hook.

## Data flow

1. Page mounts → `GET /datasets` populates the combobox. Auth banner remains as-is for non-authenticated users.
2. User picks dataset → `GET /episodes/{repo}` → `EpisodeList` renders.
3. User picks episode → `useReplayPlayback.start(repo, ep)` → `POST /replay/start` → backend downloads episode parquet, returns `{joint_names, cameras, fps, num_frames}`.
4. `VideoGrid` mounts video tiles with the returned URLs.
5. Backend ticker emits joint updates over `/ws/joint-data` at `fps × speed`. Each message carries `joints`, `timestamp`, `frame`.
6. URDF viewer applies `joints` (existing `useRealTimeJoints` — no changes).
7. `useReplayPlayback` reads `frame`, nudges each video ref's `currentTime` if drift > 0.2s.
8. Pause / seek / speed → `POST /replay/control`. Ticker updates state on next tick read.
9. Stop / unmount → `POST /replay/stop`.

## Error handling

- `GET /datasets` when unauthenticated: return `[]`. Combobox shows "Log in to see your datasets" + the existing HF auth banner instructs how. Free-form input still works.
- `GET /episodes/{repo}`: if `meta/episodes.jsonl` is missing or `meta/info.json` doesn't parse, return 404 with a clear message ("Not a LeRobot-format dataset"). Frontend surfaces it inline.
- `POST /replay/start` while another mode is active: return `{success: false, message}`. Frontend toasts.
- Mid-playback HF download failure: ticker stops, state goes to error, frontend shows the error and offers a retry.
- Browser `<video>` 401 (private dataset): inline "Video unavailable — private dataset video proxying not yet supported."

## Open implementation details

- `LeRobotDataset` has internal handling for some legacy dataset layouts. We're choosing the lean parquet+template path for the v1; if we hit a dataset version that breaks the assumptions, we revisit.
- The "join thread on stop" timeout (~1s) needs to be enough for the ticker to drain its current `time.sleep`. If the ticker sleeps longer than 1s at low speeds with sparse fps, we may need to shorten the inner sleep and re-check the stop event.

## Files touched

**Backend (new):**
- `app/dataset_browser.py`

**Backend (changed):**
- `app/replaying.py` — full rewrite (delete physical-robot flow, add streaming ticker)
- `app/main.py` — replace replay routes, add `/datasets` and `/episodes/{repo}`

**Frontend (changed):**
- `frontend/src/pages/ReplayDataset.tsx` — drop mocks, new layout
- `frontend/src/components/replay/ReplayHeader.tsx` — status from hook
- `frontend/src/components/replay/ReplayVisualizer.tsx` — split into URDF area + `VideoGrid`, or replaced

**Frontend (new):**
- `frontend/src/components/replay/DatasetCombobox.tsx`
- `frontend/src/components/replay/EpisodeList.tsx`
- `frontend/src/components/replay/VideoGrid.tsx`
- `frontend/src/components/replay/PlaybackBar.tsx`
- `frontend/src/hooks/useReplayPlayback.ts`

**Frontend (deleted):**
- `frontend/src/components/replay/DatasetSelector.tsx` (replaced by combobox)
- `frontend/src/components/replay/EpisodePlayer.tsx` (replaced by `EpisodeList` + `PlaybackBar`)
- `frontend/src/components/replay/PlaybackControls.tsx` (replaced by `PlaybackBar`)
