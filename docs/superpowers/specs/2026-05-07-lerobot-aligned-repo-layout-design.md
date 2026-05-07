# LeRobot-Aligned Repo Layout

**Status:** Design — pending user review

**Context:** The LeRobot team will take over maintenance of leLab for the LeRobot community. The repo currently uses its own naming and folder conventions (package `app/`, gerund-form filenames like `recording.py`, entry script under top-level `scripts/`). To make the codebase legible and uniform for LeRobot maintainers, restructure to mirror LeRobot's package layout, file naming, and tooling.

**Hard constraint:** **Do not change behavior.** Endpoints, websocket protocol, on-disk paths, CLI flags, and runtime semantics must be identical after the rename. Only filenames, package paths, and tooling change.

## Goals

1. Match LeRobot's package layout and file-naming style so a LeRobot maintainer can navigate leLab without learning a second convention.
2. Adopt LeRobot's tooling baseline (ruff, pre-commit, license headers, `__version__.py`, smoke test scaffold).
3. Keep the diff mechanical and reviewable — no incidental refactors.

## Non-Goals

- No behavior changes, no API renames, no endpoint URL changes.
- No content rewrites of existing modules beyond what the rename mechanically requires.
- No new tests beyond a single smoke test that imports the package.
- No CI changes beyond what the rename forces (e.g., the frontend-build workflow does not need updating; see "Affected files outside the package" below).

## Source of truth

LeRobot 0.5.2 (currently installed in `.venv`) is the reference. Patterns adopted from it:

- Top-level package layout: `__init__.py`, `__version__.py`, `types.py`, then concern-named subpackages/modules.
- Apache-2.0 license header on every `.py` file.
- CLI entry points live inside the package, under `<pkg>/scripts/`, named `<pkg>_<verb>.py`, and exposed via `[project.scripts]` as `<pkg>-<verb>`.
- Verb-form filenames for action concerns: `record.py`, `teleoperate.py`, `calibrate.py`, `replay.py`, `rollout.py`, `train.py`.
- Utilities grouped under `<pkg>/utils/`.
- `__version__.py` resolves the version via `importlib.metadata.version("<pkg>")`.

## Target layout

```
lelab/
├── __init__.py            # docstring + re-exports __version__
├── __version__.py         # importlib.metadata.version("lelab")
├── types.py               # shared TypedDicts/Enums (DeviceType, RobotType)
├── server.py              # FastAPI app + ConnectionManager (was app/main.py)
├── record.py              # was app/recording.py
├── teleoperate.py         # was app/teleoperating.py
├── calibrate.py           # was app/calibrating.py
├── replay.py              # was app/replaying.py  (note: not present in current tree, see Open Items)
├── rollout.py             # was app/inferring.py
├── train.py               # was app/training.py
├── jobs.py                # unchanged
├── datasets.py            # was app/dataset_browser.py
├── runners/
│   ├── __init__.py
│   └── hf_cloud.py        # path unchanged relative to package root
├── utils/
│   ├── __init__.py
│   ├── config.py          # was app/config.py
│   ├── hf_auth.py         # was app/hf_auth.py
│   └── system.py          # was app/system.py
├── scripts/
│   ├── __init__.py
│   └── lelab.py           # was scripts/backend.py — exposes main()
└── static/                # was app/static/ — contents unchanged
```

Top-level `scripts/` directory at the repo root is **deleted** after its sole file moves into the package.

## File mapping (full)

| Current path | New path | Notes |
|---|---|---|
| `app/__init__.py` | `lelab/__init__.py` | Replace stub with LeRobot-style docstring; re-export `__version__` from `__version__.py`. |
| `app/main.py` | `lelab/server.py` | FastAPI app + ConnectionManager. Module renamed; the FastAPI variable inside stays `app = FastAPI(...)`. |
| `app/recording.py` | `lelab/record.py` | |
| `app/teleoperating.py` | `lelab/teleoperate.py` | |
| `app/calibrating.py` | `lelab/calibrate.py` | |
| `app/replaying.py` | `lelab/replay.py` | If the file truly does not exist in the current tree, this row is dropped (see Open Items). |
| `app/inferring.py` | `lelab/rollout.py` | LeRobot calls this concept "rollout"; CLAUDE.md already notes the wrapper shells out to `lerobot-rollout`. |
| `app/training.py` | `lelab/train.py` | |
| `app/jobs.py` | `lelab/jobs.py` | |
| `app/dataset_browser.py` | `lelab/datasets.py` | Mirrors LeRobot's `lerobot/datasets/`. |
| `app/config.py` | `lelab/utils/config.py` | |
| `app/hf_auth.py` | `lelab/utils/hf_auth.py` | |
| `app/system.py` | `lelab/utils/system.py` | |
| `app/runners/__init__.py` | `lelab/runners/__init__.py` | |
| `app/runners/hf_cloud.py` | `lelab/runners/hf_cloud.py` | |
| `app/static/**` | `lelab/static/**` | Verbatim copy. |
| `scripts/backend.py` | `lelab/scripts/lelab.py` | Update internal uvicorn module string from `"app.main:app"` to `"lelab.server:app"`. |
| (new) | `lelab/__version__.py` | LeRobot-style importlib.metadata version resolver. |
| (new) | `lelab/types.py` | Empty stub with module docstring; populated when shared types emerge. |
| (new) | `lelab/scripts/__init__.py` | Empty. |

