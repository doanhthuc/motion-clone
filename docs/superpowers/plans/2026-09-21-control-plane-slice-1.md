# Control-plane API, slice 1 (read-only runs + outputs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An authenticated HTTP API, running as a thread inside `motion-bot` on the VPS and reachable
through Cloudflare Tunnel, that lets a phone list runs, read a run's progress, and stream finished
outputs.

**Architecture:** A new Telegram-free package `scripts/control/` holds the read logic (runs from the
on-disk journals, outputs from `out/*/_final`). A new package `scripts/httpapi/` is a thin stdlib
`ThreadingHTTPServer` over it: bearer auth, JSON + `ETag`, and `Range` file streaming. `bot.py`'s
`main()` starts it in a daemon thread when `CONTROL_API_TOKEN` is set. Nothing in this slice writes
state, so `control.LOCK` (spec §4.3) is deliberately not introduced yet — it arrives with slice 3,
the first slice that mutates.

**Tech Stack:** Python 3 stdlib only (`http.server`, `threading`, `hmac`, `hashlib`, `json`),
`unittest`, `cloudflared` (VPS, manual setup).

**Spec:** `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` (slice 1 = §6 row 1;
contract in §5.3 `GET /v1/runs[/{id}]`, §5.4 outputs, §5.6 status codes, §4.4 ingress, §7, §8).

## Global Constraints

- No new Python dependency (spec §4.2: stdlib `ThreadingHTTPServer` on a 1 GB box).
- Bind `127.0.0.1` only; port from `CONTROL_API_PORT`, default `8787` (spec §8).
- Every route is under `/v1`; errors are `{"error": {"code": "...", "message": "..."}}` (spec §5).
- `401` missing/wrong bearer; `404` unknown id or path outside its root; `500` logged, never
  propagated into the bot's poll loop (spec §5.6).
- Bearer compare is constant-time (`hmac.compare_digest`) (spec §4.4).
- A run's `id` is the manifest stem, `batch/<id>.yaml` (spec §5.3).
- Every file-naming path parameter goes through `safe_child` (spec §5.4).
- The API must never expose absolute paths from the journal (`state.json` stores `file` as an
  absolute path on the machine that ran the batch).
- New test files are `scripts/tests/test_batch_control_*.py` so `make batch-test` runs them (spec §7).
- English for code, comments, docs, commit messages (CLAUDE.md). No `#region ALD` markers.
- `motions-studio/setup/scrub-secrets.sh --check` exits 0 before every commit (repo is public).
- Commits end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- Work on branch `spec/vps-control-plane-api` (already exists; spec committed there).

## File Structure

| File | Responsibility |
|---|---|
| `scripts/control/__init__.py` | Package marker; one-line docstring |
| `scripts/control/paths.py` | `safe_child` — moved verbatim from `bot._safe_child` |
| `scripts/control/runs.py` | `list_runs`, `run_detail` — journal + lease + in-process run handles → plain dicts |
| `scripts/control/outputs.py` | `list_outputs`, `resolve_output` — `out/*/_final` |
| `scripts/httpapi/__init__.py` | Package marker |
| `scripts/httpapi/server.py` | `make_server`, `start_in_thread`; handler: auth, routing, JSON/ETag, errors |
| `scripts/httpapi/files.py` | `parse_range`, `send_file` — single-range `206`/`416` streaming |
| `scripts/tgbot/bot.py` | `_safe_child` becomes an import alias; `main()` starts the API thread |
| `scripts/tests/test_batch_control_paths.py` | `safe_child` |
| `scripts/tests/test_batch_control_runs.py` | `list_runs`, `run_detail` |
| `scripts/tests/test_batch_control_outputs.py` | `list_outputs`, `resolve_output` |
| `scripts/tests/test_batch_control_http.py` | Real server on an ephemeral port: auth, routes, ETag, Range, 404/500 |
| `scripts/vps/api-smoke.sh` | Curl through the tunnel: 403 / 401 / 200 |
| `scripts/vps/README.md` | `cloudflared` + Access setup, RSS measurement |
| `.env.example` | `CONTROL_API_TOKEN=`, `CONTROL_API_PORT=8787`, smoke-script keys |
| `Makefile` | `api-smoke` target |

---

### Task 1: Move `safe_child` into `scripts/control/paths.py`

The API needs the same path guard the bot uses, and `control/` must not import `tgbot/bot.py`
(6.7k lines, Telegram). Move it; the bot keeps its private name as an alias so no call site changes.

**Files:**
- Create: `scripts/control/__init__.py`, `scripts/control/paths.py`
- Modify: `scripts/tgbot/bot.py` (the `def _safe_child` block, ~line 369)
- Test: `scripts/tests/test_batch_control_paths.py`

**Interfaces:**
- Produces: `control.paths.safe_child(root: Path, name: str) -> Path | None`

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_control_paths.py
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.paths import safe_child


class TestSafeChild(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "ok.mp4").write_bytes(b"x")

    def test_plain_name_resolves_under_root(self):
        self.assertEqual(safe_child(self.root, "ok.mp4"), (self.root / "ok.mp4").resolve())

    def test_refuses_traversal_absolute_and_separators(self):
        for bad in ("..", "../x", "a/b", "a\\b", "/etc/passwd", "", "   "):
            self.assertIsNone(safe_child(self.root, bad), bad)

    def test_refuses_a_symlink_that_escapes_the_root(self):
        outside = Path(tempfile.mkdtemp()) / "secret"
        outside.write_text("s")
        os.symlink(outside, self.root / "link")
        self.assertIsNone(safe_child(self.root, "link"))

    def test_bot_alias_is_the_same_function(self):
        import tgbot.bot as bot
        self.assertIs(bot._safe_child, safe_child)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_paths.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'control'`

- [ ] **Step 3: Implement**

`scripts/control/__init__.py`:

```python
"""Telegram-free core shared by the Telegram bot and the HTTP API (spec 2026-09-21)."""
```

`scripts/control/paths.py` — cut the whole `_safe_child` function (signature, docstring and body)
out of `scripts/tgbot/bot.py` and paste it here renamed `safe_child`, unchanged otherwise:

```python
"""Path guards shared by every adapter that turns a user-supplied name into a file."""
from __future__ import annotations

