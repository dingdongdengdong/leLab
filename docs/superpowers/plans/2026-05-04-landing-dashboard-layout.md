# Landing Dashboard Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganise the LeLab landing page from a marketing stack into a working dashboard: sticky top bar + sticky dock (robot tile + 4 action buttons) + scrollable jobs grid.

**Architecture:** Frontend-only refactor in `frontend/src/`. Replace `LandingHeader` and `HfAuthBanner` with a compact sticky `LandingTopBar` containing an `HfAuthChip` that opens an `HfAuthDialog`. Inline a sticky dock in `Landing.tsx` (no new wrapper component) holding the existing `RobotConfigManager` (with tightened `RobotTile`) and `ActionList`. `JobsSection` becomes the scrolling main body. Page width grows to `max-w-7xl` (1280px). Document-level scroll only — no inner overflow container.

**Tech Stack:** React 18, TypeScript, Tailwind CSS, shadcn/ui (`Dialog`, `Button`, `Tooltip`), `lucide-react` icons, existing `useHfAuth` context.

**Spec:** [`docs/superpowers/specs/2026-05-04-landing-dashboard-layout-design.md`](../specs/2026-05-04-landing-dashboard-layout-design.md)

**Validation note:** Per `CLAUDE.md` there is no test suite or linter for this repo. Each task ends with manual verification using `lelab --dev` (Vite at `http://localhost:8080`). Don't add new tests. Use TypeScript compilation (Vite's HMR errors) as the only automated check.

---

## File Structure

```
frontend/src/pages/Landing.tsx                          (modified — re-layout)
frontend/src/components/landing/LandingTopBar.tsx       (new — sticky brand + chip bar)
frontend/src/components/landing/HfAuthChip.tsx          (new — status chip)
frontend/src/components/landing/HfAuthDialog.tsx        (new — login command modal)
frontend/src/components/landing/RobotTile.tsx           (modified — tighter padding)
frontend/src/components/landing/ActionList.tsx          (modified — drop pt-6, smaller padding)
frontend/src/components/landing/LandingHeader.tsx       (deleted)
frontend/src/components/landing/HfAuthBanner.tsx        (deleted)
```

The `--lelab-topbar-h` CSS custom property is set inline on the page root in `Landing.tsx` to keep the dock's `top` offset in sync with the top bar height.

---

## Task 1: Build `HfAuthDialog`

The dialog mirrors the content of the existing `HfAuthBanner.tsx` (login command + copy + "I've logged in — recheck") but presented inside a shadcn `Dialog`. Build this first so `HfAuthChip` can use it in Task 2.

**Files:**
- Create: `frontend/src/components/landing/HfAuthDialog.tsx`

- [ ] **Step 1: Create the file**

Path: `frontend/src/components/landing/HfAuthDialog.tsx`

```tsx
import React, { useState } from "react";
import { Check, Copy, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useHfAuth } from "@/contexts/HfAuthContext";

interface HfAuthDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const HfAuthDialog: React.FC<HfAuthDialogProps> = ({ open, onOpenChange }) => {
  const { auth, refetch } = useHfAuth();
  const [copied, setCopied] = useState(false);
  const [refetching, setRefetching] = useState(false);

  if (auth.status !== "unauthenticated") {
    return null;
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(auth.loginCommand);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (err) {
      console.warn("Clipboard write failed:", err);
    }
  };

  const handleRefetch = async () => {
    setRefetching(true);
    try {
      await refetch();
    } finally {
      setRefetching(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-gray-900 border-gray-800 text-white">
        <DialogHeader>
          <DialogTitle className="text-amber-200">
            Hugging Face CLI not configured
          </DialogTitle>
          <DialogDescription className="text-gray-400">
            Uploads, training, and replay-from-Hub require a logged-in HF CLI.
            Run this in a terminal:
          </DialogDescription>
        </DialogHeader>
        <pre className="bg-gray-950 p-3 rounded border border-gray-700 text-xs sm:text-sm overflow-x-auto flex items-center justify-between gap-2">
          <code className="text-green-400">{auth.loginCommand}</code>
          <button
            type="button"
            onClick={handleCopy}
            className="flex-shrink-0 text-gray-400 hover:text-gray-200 transition-colors"
            aria-label="Copy command"
          >
            {copied ? (
              <Check className="w-4 h-4 text-green-400" />
            ) : (
              <Copy className="w-4 h-4" />
            )}
          </button>
        </pre>
        <Button
          variant="outline"
          size="sm"
          onClick={handleRefetch}
          disabled={refetching}
          className="border-amber-700 bg-transparent text-amber-100 hover:bg-amber-900/40 hover:text-amber-50"
        >
          <RefreshCw
            className={`w-4 h-4 mr-2 ${refetching ? "animate-spin" : ""}`}
          />
          I've logged in — recheck
        </Button>
      </DialogContent>
    </Dialog>
  );
};

export default HfAuthDialog;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors. (If `tsc` is not installed standalone, run `npm run build` and stop after the typecheck phase, or rely on Vite HMR error overlay in step 4 of Task 2.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/landing/HfAuthDialog.tsx
git commit -m "feat(landing): add HfAuthDialog for login command"
```