## Import-path updates

All intra-package imports in [app/main.py](app/main.py) are relative (`from .recording import ...`), so they survive the rename automatically. The grep confirmed there are no `from app.X` or `import app.X` absolute imports anywhere in the codebase.

The only absolute string reference to the package layout is the uvicorn module path:

- `scripts/backend.py:75` — `uvicorn.run("app.main:app", ...)` → `uvicorn.run("lelab.server:app", ...)`
- `scripts/backend.py:113` — `["uvicorn", "app.main:app", ...]` → `["uvicorn", "lelab.server:app", ...]`

## Affected files outside the package

| File | Change |
|---|---|
| `pyproject.toml` | `[project.scripts] lelab = "lelab.scripts.lelab:main"`. `[tool.setuptools.packages.find] include = ["lelab*", "frontend"]`. |
| `CLAUDE.md` | Update file paths in the "Backend module layout" and "WebSocket broadcast" sections to point at `lelab/...`. |
| `.github/workflows/build_frontend.yml` | No change — operates on `frontend/`, not the Python package. |
| `.github/workflows/sync_space.yml` | No change — operates on `frontend/`, not the Python package. |
| `frontend/Dockerfile` | No change — Space build is frontend-only. |
| `README.md`, `HTTPS_SETUP.md`, `PHONE_CAMERA_SETUP.md` | Search for `app/` references; update any that point at moved files. |
| `LeLab.egg-info/`, `so_101_test.egg-info/` | Build artifacts; do not modify, they regenerate on next install. |

## Tooling baseline

Add to the repo root, mirroring LeRobot's setup:

1. **Ruff config** in `pyproject.toml`:
   ```toml
   [tool.ruff]
   line-length = 110
   target-version = "py312"

   [tool.ruff.lint]
   select = ["E", "F", "I", "B", "UP"]
   ```
   (Match LeRobot's ruff config exactly if it differs from this skeleton — confirm during implementation by reading `huggingface/lerobot` `pyproject.toml` at the pinned SHA.)

2. **`.pre-commit-config.yaml`** with ruff format + ruff lint hooks. Copy the structure from LeRobot's repo at the pinned SHA.

3. **Apache-2.0 license header** on every `.py` file. Same 14-line block LeRobot uses (verbatim). Applied as part of the rename so that no separate sweep is needed afterward.

4. **`tests/` scaffold:**
   ```
   tests/
   ├── __init__.py        # empty
   └── test_smoke.py      # imports lelab and asserts __version__ is a string
   ```
   No pytest config beyond what `pip install pytest && pytest tests/` needs by default. Tests are not added as a runtime dependency; document under a `[project.optional-dependencies] dev = ["pytest", "ruff", "pre-commit"]` extra.

## Migration sequence

The plan skill will produce the step-by-step. Sketch:

1. Create `lelab/` skeleton (empty `__init__.py`, `__version__.py`, `types.py`, `utils/__init__.py`, `runners/__init__.py`, `scripts/__init__.py`).
2. Move + rename files via `git mv` (preserves history) per the table above.
3. Update the two uvicorn strings in `lelab/scripts/lelab.py`.
4. Update `pyproject.toml` (script entry, packages.find, optional-dependencies, ruff config).
5. Drop the now-empty top-level `scripts/` directory.
6. Add license headers to every `.py` file.
7. Add `.pre-commit-config.yaml`, `tests/test_smoke.py`.
8. Update `CLAUDE.md` and any user-facing docs that reference `app/`.
9. Reinstall (`pip install -e .`) and verify `lelab` and `lelab --dev` still start the server. Hit one or two endpoints to confirm the FastAPI app is mounted under the new module path.

## Verification

End-to-end sanity, no test suite assumed:

1. `pip install -e .` succeeds.
2. `python -c "import lelab; print(lelab.__version__)"` prints a non-empty version string.
3. `python -c "from lelab.server import app; print(len(app.routes))"` prints a positive integer.
4. `lelab` boots, browser opens, `GET /health` returns 200.
5. `lelab --dev` boots both Vite (8080) and uvicorn (8000), browser opens.
6. `pytest tests/` runs the smoke test and passes.
7. `ruff check lelab/ tests/` is clean (or its findings are pre-existing and out of scope).

## Open items

- **`replaying.py` presence.** CLAUDE.md describes a `replaying.py` module but the current `app/` listing doesn't show it. Plan execution must check `git log -- app/replaying.py` and either include the rename or remove the row from the mapping. No design impact either way.
- **Exact ruff/pre-commit config.** The skeleton above is a reasonable default. During implementation, fetch the pinned LeRobot SHA's `pyproject.toml` and `.pre-commit-config.yaml` and copy verbatim where leLab has no specific reason to differ.
- **License header text.** Use the exact 14-line Apache-2.0 header LeRobot uses (visible in any `.venv/lib/python3.13/site-packages/lerobot/scripts/lerobot_*.py` file). Do not paraphrase.

## Out of scope

- Renaming or restructuring the `frontend/` directory.
- Touching the LeRobot pin or any dependency version.
- Splitting `lelab/server.py` (currently ~984 lines). Tempting but a separate project — flagged as a follow-up, not bundled here.
- Test coverage beyond the smoke test.