from pathlib import Path


def safe_child(root: Path, name: str) -> Path | None:
    """<the existing _safe_child docstring, verbatim>"""
    name = name.strip()
    if not name or Path(name).is_absolute() or ".." in name or "/" in name or "\\" in name:
        return None
    root_resolved = root.resolve()
    candidate = (root_resolved / name).resolve()
    if not candidate.is_relative_to(root_resolved):
        return None
    return candidate
```

In `scripts/tgbot/bot.py`, where `def _safe_child` was, leave only (next to the other imports near
the top of the file, after `from tgbot.tgclient import Tg, TgError`):

```python
from control.paths import safe_child as _safe_child
```

- [ ] **Step 4: Run the new test and the bot's suite**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_paths.py' -v`
Expected: 4 tests PASS
Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py'`
Expected: OK (same count as before the change)

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/__init__.py scripts/control/paths.py scripts/tgbot/bot.py scripts/tests/test_batch_control_paths.py
git commit -m "refactor(control): move _safe_child into a Telegram-free control package

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `control/outputs.py` — finished outputs

**Files:**
- Create: `scripts/control/outputs.py`
- Test: `scripts/tests/test_batch_control_outputs.py`

**Interfaces:**
- Consumes: `control.paths.safe_child`
- Produces:
  - `OUTPUT_SUFFIXES = frozenset({".mp4", ".mov", ".png", ".jpg", ".jpeg", ".webp"})`
  - `final_names(out_dir: Path, batch: str) -> list[str]` — sorted names in `out/<batch>/_final`
  - `list_outputs(out_dir: Path) -> list[dict]` — `[{"batch": str, "updated_at": float,
    "files": [{"name": str, "bytes": int}]}]`, newest batch first, batches without files omitted
  - `resolve_output(out_dir: Path, batch: str, name: str) -> Path | None`

`_final/` is the only place the runner promotes a finished result into (`runner.py` `_finalize`);
`runs/` holds per-stage intermediates, which must never be shown as a result (see
`tgbot.run.final_files`). Images are included because try-on-only results are PNGs.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_batch_control_outputs.py
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from control.outputs import final_names, list_outputs, resolve_output


