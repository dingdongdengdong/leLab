# Local datasets in the dataset dropdown

## Problem

The dataset dropdown only lists Hugging Face Hub datasets owned by the
logged-in user (and their orgs). Datasets that exist only in the local
LeRobot cache are invisible in the UI, so users can't pick them for
replay, training, or upload from the recording flow.

Users want both sources listed and visually separated.

## Goals

- Surface local LeRobot datasets alongside Hub datasets in every
  dataset dropdown (recording, replay, training).
- Group entries into two sections: Local and Hugging Face.
- On Landing, route picks to the existing Upload page when the dataset
  exists locally so users can choose to push to Hub or not.
- Keep the Hub-only viewer behavior on Landing for datasets that don't
  exist locally.

## Non-goals

- Auto-uploading local datasets when a cloud training job starts (the
  HF cloud runner needs follow-up work; tracked as a TODO comment in
  the training-side code, not in this design).
- Building a local dataset viewer page.
- Listing dirs in `~/.cache/huggingface/lerobot/` that don't contain a
  valid LeRobot dataset structure.

## Design

### 1. Backend: local dataset detection

Extend [app/dataset_browser.py](../../../app/dataset_browser.py) with a
`list_local_datasets()` function:

- **Root**: `Path(os.environ.get("HF_LEROBOT_HOME", "~/.cache/huggingface/lerobot")).expanduser()`.
  This mirrors what LeRobot uses, so any user override is respected.
  If the path doesn't exist, return `[]`.
- **Dataset signature**: a directory is a LeRobot dataset iff
  `<dir>/meta/info.json` exists. This is the canonical marker for the
  v2.0+ format LeRobot writes.
- **Walk strategy**:
  - Iterate top-level entries under the root.
  - Skip non-directories.
  - If a top-level dir has `meta/info.json`, it's a single-segment
    local dataset (e.g. `plop`). Record `repo_id = "plop"` and **do
    not descend** into it (a dataset's children are never themselves
    datasets).
  - Otherwise, descend one level: for each subdir with
    `meta/info.json`, record `repo_id = "<top>/<sub>"` (e.g.
    `Metabolik/cam_test`).
  - Do not recurse deeper. LeRobot does not nest datasets beyond two
    segments.
- **Per-entry fields**:
  - `repo_id`: relative path from the root, as derived above.
  - `last_modified`: directory `mtime` as ISO-8601 (use the dataset
    dir, not its parent or its contents).
  - `private`: always `False` for local. The field is kept on the
    payload for shape compatibility with Hub entries.
- Sort by `last_modified` descending, mirroring `list_user_datasets`.

The walk should be defensive against permission errors and broken
symlinks (`try/except OSError` around each `is_dir()`/`exists()`
check, log at debug, skip the entry).

### 2. Backend: merged endpoint

Extend the existing `GET /datasets` ([app/main.py:337-340](../../../app/main.py))
to return the merged list. No new route — keeps the frontend hook
unchanged in shape.

Each entry in the response gains a `source` field:

```python
class DatasetSource(str, Enum):
    LOCAL = "local"
    HUB = "hub"
    BOTH = "both"
```

Merge rules:

- Build a dict keyed by `repo_id`.
- Insert all `list_user_datasets()` results with `source="hub"`.
- For each `list_local_datasets()` result:
  - If `repo_id` is already present, set its `source` to `"both"` and
    keep `last_modified = max(hub_lm, local_lm)` (compare ISO strings;
    `None`-safe).
  - Otherwise insert with `source="local"`.
- Sort the merged list by `last_modified` descending.

If `whoami()` fails (no HF token), `list_user_datasets()` already
returns `[]`. The endpoint still returns local datasets — local
listing must not depend on Hub auth.

### 3. Frontend: type and hook

Update [frontend/src/lib/replayApi.ts](../../../frontend/src/lib/replayApi.ts):

```ts
export type DatasetSource = "local" | "hub" | "both";

export interface DatasetItem {
  repo_id: string;
  last_modified: string | null;
  private: boolean;
  source: DatasetSource;
}
```

`useDatasets` ([frontend/src/hooks/useDatasets.ts](../../../frontend/src/hooks/useDatasets.ts))
needs no logic change — it just passes the new shape through.

### 4. Frontend: dropdown grouping

Both consumers split into two `CommandGroup`s:

- **Local** group, heading `"Local"`. Includes entries where `source`
  is `"local"` or `"both"`. Rendered first.
- **Hugging Face** group, heading `"Hugging Face"`. Includes entries
  where `source` is `"hub"`. Rendered second.

A `"both"` entry appears in **Local only**, with a small `"on Hub"`
badge (gray text, similar styling to the existing `private` amber
badge). It does **not** also appear in the Hugging Face group. This
keeps the list short and matches the intent: the actionable affordance
(record more, upload, replay, train) lives with the local copy.

If a group is empty (e.g. user has no local datasets, or no Hub
datasets), omit the `CommandGroup` entirely rather than showing an
empty heading.

The existing private-badge / search / "Use custom repo ID…" /
"Create new" affordances stay as-is. Search must match across both
groups (the existing `<CommandInput>` already does this since it
filters at the `CommandItem` level).