---

## Task 2: Build `HfAuthChip`

The chip is the entry point: it shows status and opens `HfAuthDialog` when unauthenticated.

**Files:**
- Create: `frontend/src/components/landing/HfAuthChip.tsx`

- [ ] **Step 1: Create the file**

Path: `frontend/src/components/landing/HfAuthChip.tsx`

```tsx
import React, { useState } from "react";
import { Loader2 } from "lucide-react";
import { useHfAuth } from "@/contexts/HfAuthContext";
import HfAuthDialog from "./HfAuthDialog";

const HfAuthChip: React.FC = () => {
  const { auth } = useHfAuth();
  const [dialogOpen, setDialogOpen] = useState(false);

  if (auth.status === "loading") {
    return (
      <div className="inline-flex items-center gap-2 rounded-full border border-gray-800 bg-gray-900/60 px-3 py-1 text-xs text-gray-400">
        <Loader2 className="w-3 h-3 animate-spin" />
        <span>Checking HF…</span>
      </div>
    );
  }

  if (auth.status === "authenticated") {
    return (
      <div
        className="inline-flex items-center gap-2 rounded-full border border-gray-800 bg-gray-900/60 px-3 py-1 text-xs text-gray-200"
        title="Hugging Face authenticated"
      >
        <span
          className="h-2 w-2 rounded-full bg-emerald-400"
          aria-hidden="true"
        />
        <span>{auth.username}</span>
      </div>
    );
  }

  // unauthenticated
  return (
    <>
      <button
        type="button"
        onClick={() => setDialogOpen(true)}
        className="inline-flex items-center gap-2 rounded-full border border-amber-700/60 bg-amber-950/40 px-3 py-1 text-xs text-amber-100 hover:bg-amber-900/40 transition-colors"
        aria-label="Hugging Face not configured — show login instructions"
      >
        <span
          className="h-2 w-2 rounded-full bg-amber-400"
          aria-hidden="true"
        />
        <span>HF not configured</span>
      </button>
      <HfAuthDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  );
};

export default HfAuthChip;
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/landing/HfAuthChip.tsx
git commit -m "feat(landing): add HfAuthChip status indicator"
```

---

## Task 3: Build `LandingTopBar`

The sticky top bar holds the brand-mark on the left and `HfAuthChip` on the right. The bar sets the `--lelab-topbar-h` CSS custom property's expected value purely visually (the actual variable is set on the page root in Task 6 so the dock can read it).

**Files:**
- Create: `frontend/src/components/landing/LandingTopBar.tsx`

- [ ] **Step 1: Create the file**

Path: `frontend/src/components/landing/LandingTopBar.tsx`

The logo path matches the one used in the now-removed `LandingHeader`. Verify it resolves from `frontend/public/`: `ls frontend/public/lovable-uploads/` should show `5e648747-34b7-4d8f-93fd-4dbd00aeeefc.png`.

```tsx
import React from "react";
import HfAuthChip from "./HfAuthChip";

const LandingTopBar: React.FC = () => {
  return (
    <header className="sticky top-0 z-30 w-full border-b border-gray-800 bg-black/95 backdrop-blur supports-[backdrop-filter]:bg-black/70">
      <div className="mx-auto flex h-12 max-w-7xl items-center justify-between px-4">
        <div className="flex items-center gap-2">
          <img
            src="/lovable-uploads/5e648747-34b7-4d8f-93fd-4dbd00aeeefc.png"
            alt="LeLab"
            className="h-7 w-7"
          />
          <span className="text-base font-semibold tracking-tight text-white">
            LeLab
          </span>
        </div>
        <HfAuthChip />
      </div>
    </header>
  );
};

export default LandingTopBar;
```