class TestOutputs(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        for batch, mtime in (("b-old", 1000), ("b-new", 2000)):
            final = self.out / batch / "_final"
            final.mkdir(parents=True)
            (final / "x.mp4").write_bytes(b"12345")
            (final / "y.png").write_bytes(b"1")
            (final / "_index.tsv").write_text("t")
            os.utime(final, (mtime, mtime))
        (self.out / "b-empty" / "runs").mkdir(parents=True)
        (self.out / "b-new" / "runs" / "r").mkdir(parents=True)
        (self.out / "b-new" / "runs" / "r" / "01-motion.mp4").write_bytes(b"i")

    def test_final_names_filters_by_suffix(self):
        self.assertEqual(final_names(self.out, "b-new"), ["x.mp4", "y.png"])

    def test_final_names_of_a_hostile_batch_is_empty(self):
        self.assertEqual(final_names(self.out, "../etc"), [])

    def test_list_is_newest_first_and_skips_batches_without_final(self):
        listed = list_outputs(self.out)
        self.assertEqual([b["batch"] for b in listed], ["b-new", "b-old"])
        self.assertEqual(listed[0]["files"], [{"name": "x.mp4", "bytes": 5},
                                              {"name": "y.png", "bytes": 1}])

    def test_resolve_only_inside_final(self):
        self.assertEqual(resolve_output(self.out, "b-new", "x.mp4"),
                         (self.out / "b-new" / "_final" / "x.mp4").resolve())
        self.assertIsNone(resolve_output(self.out, "b-new", "01-motion.mp4"))
        self.assertIsNone(resolve_output(self.out, "b-new", "_index.tsv"))
        self.assertIsNone(resolve_output(self.out, "..", "x.mp4"))
        self.assertIsNone(resolve_output(self.out, "b-new", "../runs/r/01-motion.mp4"))
        self.assertIsNone(resolve_output(self.out, "b-new", "missing.mp4"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_outputs.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'control.outputs'`

- [ ] **Step 3: Implement**

```python
# scripts/control/outputs.py
"""Finished outputs: `out/<batch>/_final/*` only.

`runs/<run>/NN-stage.*` are per-stage intermediates; the runner promotes only
a run's LAST stage into `_final/` (runner.py `_finalize`). Serving an
intermediate as if it were the result is worse than serving nothing — the
user cannot tell them apart (same reasoning as tgbot.run.final_files).
"""
from __future__ import annotations

from pathlib import Path

from control.paths import safe_child

OUTPUT_SUFFIXES = frozenset({".mp4", ".mov", ".png", ".jpg", ".jpeg", ".webp"})


def _final_dir(out_dir: Path, batch: str) -> Path | None:
    batch_dir = safe_child(out_dir, batch)
    if batch_dir is None:
        return None
    final = batch_dir / "_final"
    return final if final.is_dir() else None


def _final_files(final: Path) -> list[Path]:
    return sorted(p for p in final.iterdir()
                  if p.is_file() and p.suffix.lower() in OUTPUT_SUFFIXES)


def final_names(out_dir: Path, batch: str) -> list[str]:
    final = _final_dir(out_dir, batch)
    return [p.name for p in _final_files(final)] if final else []


def list_outputs(out_dir: Path) -> list[dict]:
    listed = []
    for batch_dir in out_dir.iterdir() if out_dir.is_dir() else []:
        final = batch_dir / "_final"
        if not batch_dir.is_dir() or not final.is_dir():
            continue
        files = _final_files(final)
        if files:
            listed.append({"batch": batch_dir.name,
                           "updated_at": final.stat().st_mtime,
                           "files": [{"name": p.name, "bytes": p.stat().st_size} for p in files]})
    return sorted(listed, key=lambda b: b["updated_at"], reverse=True)


def resolve_output(out_dir: Path, batch: str, name: str) -> Path | None:
    final = _final_dir(out_dir, batch)
    if final is None:
        return None
    path = safe_child(final, name)
    if path is None or not path.is_file() or path.suffix.lower() not in OUTPUT_SUFFIXES:
        return None
    return path
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py' -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/outputs.py scripts/tests/test_batch_control_outputs.py
git commit -m "feat(control): list and resolve finished outputs under out/*/_final

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: `control/runs.py` — runs as plain dicts

**Files:**
- Create: `scripts/control/runs.py`
- Test: `scripts/tests/test_batch_control_runs.py`

**Interfaces:**
- Consumes: `control.paths.safe_child`; `control.outputs.final_names` (Task 2); `batchlib.manifest.load_state`, `state_path_for`;
  `tgbot.run.lease_for`, `drain_running`, `phase_a_running` — called through the module
  (`run_mod.drain_running(...)`) so tests can patch them. `tgbot/run.py` is already Telegram-free;
  slice 4 moves it into `control/`.
- Produces:
  - `list_runs(batch_dir: Path, out_dir: Path) -> list[dict]` — newest first by journal mtime;
    each item is `run_summary`'s shape.
  - `run_detail(batch_dir: Path, out_dir: Path, run_id: str) -> dict | None` — `None` if unknown.
  - Shapes:

    ```
    summary = {"id": str, "batch": str | None, "status": Status, "updated_at": float,
               "jobs_total": int, "jobs_done": int}
    detail  = summary + {"jobs": [{"id": str, "status": str,
                                   "stages": [{"name": str, "status": str,
                                               "elapsed_sec": int | None}]}],
                         "lease": {"provider": str, "elapsed_sec": int,
                                   "abs_max_min": int} | None,
                         "outputs": [str]}          # file names in out/<batch>/_final
    Status  = "running" | "phase_a" | "error" | "done" | "stopped"
    ```

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_batch_control_runs.py
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease
import control.runs as runs
import tgbot.run as run_mod

STATE = {"version": 1, "batch": "2026-09-20-1000", "runs": {
    "a": {"status": "done", "stages": {
        "motion": {"status": "done", "elapsed_sec": 247,
                   "file": "/opt/motion-clone/out/2026-09-20-1000/runs/a/01-motion.mp4",
                   "params_sent": {"cfg": 1}},
        "enhance": {"status": "done", "elapsed_sec": 90}}},
    "b": {"status": "error", "stages": {"motion": {"status": "error"}}}}}


def write_run(batch_dir: Path, run_id: str, state: dict, mtime: float | None = None) -> Path:
    manifest = batch_dir / f"{run_id}.yaml"
    manifest.write_text("runs: []\n", encoding="utf-8")
    state_file = batch_dir / f"{run_id}.state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    if mtime is not None:
        os.utime(state_file, (mtime, mtime))
    return manifest


class RunsTestBase(unittest.TestCase):
    def setUp(self):
        self.batch = Path(tempfile.mkdtemp())
        self.out = Path(tempfile.mkdtemp())
        patches = [mock.patch.object(run_mod, "drain_running", return_value=False),
                   mock.patch.object(run_mod, "phase_a_running", return_value=False),
                   mock.patch.object(run_mod, "lease_for", return_value=None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestListRuns(RunsTestBase):
    def test_newest_first_and_only_journals_with_a_manifest(self):
        write_run(self.batch, "old", STATE, mtime=1000)
        write_run(self.batch, "new", STATE, mtime=2000)
        (self.batch / "orphan.state.json").write_text(json.dumps(STATE))   # no .yaml
        (self.batch / "pod-lease.json").write_text("{}")
        self.assertEqual([r["id"] for r in runs.list_runs(self.batch, self.out)], ["new", "old"])

    def test_counts(self):
        write_run(self.batch, "r", STATE)
        [summary] = runs.list_runs(self.batch, self.out)
        self.assertEqual((summary["jobs_total"], summary["jobs_done"]), (2, 1))
        self.assertEqual(summary["batch"], "2026-09-20-1000")


class TestStatus(RunsTestBase):
    def test_error_when_any_job_errored_and_nothing_runs(self):
        write_run(self.batch, "r", STATE)
        self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "error")

    def test_done_when_every_job_is_done(self):
        state = {"batch": "x", "runs": {"a": STATE["runs"]["a"]}}
        write_run(self.batch, "r", state)
        self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "done")

    def test_stopped_when_nothing_recorded(self):
        write_run(self.batch, "r", {"batch": "x", "runs": {}})
        self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "stopped")

    def test_a_live_drain_wins_over_the_journal(self):
        write_run(self.batch, "r", STATE)
        with mock.patch.object(run_mod, "drain_running", return_value=True):
            self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "running")

    def test_phase_a(self):
        write_run(self.batch, "r", STATE)
        with mock.patch.object(run_mod, "phase_a_running", return_value=True):
            self.assertEqual(runs.run_detail(self.batch, self.out, "r")["status"], "phase_a")


class TestDetail(RunsTestBase):
    def test_unknown_or_hostile_id_is_none(self):
        for bad in ("nope", "../etc", "a/b", ""):
            self.assertIsNone(runs.run_detail(self.batch, self.out, bad), bad)

    def test_never_leaks_absolute_paths_or_sent_params(self):
        write_run(self.batch, "r", STATE)
        body = json.dumps(runs.run_detail(self.batch, self.out, "r"))
        self.assertNotIn("/opt/motion-clone", body)
        self.assertNotIn("params_sent", body)

    def test_stages_keep_journal_order(self):
        write_run(self.batch, "r", STATE)
        job = runs.run_detail(self.batch, self.out, "r")["jobs"][0]
        self.assertEqual([s["name"] for s in job["stages"]], ["motion", "enhance"])
        self.assertEqual(job["stages"][0]["elapsed_sec"], 247)

    def test_lease_is_summarised(self):
        write_run(self.batch, "r", STATE)
        lease = Lease(pod_id="p1", provisioned_at=time.time() - 65,
                      manifest=str(self.batch / "r.yaml"), abs_max_min=120, provider="vast")
        with mock.patch.object(run_mod, "lease_for", return_value=lease):
            got = runs.run_detail(self.batch, self.out, "r")["lease"]
        self.assertEqual(got["provider"], "vast")
        self.assertEqual(got["abs_max_min"], 120)
        self.assertGreaterEqual(got["elapsed_sec"], 65)

    def test_outputs_list_final_files_only(self):
        write_run(self.batch, "r", STATE)
        final = self.out / "2026-09-20-1000" / "_final"
        final.mkdir(parents=True)
        (final / "a.mp4").write_bytes(b"v")
        (final / "notes.txt").write_text("x")
        self.assertEqual(runs.run_detail(self.batch, self.out, "r")["outputs"], ["a.mp4"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_runs.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'control.runs'`

- [ ] **Step 3: Implement**

```python
# scripts/control/runs.py
"""Runs, read from the on-disk journals — never from the pod.

The journal (`batch/<id>.state.json`) is the same source `progress_text` and
`batch_status` read, so a run stays reportable after `make gpu-destroy`: the
pod is the thing most likely to be gone by the time the phone asks.

Liveness comes from `tgbot.run` through the module, not through imported
names, so it sees this process's own `_RUNNING` / `_PHASE_A` handles — the
reason the API runs inside the bot process (spec §3, approach A).
"""
from __future__ import annotations

import time
from pathlib import Path

from batchlib.manifest import load_state, state_path_for
from control.outputs import final_names
from control.paths import safe_child
import tgbot.run as run_mod


def _status(manifest: Path, jobs: dict) -> str:
    if run_mod.drain_running(manifest):
        return "running"
    if run_mod.phase_a_running(manifest):
        return "phase_a"
    statuses = [str(job.get("status")) for job in jobs.values() if isinstance(job, dict)]
    if any(s == "error" for s in statuses):
        return "error"
    if statuses and all(s == "done" for s in statuses):
        return "done"
    return "stopped"


def _summary(manifest: Path, state_file: Path) -> dict:
    state = load_state(state_file)
    jobs = state.get("runs") or {}
    return {
        "id": manifest.stem,
        "batch": state.get("batch") or None,
        "status": _status(manifest, jobs),
        "updated_at": state_file.stat().st_mtime,
        "jobs_total": len(jobs),
        "jobs_done": sum(1 for j in jobs.values()
                         if isinstance(j, dict) and j.get("status") == "done"),
    }


def list_runs(batch_dir: Path, out_dir: Path) -> list[dict]:
    """Every journal that still has its manifest next to it, newest first."""
    found = []
    for state_file in batch_dir.glob("*.state.json"):
        manifest = batch_dir / (state_file.name[: -len(".state.json")] + ".yaml")
        if manifest.is_file():
            found.append(_summary(manifest, state_file))
    return sorted(found, key=lambda r: r["updated_at"], reverse=True)


def run_detail(batch_dir: Path, out_dir: Path, run_id: str) -> dict | None:
    manifest = safe_child(batch_dir, f"{run_id}.yaml") if run_id else None
    if manifest is None or not manifest.is_file():
        return None
    state_file = state_path_for(manifest)
    if not state_file.is_file():
        return None
    detail = _summary(manifest, state_file)
    jobs = load_state(state_file).get("runs") or {}
    # Whitelisted fields only: the journal also holds absolute `file` paths
    # and the full params_sent payload, neither of which the phone needs.
    detail["jobs"] = [
        {"id": job_id, "status": str(job.get("status")),
         "stages": [{"name": name,
                     "status": str(stage.get("status")),
                     "elapsed_sec": stage.get("elapsed_sec")}
                    for name, stage in (job.get("stages") or {}).items()
                    if isinstance(stage, dict)]}
        for job_id, job in jobs.items() if isinstance(job, dict)
    ]
    lease = run_mod.lease_for(manifest)
    detail["lease"] = None if lease is None else {
        "provider": lease.provider,
        "elapsed_sec": int(time.time() - lease.provisioned_at),
        "abs_max_min": lease.abs_max_min,
    }
    detail["outputs"] = final_names(out_dir, detail["batch"]) if detail["batch"] else []
    return detail
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py' -v`
Expected: all PASS (12 new in `test_batch_control_runs.py`)

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/runs.py scripts/tests/test_batch_control_runs.py
git commit -m "feat(control): read runs and their progress from the on-disk journals

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `httpapi/server.py` — auth, routing, JSON, ETag, errors

**Files:**
- Create: `scripts/httpapi/__init__.py`, `scripts/httpapi/server.py`
- Test: `scripts/tests/test_batch_control_http.py`

**Interfaces:**
- Consumes: `control.runs.list_runs`, `run_detail`; `control.outputs.list_outputs`,
  `resolve_output`; `httpapi.files.send_file` (Task 5 — until then the outputs file route returns
  `501`; Task 5 replaces that line).
- Produces:
  - `make_server(*, token: str, batch_dir: Path, out_dir: Path, host: str = "127.0.0.1",
    port: int = 0, log=print) -> ThreadingHTTPServer` — `port=0` picks a free port (tests).
  - `start_in_thread(server) -> threading.Thread` — daemon thread running `serve_forever`.
  - Routes: `GET /v1/health` → `{"ok": true}`; `GET /v1/runs` → `{"runs": [...]}`;
    `GET /v1/runs/{id}` → detail; `GET /v1/outputs` → `{"outputs": [...]}`;
    `GET /v1/outputs/{batch}/{file}` → file bytes.

`/v1/health` also requires the token: nothing on this API is public.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_batch_control_http.py
import http.client
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import control.runs as runs
import tgbot.run as run_mod
from httpapi.server import make_server, start_in_thread

TOKEN = "t-123"


class HttpTestBase(unittest.TestCase):
    def setUp(self):
        self.batch = Path(tempfile.mkdtemp())
        self.out = Path(tempfile.mkdtemp())
        (self.batch / "r1.yaml").write_text("runs: []\n")
        (self.batch / "r1.state.json").write_text(json.dumps(
            {"batch": "b1", "runs": {"a": {"status": "done", "stages": {}}}}))
        final = self.out / "b1" / "_final"
        final.mkdir(parents=True)
        self.video = bytes(range(256)) * 40            # 10240 bytes
        (final / "a.mp4").write_bytes(self.video)
        for name in ("drain_running", "phase_a_running"):
            p = mock.patch.object(run_mod, name, return_value=False)
            p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(run_mod, "lease_for", return_value=None)
        p.start(); self.addCleanup(p.stop)
        self.logged = []
        self.server = make_server(token=TOKEN, batch_dir=self.batch, out_dir=self.out,
                                  port=0, log=self.logged.append)
        start_in_thread(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, *, token=TOKEN, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        h = dict(headers or {})
        if token is not None:
            h["Authorization"] = f"Bearer {token}"
        conn.request("GET", path, headers=h)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body


class TestAuth(HttpTestBase):
    def test_missing_and_wrong_token_are_401(self):
        for token in (None, "wrong", ""):
            resp, body = self.request("/v1/health", token=token)
            self.assertEqual(resp.status, 401, token)
            self.assertEqual(json.loads(body)["error"]["code"], "unauthorized")

    def test_right_token_is_200(self):
        resp, body = self.request("/v1/health")
        self.assertEqual((resp.status, json.loads(body)), (200, {"ok": True}))


class TestRoutes(HttpTestBase):
    def test_runs_list_and_detail(self):
        resp, body = self.request("/v1/runs")
        self.assertEqual(resp.status, 200)
        self.assertEqual([r["id"] for r in json.loads(body)["runs"]], ["r1"])
        resp, body = self.request("/v1/runs/r1")
        self.assertEqual(json.loads(body)["status"], "done")

    def test_unknown_run_and_unknown_route_are_404(self):
        for path in ("/v1/runs/nope", "/v1/runs/..%2Fetc", "/v1/nope", "/", "/v2/runs"):
            resp, body = self.request(path)
            self.assertEqual(resp.status, 404, path)
            self.assertEqual(json.loads(body)["error"]["code"], "not_found")

    def test_outputs_list(self):
        resp, body = self.request("/v1/outputs")
        self.assertEqual(json.loads(body)["outputs"][0]["batch"], "b1")

    def test_output_outside_final_is_404(self):
        for path in ("/v1/outputs/b1/..%2F..%2Fr1.yaml", "/v1/outputs/..%2Fb1/a.mp4",
                     "/v1/outputs/b1/missing.mp4"):
            resp, _ = self.request(path)
            self.assertEqual(resp.status, 404, path)


class TestEtag(HttpTestBase):
    def test_if_none_match_returns_304(self):
        resp, _ = self.request("/v1/runs/r1")
        etag = resp.getheader("ETag")
        self.assertTrue(etag)
        resp, body = self.request("/v1/runs/r1", headers={"If-None-Match": etag})
        self.assertEqual((resp.status, body), (304, b""))


class TestErrors(HttpTestBase):
    def test_an_exception_is_a_logged_500_and_the_server_survives(self):
        with mock.patch.object(runs, "list_runs", side_effect=RuntimeError("boom")):
            resp, body = self.request("/v1/runs")
        self.assertEqual(resp.status, 500)
        self.assertEqual(json.loads(body)["error"]["code"], "internal")
        self.assertNotIn("boom", body.decode())          # no internals to the client
        self.assertTrue(any("boom" in line for line in self.logged))
        resp, _ = self.request("/v1/health")
        self.assertEqual(resp.status, 200)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'httpapi'`

- [ ] **Step 3: Implement**

`scripts/httpapi/__init__.py`:

```python
"""HTTP adapter over scripts/control/ — no domain logic lives here (spec 2026-09-21 §4.2)."""
```

```python
# scripts/httpapi/server.py
"""The control-plane HTTP API: a thin stdlib server over scripts/control/.

Runs as a daemon thread inside motion-bot (spec §3, approach A) so it sees the
same in-process run handles the bot does. Bound to 127.0.0.1; the only way in
from outside is the Cloudflare Tunnel, which also enforces an Access service
token before a request reaches this process (spec §4.4). The bearer check here
is the second, independent layer.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import control.outputs as outputs
import control.runs as runs


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


NOT_FOUND = ApiError(404, "not_found", "no such resource")


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):          # route stdlib access logs to our logger
        self.server.log("api: " + fmt % args)

    def do_GET(self):
        try:
            self._authenticate()
            self._route()
        except ApiError as exc:
            self._send_json(exc.status, {"error": {"code": exc.code, "message": exc.message}})
        except Exception:
            # Logged in full, returned opaque: the client gets no internals.
            self.server.log("api: request failed\n" + traceback.format_exc())
            self._send_json(500, {"error": {"code": "internal", "message": "internal error"}})

    def _authenticate(self) -> None:
        header = self.headers.get("Authorization", "")
        given = header[len("Bearer "):] if header.startswith("Bearer ") else ""
        if not given or not hmac.compare_digest(given.encode(), self.server.token.encode()):
            raise ApiError(401, "unauthorized", "missing or wrong bearer token")

    def _route(self) -> None:
        # Split on the RAW path before unquoting, so an encoded "/" (%2F) stays
        # inside one segment and is then refused by safe_child, instead of
        # becoming a separator that changes which route matched.
        parts = [unquote(p) for p in urlsplit(self.path).path.split("/")[1:]]
        if parts[:1] != ["v1"]:
            raise NOT_FOUND
        rest = parts[1:]
        s = self.server
        if rest == ["health"]:
            return self._send_json(200, {"ok": True})
        if rest == ["runs"]:
            return self._send_json(200, {"runs": runs.list_runs(s.batch_dir, s.out_dir)})
        if len(rest) == 2 and rest[0] == "runs":
            detail = runs.run_detail(s.batch_dir, s.out_dir, rest[1])
            if detail is None:
                raise NOT_FOUND
            return self._send_json(200, detail)
        if rest == ["outputs"]:
            return self._send_json(200, {"outputs": outputs.list_outputs(s.out_dir)})
        if len(rest) == 3 and rest[0] == "outputs":
            path = outputs.resolve_output(s.out_dir, rest[1], rest[2])
            if path is None:
                raise NOT_FOUND
            raise ApiError(501, "not_implemented", "file streaming arrives in Task 5")
        raise NOT_FOUND

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        etag = '"' + hashlib.sha1(body).hexdigest() + '"'
        if status == 200 and self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if status == 200:
            self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def make_server(*, token: str, batch_dir: Path, out_dir: Path,
                host: str = "127.0.0.1", port: int = 0, log=print) -> _Server:
    if not token:
        raise ValueError("an empty token would authenticate nothing")
    server = _Server((host, port), _Handler)
    server.token, server.batch_dir, server.out_dir, server.log = token, batch_dir, out_dir, log
    return server


def start_in_thread(server: _Server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="control-api", daemon=True)
    thread.start()
    return thread
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py' -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/httpapi/__init__.py scripts/httpapi/server.py scripts/tests/test_batch_control_http.py
git commit -m "feat(httpapi): authenticated read-only API for runs and outputs

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `httpapi/files.py` — streaming with `Range`

`AVPlayer` requests `Range: bytes=0-1` first and then seeks with further ranges; without `206`
support it will not play a remote file. One range per request is all it sends; a multi-range
request gets the whole file (`200`), which RFC 9110 allows.

**Files:**
- Create: `scripts/httpapi/files.py`
- Modify: `scripts/httpapi/server.py` (the `raise ApiError(501, ...)` line in `_route`)
- Test: `scripts/tests/test_batch_control_http.py` (add a class)

**Interfaces:**
- Produces:
  - `parse_range(header: str | None, size: int) -> tuple[int, int] | None | Literal["unsatisfiable"]`
    — inclusive `(start, end)`; `None` = serve the whole file.
  - `send_file(handler: BaseHTTPRequestHandler, path: Path) -> None`

- [ ] **Step 1: Write the failing tests** — append to `scripts/tests/test_batch_control_http.py`
  (above the `if __name__` guard):

```python
from httpapi.files import parse_range


class TestParseRange(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(parse_range("bytes=0-1", 100), (0, 1))
        self.assertEqual(parse_range("bytes=10-", 100), (10, 99))
        self.assertEqual(parse_range("bytes=-10", 100), (90, 99))
        self.assertEqual(parse_range("bytes=90-500", 100), (90, 99))   # end clamped

    def test_whole_file_cases(self):
        for header in (None, "", "items=0-1", "bytes=0-1,5-6", "bytes=abc"):
            self.assertIsNone(parse_range(header, 100), header)

    def test_unsatisfiable(self):
        for header in ("bytes=100-", "bytes=5-2", "bytes=-0"):
            self.assertEqual(parse_range(header, 100), "unsatisfiable", header)


class TestFileStreaming(HttpTestBase):
    def test_full_file(self):
        resp, body = self.request("/v1/outputs/b1/a.mp4")
        self.assertEqual(resp.status, 200)
        self.assertEqual(body, self.video)
        self.assertEqual(resp.getheader("Accept-Ranges"), "bytes")
        self.assertEqual(resp.getheader("Content-Type"), "video/mp4")

    def test_range(self):
        resp, body = self.request("/v1/outputs/b1/a.mp4", headers={"Range": "bytes=100-199"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(body, self.video[100:200])
        self.assertEqual(resp.getheader("Content-Range"), f"bytes 100-199/{len(self.video)}")

    def test_unsatisfiable_range_is_416(self):
        resp, _ = self.request("/v1/outputs/b1/a.mp4",
                               headers={"Range": f"bytes={len(self.video)}-"})
        self.assertEqual(resp.status, 416)
        self.assertEqual(resp.getheader("Content-Range"), f"bytes */{len(self.video)}")
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'httpapi.files'`

- [ ] **Step 3: Implement**

```python
# scripts/httpapi/files.py
"""File responses with single-range support, which AVPlayer needs to play and seek."""
from __future__ import annotations

import mimetypes
import re
from http.server import BaseHTTPRequestHandler
from pathlib import Path

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 1024 * 1024


def parse_range(header: str | None, size: int):
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (not m.group(1) and not m.group(2)):
        return None                     # malformed or multi-range: whole file
    first, last = m.group(1), m.group(2)
    if not first:                       # suffix form: last N bytes
        n = int(last)
        if n == 0:
            return "unsatisfiable"
        return max(size - n, 0), size - 1
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return "unsatisfiable"
    return start, end


def send_file(handler: BaseHTTPRequestHandler, path: Path) -> None:
    size = path.stat().st_size
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    rng = parse_range(handler.headers.get("Range"), size)
    if rng == "unsatisfiable":
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    start, end = rng if rng else (0, size - 1)
    length = end - start + 1 if size else 0
    handler.send_response(206 if rng else 200)
    handler.send_header("Content-Type", kind)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if rng:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    with path.open("rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(_CHUNK, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)
```

In `scripts/httpapi/server.py` add
`from httpapi.files import send_file` to the imports and replace

```python
            raise ApiError(501, "not_implemented", "file streaming arrives in Task 5")
```

with

```python
            return send_file(self, path)
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py' -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/httpapi/files.py scripts/httpapi/server.py scripts/tests/test_batch_control_http.py
git commit -m "feat(httpapi): stream outputs with single-range support for AVPlayer

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Start the API from `motion-bot`

**Files:**
- Modify: `scripts/tgbot/bot.py` (`main()`, just before the `log(f"started, ...")` line; plus a new
  `_start_control_api` function directly above `main()`)
- Modify: `.env.example` (new section after the Vast-in-the-bot section)
- Test: `scripts/tests/test_batch_control_http.py` (add a class)

**Interfaces:**
- Consumes: `httpapi.server.make_server`, `start_in_thread`
- Produces: `bot._start_control_api(tg, chat_id: int) -> object | None` — the server, or `None`
  when disabled or failed.

Behaviour: no `CONTROL_API_TOKEN` → log one line and return `None` (the API is opt-in, so an old
`.env` keeps a working bot). Start failure (port taken, bad port value) → log, send one Telegram
message, return `None`; the bot keeps polling (spec §4.2).

- [ ] **Step 1: Write the failing tests** — append to `scripts/tests/test_batch_control_http.py`:

```python
import socket


class TestBotStartsApi(unittest.TestCase):
    def setUp(self):
        import tgbot.bot as bot
        self.bot = bot
        self.tg = mock.Mock()

    def env(self, **values):
        return mock.patch.object(self.bot, "env_get",
                                 side_effect=lambda _path, key: values.get(key))

    def test_no_token_means_disabled_and_silent(self):
        with self.env():
            self.assertIsNone(self.bot._start_control_api(self.tg, 1))
        self.tg.send_message.assert_not_called()

    def test_starts_on_the_configured_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        with self.env(CONTROL_API_TOKEN="x", CONTROL_API_PORT=str(port)):
            server = self.bot._start_control_api(self.tg, 1)
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        self.assertEqual(server.server_address, ("127.0.0.1", port))

    def test_port_in_use_reports_and_keeps_the_bot_alive(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); s.listen(); port = s.getsockname()[1]
            with self.env(CONTROL_API_TOKEN="x", CONTROL_API_PORT=str(port)):
                self.assertIsNone(self.bot._start_control_api(self.tg, 1))
        self.tg.send_message.assert_called_once()
        self.assertIn("API", self.tg.send_message.call_args.args[1])
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_http.py' -v`
Expected: FAIL — `AttributeError: module 'tgbot.bot' has no attribute '_start_control_api'`

- [ ] **Step 3: Implement**

In `scripts/tgbot/bot.py`, add to the imports near the top:

```python
from httpapi.server import make_server, start_in_thread
```

Directly above `def main() -> int:`:

```python
def _start_control_api(tg: Tg, chat_id: int):
    """Start the phone app's HTTP API in a daemon thread, or explain why not.

    In this process on purpose (spec 2026-09-21 §3): the API must see the same
    _RUNNING / _PHASE_A handles the bot does, or "is this run live" has two
    answers. Opt-in via CONTROL_API_TOKEN so a VPS whose .env predates it keeps
    a working bot. A failure to start never stops the bot — Telegram is still
    the primary interface — but it is said out loud once, because the phone
    app would otherwise just show a connection error with no reason.
    """
    token = env_get(ROOT / ".env", "CONTROL_API_TOKEN")
    if not token:
        log("control API disabled (CONTROL_API_TOKEN unset)")
        return None
    try:
        port = int(env_get(ROOT / ".env", "CONTROL_API_PORT") or 8787)
        server = make_server(token=token, batch_dir=ROOT / "batch", out_dir=ROOT / "out",
                             port=port, log=log)
    except (OSError, ValueError) as exc:
        log(f"control API failed to start: {exc!r}")
        try:
            tg.send_message(chat_id, f"Phone API did not start: {exc}")
        except TgError:
            pass
        return None
    start_in_thread(server)
    log(f"control API listening on 127.0.0.1:{port}")
    return server
```

In `main()`, immediately before `log(f"started, api={base}, ...")`:

```python
    _start_control_api(tg, allowed_user_id)
```

In `.env.example`, after the `VAST_GPU=` line and its blank line, add:

```
# --- Phone app control-plane API (docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md)
# Served by motion-bot on the VPS at 127.0.0.1:CONTROL_API_PORT and reached only through the
# Cloudflare Tunnel. Empty token = API off. Generate: python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
CONTROL_API_TOKEN=
CONTROL_API_PORT=8787
# Used only by `make api-smoke` (run from the Mac): the tunnel hostname and the Access service token.
CONTROL_API_URL=
CF_ACCESS_CLIENT_ID=
CF_ACCESS_CLIENT_SECRET=
```

- [ ] **Step 4: Run the new tests, the whole gate, and a dry bot round**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_control_*.py' -v`
Expected: all PASS
Run: `make batch-test`
Expected: OK
Run: `make check-job-types && make check-batch-params`
Expected: both pass (unchanged by this slice; confirms nothing else drifted)

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py .env.example scripts/tests/test_batch_control_http.py
git commit -m "feat(tgbot): serve the control-plane API from motion-bot when CONTROL_API_TOKEN is set

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Tunnel, Access, smoke script, and measurement

The code side is a small script plus docs; the setup itself is manual in the Cloudflare dashboard
and on the VPS, and is done **with the user** — it creates a public hostname and a credential.

**Files:**
- Create: `scripts/vps/api-smoke.sh`
- Modify: `Makefile` (add `api-smoke` to `.PHONY` and a target next to `bot-dry`)
- Modify: `scripts/vps/README.md` (new section "Phone API (Cloudflare Tunnel + Access)")

- [ ] **Step 1: Write the smoke script**

```bash
#!/usr/bin/env bash
# scripts/vps/api-smoke.sh — prove both auth layers on the phone API, through the real tunnel.
# Expects, in order: 403 from Cloudflare Access without its headers; 401 from the API with Access
# headers but no bearer; 200 with both. Reads CONTROL_API_URL, CF_ACCESS_CLIENT_ID,
# CF_ACCESS_CLIENT_SECRET and CONTROL_API_TOKEN from the root .env.
set -euo pipefail
cd "$(dirname "$0")/../.."
get() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }
URL="$(get CONTROL_API_URL)"; ID="$(get CF_ACCESS_CLIENT_ID)"
SECRET="$(get CF_ACCESS_CLIENT_SECRET)"; TOKEN="$(get CONTROL_API_TOKEN)"
for v in URL ID SECRET TOKEN; do
  [ -n "${!v}" ] || { echo "missing $v in .env" >&2; exit 2; }
done
code() { curl -s -o /dev/null -w '%{http_code}' "$@" "$URL/v1/health"; }
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1 → $2"; else echo "FAIL $1 → $2 (want $3)"; fail=1; fi; }
check "no Access headers"       "$(code)" 403
check "Access, no bearer"       "$(code -H "CF-Access-Client-Id: $ID" -H "CF-Access-Client-Secret: $SECRET")" 401
check "Access + bearer"         "$(code -H "CF-Access-Client-Id: $ID" -H "CF-Access-Client-Secret: $SECRET" -H "Authorization: Bearer $TOKEN")" 200
exit $fail
```

`chmod +x scripts/vps/api-smoke.sh`

Note: Cloudflare Access may answer an unauthenticated request with a `302` to its login page
rather than `403`, depending on the application's settings. If the first check reports `302`,
set the Access application to service-auth-only (policy action "Service Auth") and re-run; do not
loosen the check to accept `302`.

- [ ] **Step 2: Makefile target** — add `api-smoke` to the `.PHONY` line and, after `bot-dry`:

```make
api-smoke: ## Phone API through the tunnel: 403 without Access, 401 without bearer, 200 with both
	@bash scripts/vps/api-smoke.sh
```

- [ ] **Step 3: README section** — append to `scripts/vps/README.md`:

````markdown
## Phone API (Cloudflare Tunnel + Access)

`motion-bot` serves the phone app's API on `127.0.0.1:${CONTROL_API_PORT:-8787}` when
`CONTROL_API_TOKEN` is set in `/opt/motion-clone/.env`
(design: `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md`). It never listens on
a public interface; the tunnel is the only way in, and Access is checked at Cloudflare's edge
before anything reaches the box.

One-time setup:

1. Zero Trust → Networks → Tunnels → create a tunnel (`motion-vps-api`), choose the Debian
   connector, and run the `cloudflared service install <token>` command it prints **on the VPS**.
   `systemctl status cloudflared` must be `active (running)`.
2. In the tunnel, add a public hostname (e.g. `api-motion.<your domain>`) → `http://localhost:8787`.
3. Zero Trust → Access → Service Auth → create a service token. Copy the Client ID and Secret —
   the secret is shown once.
4. Access → Applications → self-hosted app for that hostname, with one policy: action
   **Service Auth**, include the service token from step 3.
5. On the VPS: set `CONTROL_API_TOKEN` in `.env`, then `systemctl restart motion-bot`;
   `journalctl -u motion-bot | grep "control API"` must show `listening on 127.0.0.1:8787`.
6. On the Mac: put `CONTROL_API_URL`, `CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET` and the same
   `CONTROL_API_TOKEN` in the root `.env`, then `make api-smoke` — all three lines must be `ok`.

The same three secrets go into the iPhone app's Keychain. None of them is ever committed.
````

- [ ] **Step 4: Verify locally, then commit**

Run: `bash -n scripts/vps/api-smoke.sh && make help | grep api-smoke`
Expected: no syntax error; the target is listed
Run: `make batch-test && motions-studio/setup/scrub-secrets.sh --check`
Expected: OK; exit 0

```bash
git add scripts/vps/api-smoke.sh Makefile scripts/vps/README.md
git commit -m "docs(vps): phone API tunnel + Access setup and a three-layer smoke check

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Deploy and measure (with the user — outward-facing)**

Pushing `scripts/**` to `main` auto-deploys to `motion-vps`, so this branch reaches the VPS only
through a PR merge the user approves. After it is merged and deployed:

1. Before setting the token, record the baseline:
   `doctl compute ssh motion-vps --ssh-command "ps -o rss= -p \$(systemctl show -p MainPID --value motion-bot)"`
2. Do README steps 1–6 with the user.
3. Record RSS again after a few `GET /v1/runs` and one output stream.
4. Write both numbers, the date, and the droplet size into the new README section as
   "Measured <date>: motion-bot RSS <before> → <after> KB" — spec §8 treats "a thread is cheap on a
   1 GB box" as an assumption until this line exists.
