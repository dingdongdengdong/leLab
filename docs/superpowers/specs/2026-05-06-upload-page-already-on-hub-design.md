# Upload page: "already on Hub" mode

## Problem

When a user picks a dataset that exists both locally and on the Hub
(`source === "both"`) from the Landing dropdown, the Upload page
currently shows the full upload UI: tags input, privacy toggle, "Upload
to HuggingFace Hub" button, "Skip Upload" button, and the
"About HuggingFace Hub Upload" info box. None of that applies — the
dataset is already on the Hub.

## Goals

- For `source === "both"` arrivals on the Upload page, hide the
  upload-related controls and replace them with a single "View on
  Hugging Face Hub" button that opens the dataset in the HF
  visualize_dataset Space.
- Leave the Dataset Summary card and the header (Back to Home + Trash
  with its existing confirmation dialog) untouched.
- For `source === "local"` and undefined-source arrivals (e.g. the
  recording flow), keep the existing behavior unchanged.

## Non-goals

- Changing the page title between modes.
- Refactoring the duplicated HF-viewer URL builder (already lives in
  the success-state branch; will exist in two places in the form
  branch too).
- Auto-deleting the local copy when the user opens a "both" dataset.
  The trash button already in the header handles that on demand.

## Design

### 1. Pass `source` via navigation state from Landing

In [Landing.tsx](../../../frontend/src/pages/Landing.tsx),
`handlePickExisting` currently passes only the repo id:

```tsx
navigate("/upload", {
  state: { datasetInfo: { dataset_repo_id: item.repo_id } },
});
```

Extend it to include the source:

```tsx
navigate("/upload", {
  state: {
    datasetInfo: {
      dataset_repo_id: item.repo_id,
      source: item.source,
    },
  },
});
```

Other entry points to `/upload` (currently none in code; the recording
flow's `navigate("/upload", ...)` was removed in the user's
in-progress edits) will continue to work — `source` is optional.

### 2. Track `source` in Upload page state

In [Upload.tsx](../../../frontend/src/pages/Upload.tsx) extend the
local `DatasetInfo` interface:

```ts
import { DatasetSource } from "@/lib/replayApi";

interface DatasetInfo {
  dataset_repo_id: string;
  single_task: string;
  num_episodes: number;
  saved_episodes?: number;
  session_elapsed_seconds?: number;
  fps?: number;
  total_frames?: number;
  robot_type?: string;
  source?: DatasetSource;
}
```

Carry the source through the existing fetch flow:

- The success path (`/dataset-info` returned `success: true`) already
  spreads `data` into the new state. Backend doesn't return `source`,
  so add it explicitly:

  ```tsx
  setDatasetInfo({
    ...data,
    saved_episodes: data.num_episodes,
    session_elapsed_seconds: initialDatasetInfo.session_elapsed_seconds || 0,
    source: initialDatasetInfo.source,
  });
  ```

- The fallback path (`setDatasetInfo(initialDatasetInfo)`) already
  preserves `source` because it's part of `initialDatasetInfo`.

### 3. Helper for the viewer URL

Add a small helper near `formatDuration`:

```tsx
const openInHubViewer = (repoId: string) => {
  const spacePath = `/spaces/lerobot/visualize_dataset?path=${encodeURIComponent(`/${repoId}`)}`;
  // The user owns/manages the dataset (it appears under their hub
  // listing), so login-redirect always works whether public or
  // private. Avoids passing `private` through navigation state.
  const target = `https://huggingface.co/login?next=${encodeURIComponent(spacePath)}`;
  window.open(target, "_blank", "noopener,noreferrer");
};
```

The success-state branch keeps its own near-identical inline call (it
uses `uploadConfig.private` to decide the redirect). Two copies is
under the refactor threshold.

### 4. Conditional render in the form (`!uploadSuccess`) branch

Introduce a `const isAlreadyOnHub = datasetInfo.source === "both";`
gate inside the form-branch. Apply it in three places:

- **Upload Configuration card** ([Upload.tsx:391-444](../../../frontend/src/pages/Upload.tsx#L391-L444))
  — wrap with `{!isAlreadyOnHub && (...)}`.
- **Action button pair** ([Upload.tsx:446-474](../../../frontend/src/pages/Upload.tsx#L446-L474))
  — replace with a conditional. When `isAlreadyOnHub`, render only:

  ```tsx
  <div className="flex flex-col sm:flex-row gap-4 justify-center">
    <Button
      onClick={() => openInHubViewer(datasetInfo.dataset_repo_id)}
      className="bg-blue-500 hover:bg-blue-600 text-white font-semibold py-4 px-8 text-lg"
    >
      <ExternalLink className="w-5 h-5 mr-2" />
      View on Hugging Face Hub
    </Button>
  </div>
  ```

  When not, render the existing Upload + Skip pair unchanged.
- **About-Hub info box** ([Upload.tsx:476-504](../../../frontend/src/pages/Upload.tsx#L476-L504))
  — wrap with `{!isAlreadyOnHub && (...)}`.

The Dataset Summary card stays unconditional.

### 5. Edge cases

- `source === undefined`: behaves identically to `source === "local"`
  (no `isAlreadyOnHub` switch). Recording-flow arrivals (when that
  flow is restored) keep working.
- User clicks Trash on a "both" dataset: already wired — calls
  `/delete-dataset`, removes the local copy, navigates home. The Hub
  copy is untouched.
- User uploads (impossible in this mode — Upload button hidden) — N/A.
- Backend `/dataset-info` returns `success: false`: the fallback
  `initialDatasetInfo` still carries `source`, so `isAlreadyOnHub`
  resolves correctly even when summary fields are sparse.

## Files changed

- [frontend/src/pages/Landing.tsx](../../../frontend/src/pages/Landing.tsx)
  — pass `source` in navigation state.
- [frontend/src/pages/Upload.tsx](../../../frontend/src/pages/Upload.tsx)
  — extend `DatasetInfo`, import `DatasetSource`, add
  `openInHubViewer` helper, gate three render blocks on
  `isAlreadyOnHub`.

## Validation

Manual (no test suite per CLAUDE.md). Run `lelab --dev`:

1. Pick a Hub-only dataset on Landing → opens HF viewer in new tab
   (unchanged).
2. Pick a Local-only dataset on Landing → /upload shows Upload
   Configuration card, Upload + Skip buttons, About-Hub info box
   (unchanged).
3. Pick a "both" dataset on Landing → /upload shows: Dataset Summary,
   single "View on Hugging Face Hub" button, header still has Back to
   Home + Trash. No Upload Configuration card, no Skip button, no
   info box.
4. Click "View on Hugging Face Hub" → new tab opens the HF Space at
   the dataset path.
5. Click Trash on a "both" dataset, confirm → local copy removed,
   navigates home.