The bar height is `h-12` (48px). This matches the value used for `--lelab-topbar-h` in Task 6 — keep these in sync if you change one.

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/landing/LandingTopBar.tsx
git commit -m "feat(landing): add sticky LandingTopBar with brand + HF chip"
```

---

## Task 4: Tighten `RobotTile` (V1 compaction)

Reduce padding and gap so the tile's height drops ~30% without touching the structure (selector row → status line → full-width Teleop button stays).

**Files:**
- Modify: `frontend/src/components/landing/RobotTile.tsx`

- [ ] **Step 1: Update outer container padding and gap**

Open `frontend/src/components/landing/RobotTile.tsx`. Find:

```tsx
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 flex flex-col gap-3 relative">
```

Replace with:

```tsx
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-3 flex flex-col gap-2 relative">
```

- [ ] **Step 2: Tighten icon button height**

In the same file, find both occurrences of (one for Configure, one for Delete):

```tsx
                  className="h-9 w-9 text-gray-300 hover:text-white"
```
and
```tsx
                  className="h-9 w-9 text-red-400 hover:text-red-300 hover:bg-red-900/20"
```

Change `h-9 w-9` to `h-8 w-8` in both. Use Edit's `replace_all: false` and target each unique string with surrounding context.

- [ ] **Step 3: Force the status line to a single line**

Find:

```tsx
        <p
          className={`text-xs text-center ${
            robot!.is_clean ? "text-green-400" : "text-amber-400"
          }`}
        >
          {status}
        </p>
```

Replace with:

```tsx
        <p
          className={`text-xs text-center truncate ${
            robot!.is_clean ? "text-green-400" : "text-amber-400"
          }`}
        >
          {status}
        </p>
```

(Adds `truncate` so "Needs configuration" can't wrap and inflate the tile height.)

- [ ] **Step 4: Verify TypeScript compiles and visually check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/landing/RobotTile.tsx
git commit -m "style(landing): tighten RobotTile padding for dock layout"
```

---

## Task 5: Tighten `ActionList` for the dock

Drop the `pt-6` spacer (no preceding section header in the new layout) and shrink action item padding. Description and arrow stay.

**Files:**
- Modify: `frontend/src/components/landing/ActionList.tsx`

- [ ] **Step 1: Remove `pt-6` and tighten the action item padding**

Open `frontend/src/components/landing/ActionList.tsx`. Find:

```tsx
      <div className="pt-6">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {actions.map((action, index) => (
            <div
              key={index}
              className="flex items-center justify-between p-4 bg-gray-800 rounded-lg border border-gray-700"
            >
```

Replace with:

```tsx
      <div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {actions.map((action, index) => (
            <div
              key={index}
              className="flex items-center justify-between p-3 bg-gray-800 rounded-lg border border-gray-700"
            >
```