Files touched:

- [frontend/src/components/replay/DatasetCombobox.tsx](../../../frontend/src/components/replay/DatasetCombobox.tsx)
- [frontend/src/components/landing/DatasetPicker.tsx](../../../frontend/src/components/landing/DatasetPicker.tsx)

### 5. Landing click behavior

In [frontend/src/pages/Landing.tsx](../../../frontend/src/pages/Landing.tsx),
replace the single `openDatasetInViewer` action that's wired to both
`onPickExisting` and `onOpenCustom`:

- Update `DatasetPicker`'s `onPickExisting` prop signature from
  `(repoId: string) => void` to `(item: DatasetItem) => void` so
  Landing has the `source` to branch on. `onOpenCustom` stays
  string-only — custom repo IDs typed by the user are always treated
  as Hub paths.
- New Landing handler:
  - `source === "hub"` (or unknown for custom-typed) → existing
    `openDatasetInViewer` behavior (open
    `huggingface.co/spaces/lerobot/visualize_dataset?path=/<repo_id>`,
    with login redirect for private/unknown).
  - `source === "local"` or `"both"` → `navigate("/upload", { state:
    { datasetInfo: { dataset_repo_id: repoId } } })`. The Upload page
    already fetches full metadata via `POST /dataset-info`
    ([Upload.tsx:74-81](../../../frontend/src/pages/Upload.tsx#L74-L81))
    and renders the upload-or-skip UI.

`onCreateNew` is unchanged.

### 6. Replay and Training behavior

No behavioral changes for these consumers. `DatasetCombobox`'s
`onChange(repoId)` still just sets the selected ID. Local datasets
already work for:

- Replay: `LeRobotDataset(repo_id)` resolves the local copy.
- Training: same `LeRobotDataset(repo_id)` resolution path.

For HF cloud training jobs specifically, picking a local-only dataset
will fail at job start because the runner can't see local files. Add
a TODO comment in [app/runners/hf_cloud.py](../../../app/runners/hf_cloud.py)
noting this is an open question — out of scope here.

## Edge cases

- **No HF token**: `list_user_datasets()` returns `[]`. Local list
  still works; dropdown shows only the Local group.
- **No local cache directory**: `list_local_datasets()` returns `[]`.
  Dropdown shows only the Hugging Face group.
- **Both empty**: existing `CommandEmpty` rendering kicks in
  unchanged.
- **Junk dirs in cache** (`cvkjln/`, `dgf/`, etc. without
  `meta/info.json`): filtered out, not shown.
- **Single-segment names with valid `meta/info.json`** (e.g. `plop/`):
  shown with `repo_id = "plop"`. Picking on Landing routes to Upload;
  uploading without a `<owner>/` prefix will likely require the user
  to rename — that's an Upload-page concern, not a dropdown concern.
- **Same `repo_id` exists locally and on Hub but with different
  contents**: the dropdown can't tell. We trust the path — local
  always wins for the click action. The Upload page will warn if it
  tries to overwrite, but that's existing behavior.
- **`HF_LEROBOT_HOME` env var set**: detection follows the env, so a
  user with a custom cache location is supported.
- **Permission errors / broken symlinks** under the cache root: log,
  skip the offending entry, continue.

## Validation

There's no test suite in this repo (per CLAUDE.md). Validate by
running `lelab` and:

1. Open Landing. Confirm the dataset dropdown shows two groups.
2. Confirm a known local-only dataset appears under Local.
3. Confirm a Hub-only dataset appears under Hugging Face.
4. Confirm a dataset present in both shows once under Local with an
   "on Hub" badge.
5. Click a local entry → lands on `/upload` with the right repo_id.
6. Click a Hub-only entry → opens viewer in new tab (unchanged).
7. Open the Replay page → local datasets selectable, replay starts.
8. Open the Training page → local datasets selectable in the combobox.
9. Confirm dropdown still works when not logged in to HF (only Local
   group shows).

## Files changed

- [app/dataset_browser.py](../../../app/dataset_browser.py) — add
  `list_local_datasets()`, add merged `list_all_datasets()`.
- [app/main.py](../../../app/main.py) — `GET /datasets` calls the
  merged function.
- [frontend/src/lib/replayApi.ts](../../../frontend/src/lib/replayApi.ts) —
  add `DatasetSource`, extend `DatasetItem`.
- [frontend/src/components/replay/DatasetCombobox.tsx](../../../frontend/src/components/replay/DatasetCombobox.tsx) —
  split into two groups; render `"on Hub"` badge for `source: "both"`.
- [frontend/src/components/landing/DatasetPicker.tsx](../../../frontend/src/components/landing/DatasetPicker.tsx) —
  same grouping; pass `DatasetItem` (not just `repo_id`) to
  `onPickExisting`.
- [frontend/src/pages/Landing.tsx](../../../frontend/src/pages/Landing.tsx) —
  branch click action on `source`.
- [app/runners/hf_cloud.py](../../../app/runners/hf_cloud.py) — add
  TODO comment about local-dataset handling for cloud jobs.