(Drops `pt-6`, tightens `gap-4` → `gap-3`, `p-4` → `p-3`.)

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/landing/ActionList.tsx
git commit -m "style(landing): tighten ActionList for dock layout"
```

---

## Task 6: Reorganise `Landing.tsx` (the main work)

This rewires the page: drops `LandingHeader` + `HfAuthBanner`, mounts `LandingTopBar`, and lays out the sticky dock + jobs body. All the existing recording-modal state and handlers stay exactly as they were.

**Files:**
- Modify: `frontend/src/pages/Landing.tsx`

- [ ] **Step 1: Update imports**

Open `frontend/src/pages/Landing.tsx`. Find:

```tsx
import LandingHeader from "@/components/landing/LandingHeader";
import HfAuthBanner from "@/components/landing/HfAuthBanner";
import RobotConfigManager from "@/components/landing/RobotConfigManager";
import ActionList from "@/components/landing/ActionList";
import RecordingModal from "@/components/landing/RecordingModal";
import JobsSection from "@/components/jobs/JobsSection";
```

Replace with:

```tsx
import LandingTopBar from "@/components/landing/LandingTopBar";
import RobotConfigManager from "@/components/landing/RobotConfigManager";
import ActionList from "@/components/landing/ActionList";
import RecordingModal from "@/components/landing/RecordingModal";
import JobsSection from "@/components/jobs/JobsSection";
```

(Removes `LandingHeader` and `HfAuthBanner` imports; adds `LandingTopBar`.)

- [ ] **Step 2: Replace the JSX `return` block**

Find the entire `return (...)` block, which currently is:

```tsx
  return (
    <div className="min-h-screen bg-black text-white flex flex-col items-center p-4 pt-12 sm:pt-20">
      <div className="w-full max-w-7xl mx-auto px-4 mb-12">
        <HfAuthBanner />
        <LandingHeader />
      </div>

      <div className="p-8 bg-gray-900 rounded-lg shadow-xl w-full max-w-4xl space-y-6 border border-gray-700">
        <RobotConfigManager
          selectedName={selectedName}
          selectedRecord={selectedRecord}
          availableNames={availableNames}
          isLoading={isLoadingRobots}
          selectRobot={selectRobot}
          createRobot={createRobot}
          deleteRobot={deleteRobot}
        />
        <JobsSection />
        <ActionList actions={actions} />
      </div>

      <UsageInstructionsModal
        open={showUsageModal}
        onOpenChange={setShowUsageModal}
        dismissible={!ON_SPACE}
      />

      <RecordingModal
        open={showRecordingModal}
        onOpenChange={handleRecordingModalClose}
        robot={selectedRecord}
        datasetName={datasetName}
        setDatasetName={setDatasetName}
        singleTask={singleTask}
        setSingleTask={setSingleTask}
        numEpisodes={numEpisodes}
        setNumEpisodes={setNumEpisodes}
        episodeTimeS={episodeTimeS}
        setEpisodeTimeS={setEpisodeTimeS}
        resetTimeS={resetTimeS}
        setResetTimeS={setResetTimeS}
        cameras={cameras}
        setCameras={setCameras}
        onStart={handleStartRecording}
        releaseStreamsRef={releaseStreamsRef}
      />
    </div>
  );
```

Replace with:

```tsx
  return (
    <div
      className="min-h-screen bg-black text-white"
      style={{ ["--lelab-topbar-h" as string]: "48px" }}
    >
      <LandingTopBar />

      <div
        className="sticky z-20 bg-black/95 backdrop-blur supports-[backdrop-filter]:bg-black/70 border-b border-gray-800"
        style={{ top: "var(--lelab-topbar-h)" }}
      >
        <div className="mx-auto max-w-7xl px-4 py-4 grid gap-4 grid-cols-1 lg:grid-cols-[1.2fr_2fr]">
          <RobotConfigManager
            selectedName={selectedName}
            selectedRecord={selectedRecord}
            availableNames={availableNames}
            isLoading={isLoadingRobots}
            selectRobot={selectRobot}
            createRobot={createRobot}
            deleteRobot={deleteRobot}
          />
          <ActionList actions={actions} />
        </div>
      </div>

      <main className="mx-auto max-w-7xl px-4 py-6">
        <JobsSection />
      </main>

      <UsageInstructionsModal
        open={showUsageModal}
        onOpenChange={setShowUsageModal}
        dismissible={!ON_SPACE}
      />

      <RecordingModal
        open={showRecordingModal}
        onOpenChange={handleRecordingModalClose}
        robot={selectedRecord}
        datasetName={datasetName}
        setDatasetName={setDatasetName}
        singleTask={singleTask}
        setSingleTask={setSingleTask}
        numEpisodes={numEpisodes}
        setNumEpisodes={setNumEpisodes}
        episodeTimeS={episodeTimeS}
        setEpisodeTimeS={setEpisodeTimeS}
        resetTimeS={resetTimeS}
        setResetTimeS={setResetTimeS}
        cameras={cameras}
        setCameras={setCameras}
        onStart={handleStartRecording}
        releaseStreamsRef={releaseStreamsRef}
      />
    </div>
  );
```

Notes on the JSX:

- The outer `div` sets `--lelab-topbar-h` so the dock's `top` stays in sync if the bar's height changes later. The `["--lelab-topbar-h" as string]` cast is needed because React's CSS prop typing rejects unknown custom properties.
- The dock is a plain `<div>` with `sticky top-[var(--lelab-topbar-h)]`. No new component file. Its `bg-black/95 backdrop-blur` matches the top bar so jobs scrolling underneath stay hidden.
- `JobsSection` lives inside `<main>` for landmark semantics; this is the natural document scroll target.
- The dock uses `lg:grid-cols-[1.2fr_2fr]`. Below `lg` the grid collapses to one column (robot tile on top, then `ActionList` which already does its own `md:grid-cols-2` internally).

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Landing.tsx
git commit -m "feat(landing): sticky top bar + dock + scrolling jobs body"
```

---

## Task 7: Delete `LandingHeader` and `HfAuthBanner`

Both are now unreferenced. Confirmed earlier by grep — the only imports were the two we just removed.

**Files:**
- Delete: `frontend/src/components/landing/LandingHeader.tsx`
- Delete: `frontend/src/components/landing/HfAuthBanner.tsx`

- [ ] **Step 1: Re-confirm both files have no remaining references**

Run:

```bash
grep -rn "LandingHeader\|HfAuthBanner" frontend/src/
```

Expected output: only the two files themselves (the `const X: React.FC` and `export default X` lines). No imports anywhere else. If any importer turns up, stop and resolve it before deleting.

- [ ] **Step 2: Delete the files**

```bash
git rm frontend/src/components/landing/LandingHeader.tsx
git rm frontend/src/components/landing/HfAuthBanner.tsx
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git commit -m "refactor(landing): drop LandingHeader hero and HfAuthBanner"
```

---

## Task 8: Manual verification

This task has no code. Run through the spec's acceptance criteria. Any failure → file follow-up tasks before declaring done.

- [ ] **Step 1: Start the dev stack**

```bash
lelab --dev
```

Open `http://localhost:8080`.

- [ ] **Step 2: Walk the acceptance criteria**

For each criterion in `docs/superpowers/specs/2026-05-04-landing-dashboard-layout-design.md` § "Acceptance criteria", confirm:

1. At ≥1280px width, top bar + dock (robot + 2×2 actions) + jobs grid are all visible without horizontal scroll.
2. Scroll the page: top bar and dock stay pinned to the viewport top.
3. The four action buttons stay visible regardless of how many jobs exist.
4. With ≥6 jobs, at least one full row of 3 cards is visible above the fold (use `lelab` to launch a few training jobs, or temporarily seed `JobsSection` with mock data if the backend is empty).
5. With HF authenticated: chip shows green dot + username; no banner anywhere.
6. With HF unauthenticated: chip shows amber dot + "HF not configured"; clicking opens the dialog with the login command (copyable) and "I've logged in — recheck" button. Page itself contains no banner.
7. The hero (big logo + "LeLab" title + tagline) is gone.
8. Click each action button: Record Dataset opens the recording modal; Replay/Training/Inference navigate to their pages. Robot tile's Configure/Delete/Teleoperation buttons still work.

To exercise the unauthenticated state without real CLI changes: in `frontend/src/contexts/HfAuthContext.tsx`, temporarily force `setAuth({ status: "unauthenticated", loginCommand: "hf auth login" })` near the top of `fetchStatus`. Revert before committing anything else.

- [ ] **Step 3: Note any issues**

If any criterion fails, append a follow-up task to this plan with the exact symptom and the file to edit. Do not silently fix and merge.

- [ ] **Step 4: No commit**

This task produces no code changes (assuming verification passes).

---

## Self-Review

Spec coverage:

- D1 layout: Task 6 ✓
- D2 robot tile V1 compaction: Task 4 ✓
- D3 HF chip + dialog: Tasks 1, 2 ✓
- D4 brand-mark in top bar (hero removed): Tasks 3, 7 ✓
- D5 max-w-7xl: Task 6 ✓
- D6 stickiness with shared CSS variable: Tasks 3, 6 ✓
- File deletions: Task 7 ✓
- Acceptance criteria walk: Task 8 ✓

Placeholder scan: no TBDs, no "implement later", every code-changing step shows the code or the exact diff. The unauthenticated-state mock in Task 8 step 2 includes the exact line to add.

Type / signature consistency: `HfAuthChip` (no props) is referenced from `LandingTopBar` matching its definition. `HfAuthDialog` props `{ open, onOpenChange }` match `HfAuthChip`'s usage. `auth.status` discriminator values used (`"loading" | "authenticated" | "unauthenticated"`) match `HfAuthState` in `HfAuthContext.tsx`. The `--lelab-topbar-h` value (`48px`) matches `LandingTopBar`'s `h-12`.
