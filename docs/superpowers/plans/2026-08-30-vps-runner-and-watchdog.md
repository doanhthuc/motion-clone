# VPS Runner and Pod Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a Linux VPS able to rent a GPU pod, run a batch on it, and guarantee the pod dies afterwards — with no human at a keyboard.

**Architecture:** A new `make drain FILE=…` entry point orchestrates provision → bootstrap → `run_batch` → collect diagnostics → destroy, writing a `pod-lease.json` as it goes. A separate `pod_watchdog` daemon with its own clock reads that lease plus the runner's existing `state.json` journal and destroys any pod that outlives its deadline. `scripts/batchlib/` is **not modified**: the watchdog derives its heartbeat from the journal's mtime, which `run_one()` already updates twice per stage.

**Tech Stack:** Python 3 stdlib only (matching `batchlib` — zero third-party deps), `unittest`, bash, systemd, `runpodctl`.

**Spec:** `docs/superpowers/specs/2026-08-30-telegram-batch-control-design.md`

This plan implements **Plan 1 of 2**. It covers spec sections 3 (architecture), 7 (drain), 8 (money safety), 9 (diagnostics before destroy), and 12 (VPS scope). The Telegram bot — spec sections 4, 5, 6, 10 — is Plan 2 and depends on the interfaces this plan produces.

## Global Constraints

- **Python 3 standard library only.** `batchlib` has zero third-party dependencies; keep it that way. No `requests`, no `pyyaml` outside what `batchlib` already imports.
- **Do not modify `scripts/batchlib/`.** Every file under it stays byte-identical. If a task seems to need a change there, stop and escalate — the watchdog is designed specifically to avoid it.
- **Comments in English.** Existing Vietnamese comments are legacy; do not translate them, do not copy the style. Do not add `# #region ALD <date>` markers — that convention is retired.
- **Explain why, with the measured number and the date it was measured.** This repo's comments carry evidence, not opinions.
- **Money claims come from the invoice.** Any cost a program prints must come from `runpodctl billing pods`, never from `currentSpendPerHr`.
- **Test file naming:** `scripts/tests/test_batch_<module>.py`, discovered by `make batch-test`.
- **`motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit.** The repo is public.
- Stage timeouts are declared once, in `scripts/batchlib/pipelines.py`: tryon 20, motion 60, character-swap 60, enhance 90 minutes. Never hardcode them anywhere else — import `STAGES`.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/batchlib_ext/lease.py` (create) | Read/write/clear `pod-lease.json`. Storage only, no policy. |
| `scripts/batchlib_ext/watchdog.py` (create) | Pure decision functions: given a lease, a journal and a clock, should this pod die? No I/O. |
| `scripts/batchlib_ext/podctl.py` (create) | Thin adapter over `runpodctl`. One protocol, one real impl, so tests use a fake. |
| `scripts/pod_watchdog.py` (create) | The daemon: loop, logging, wiring. Thin — all logic lives in the modules above. |
| `scripts/drain.py` (create) | The orchestration entry point behind `make drain`. Thin: it shells out to `batch_run.py` rather than re-wiring the phases. |
| `scripts/batch_run.py` (modify) | Add a distinct exit code for "stopped because there is no pod", so `drain.py` can tell that apart from a real failure. |
| `scripts/vps/README.md` (create) | How to stand up the VPS, including the systemd units. |
| `scripts/vps/pod-watchdog.service` (create) | systemd unit for the daemon. |
| `scripts/gpu-preflight.sh` (modify) | Warn when the VPS holds a live lease — pod ownership guard. |
| `Makefile` (modify) | Add `drain` and `watchdog-dry` targets. |

**Why a new `scripts/batchlib_ext/` package rather than adding to `batchlib/`:** the Global Constraints forbid touching `batchlib`, and a reviewer needs to be able to verify that at a glance with `git diff --stat`. A sibling package makes the boundary mechanical instead of a promise.

---

### Task 1: Lease storage

**Files:**
- Create: `scripts/batchlib_ext/__init__.py` (empty)
- Create: `scripts/batchlib_ext/lease.py`
- Test: `scripts/tests/test_batch_lease.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Lease` (frozen dataclass: `pod_id: str`, `provisioned_at: float`, `manifest: str`, `abs_max_min: int`), `write_lease(path: Path, lease: Lease) -> None`, `read_lease(path: Path) -> Lease | None`, `clear_lease(path: Path) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_lease.py
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease, clear_lease, read_lease, write_lease


class TestLease(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "pod-lease.json"

    def test_roundtrip(self):
        lease = Lease(pod_id="abc123", provisioned_at=1000.0,
                      manifest="batch/x.yaml", abs_max_min=240)
        write_lease(self.tmp, lease)
        self.assertEqual(read_lease(self.tmp), lease)

    def test_missing_file_is_none(self):
        self.assertIsNone(read_lease(self.tmp))

    def test_corrupt_file_is_none_not_crash(self):
        # A watchdog that crashes on a half-written lease stops guarding the
        # thing it exists to guard. Truncated JSON must read as "no lease".
        self.tmp.write_text('{"pod_id": "abc', encoding="utf-8")
        self.assertIsNone(read_lease(self.tmp))

    def test_clear_is_idempotent(self):
        write_lease(self.tmp, Lease("a", 1.0, "b.yaml", 240))
        clear_lease(self.tmp)
        clear_lease(self.tmp)
        self.assertIsNone(read_lease(self.tmp))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_lease.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batchlib_ext'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/batchlib_ext/lease.py
"""Who currently owns a rented pod, on disk.

Storage only — no policy. The decision to kill a pod lives in watchdog.py, so
it can be unit-tested without touching a filesystem or RunPod.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Lease:
    pod_id: str
    provisioned_at: float   # unix seconds, set once at provision time
    manifest: str           # path to the manifest this pod was rented for
    abs_max_min: int        # tier-2 ceiling, computed once at provision time


def write_lease(path: Path, lease: Lease) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(lease), indent=2), encoding="utf-8")
    tmp.replace(path)   # atomic: a reader never sees a half-written lease


def read_lease(path: Path) -> Lease | None:
    """None means 'no lease', including when the file is unreadable garbage.

    Raising here would take the watchdog down, and a dead watchdog bills
    $0.99/hour silently. Unreadable is treated as absent on purpose: tier 3
    reconciliation then sees a pod with no lease and destroys it, which is the
    safe direction to fail in.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Lease(pod_id=str(raw["pod_id"]),
                     provisioned_at=float(raw["provisioned_at"]),
                     manifest=str(raw["manifest"]),
                     abs_max_min=int(raw["abs_max_min"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_lease(path: Path) -> None:
    path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_lease.py' -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib_ext/__init__.py scripts/batchlib_ext/lease.py scripts/tests/test_batch_lease.py
git commit -m "Watchdog: pod lease file, read as absent when corrupt

An unreadable lease must not raise: a watchdog that dies stops guarding a
\$0.99/hour pod. Treating it as absent hands the pod to tier-3
reconciliation, which destroys it — the safe direction to fail in."
```

---

### Task 2: Deadline policy (tiers 1 and 2)

**Files:**
- Create: `scripts/batchlib_ext/watchdog.py`
- Test: `scripts/tests/test_batch_watchdog.py`

**Interfaces:**
- Consumes: `Lease` from Task 1; `STAGES` from `scripts/batchlib/pipelines.py`.
- Produces: `Verdict` (frozen dataclass: `kill: bool`, `reason: str`), `in_flight_stage(state: dict) -> str | None`, `decide(*, lease: Lease, state: dict, journal_mtime: float, now: float, slack_min: int = 15) -> Verdict`.

**Why mtime and not a heartbeat call:** `run_one()` in `scripts/batchlib/runner.py` already calls `save_state()` twice per stage — at line 196 when the job is submitted, and at line 226 when the stage finishes. That makes the journal's mtime a *progress* signal, not merely a liveness signal, and it means `batchlib` needs no modification.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_watchdog.py
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease
from batchlib_ext.watchdog import decide, in_flight_stage

MIN = 60.0
LEASE = Lease(pod_id="p1", provisioned_at=0.0, manifest="batch/x.yaml", abs_max_min=240)


def state_with(stage: str, status: str) -> dict:
    return {"batch": "b1", "runs": {"r1": {"status": "running",
                                           "stages": {stage: {"status": status}}}}}


class TestInFlightStage(unittest.TestCase):
    def test_finds_the_running_stage(self):
        self.assertEqual(in_flight_stage(state_with("enhance", "running")), "enhance")

    def test_none_when_nothing_running(self):
        self.assertIsNone(in_flight_stage(state_with("enhance", "done")))

    def test_none_on_empty_state(self):
        self.assertIsNone(in_flight_stage({}))


class TestTier1(unittest.TestCase):
    def test_alive_well_inside_the_stage_timeout(self):
        # enhance has timeout_min=90; journal touched 10 min ago
        v = decide(lease=LEASE, state=state_with("enhance", "running"),
                   journal_mtime=0.0, now=10 * MIN)
        self.assertFalse(v.kill)

    def test_killed_past_stage_timeout_plus_slack(self):
        # 90 + 15 = 105 min budget; 106 min of silence means the runner is gone
        v = decide(lease=LEASE, state=state_with("enhance", "running"),
                   journal_mtime=0.0, now=106 * MIN)
        self.assertTrue(v.kill)
        self.assertIn("enhance", v.reason)

    def test_short_stage_gets_a_short_leash(self):
        # tryon is timeout_min=20, so 36 min of silence is already fatal —
        # this is the whole point of deriving the leash per stage
        v = decide(lease=LEASE, state=state_with("tryon", "running"),
                   journal_mtime=0.0, now=36 * MIN)
        self.assertTrue(v.kill)

    def test_no_stage_running_falls_back_to_longest_timeout(self):
        # Between stages, or before the first job is submitted, there is no
        # in-flight stage to size the leash from. Use the longest stage
        # timeout so we never kill a batch that is merely about to start
        # something slow.
        v = decide(lease=LEASE, state=state_with("enhance", "done"),
                   journal_mtime=0.0, now=104 * MIN)
        self.assertFalse(v.kill)
        v = decide(lease=LEASE, state=state_with("enhance", "done"),
                   journal_mtime=0.0, now=106 * MIN)
        self.assertTrue(v.kill)


class TestTier2(unittest.TestCase):
    def test_absolute_ceiling_ignores_a_healthy_heartbeat(self):
        # The runner is alive and touching the journal every minute, but it has
        # been going for longer than the ceiling: a stuck loop still bills.
        v = decide(lease=LEASE, state=state_with("enhance", "running"),
                   journal_mtime=241 * MIN, now=241 * MIN)
        self.assertTrue(v.kill)
        self.assertIn("ceiling", v.reason)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_watchdog.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batchlib_ext.watchdog'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/batchlib_ext/watchdog.py
"""Should this pod be destroyed right now? Pure functions, no I/O.

Everything here takes the clock as an argument so the tiers can be tested
without sleeping and without a real pod.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.pipelines import STAGES

from .lease import Lease

# Longest declared stage timeout (enhance, 90 min as of 2026-08-30). Derived,
# never hardcoded: pipelines.py is the single place stage timeouts are declared.
LONGEST_STAGE_MIN = max(s.timeout_min for s in STAGES.values())


@dataclass(frozen=True)
class Verdict:
    kill: bool
    reason: str


def in_flight_stage(state: dict) -> str | None:
    """Name of the stage currently marked running, if any."""
    for entry in (state.get("runs") or {}).values():
        for name, stage in (entry.get("stages") or {}).items():
            if stage.get("status") == "running":
                return name
    return None


def decide(*, lease: Lease, state: dict, journal_mtime: float, now: float,
           slack_min: int = 15) -> Verdict:
    """Tier 1 (dead-man's switch) then tier 2 (absolute ceiling).

    Tier 1 leash is the CURRENT stage's own timeout plus slack. Anything
    shorter would kill a stage the runner still legitimately considers alive —
    enhance really does take up to 90 minutes with no journal write in between.
    Anything longer keeps paying $0.99/hour after a crash.
    """
    age_min = (now - lease.provisioned_at) / 60.0
    if age_min > lease.abs_max_min:
        return Verdict(True, f"absolute ceiling: alive {age_min:.0f} min "
                             f"> {lease.abs_max_min} min")

    stage = in_flight_stage(state)
    budget = (STAGES[stage].timeout_min if stage else LONGEST_STAGE_MIN) + slack_min
    silent_min = (now - journal_mtime) / 60.0
    if silent_min > budget:
        where = stage or "no stage running"
        return Verdict(True, f"journal silent {silent_min:.0f} min "
                             f"> {budget} min budget ({where})")

    return Verdict(False, "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_watchdog.py' -v`
Expected: 8 tests PASS

- [ ] **Step 5: Verify batchlib is still untouched**

Run: `git status --short scripts/batchlib/`
Expected: no output

- [ ] **Step 6: Commit**

```bash
git add scripts/batchlib_ext/watchdog.py scripts/tests/test_batch_watchdog.py
git commit -m "Watchdog: tier-1 dead-man's switch and tier-2 ceiling

The heartbeat is the journal's mtime, not a callback: run_one() already
calls save_state() at runner.py:196 and :226, so this needs no change to
batchlib and measures progress rather than mere liveness.

The tier-1 leash is the running stage's own timeout_min plus 15 min,
read from pipelines.py. A flat leash cannot work: enhance legitimately
runs 90 min without a journal write, while tryon is 20."
```

---

### Task 3: RunPod adapter and tier-3 reconciliation

**Files:**
- Create: `scripts/batchlib_ext/podctl.py`
- Modify: `scripts/batchlib_ext/watchdog.py` (add `reconcile`)
- Test: `scripts/tests/test_batch_podctl.py`

**Interfaces:**
- Consumes: `Lease` from Task 1.
- Produces: `PodInfo` (frozen dataclass: `pod_id: str`, `name: str`), `PodControl` protocol with `list_pods() -> list[PodInfo]` and `destroy(pod_id: str) -> None`, `RunpodCtl` (real impl), and `reconcile(*, pods: list[PodInfo], lease: Lease | None, first_seen: dict[str, float], now: float, grace_min: int = 10) -> tuple[list[str], dict[str, float]]` returning (pod ids to destroy, updated first_seen).

**Why a grace period:** the drain sequence creates the pod and *then* writes the lease. Without a grace window, a watchdog tick landing in that gap would destroy a pod that is one second old and perfectly legitimate.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_podctl.py
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.lease import Lease
from batchlib_ext.podctl import PodInfo
from batchlib_ext.watchdog import reconcile

MIN = 60.0
LEASE = Lease(pod_id="mine", provisioned_at=0.0, manifest="batch/x.yaml", abs_max_min=240)


class TestReconcile(unittest.TestCase):
    def test_leased_pod_is_left_alone(self):
        kill, _ = reconcile(pods=[PodInfo("mine", "motion")], lease=LEASE,
                            first_seen={}, now=100 * MIN)
        self.assertEqual(kill, [])

    def test_orphan_inside_grace_is_left_alone(self):
        # Covers the gap between `runpodctl pod create` returning and the lease
        # being written: the pod is real and legitimate, just not recorded yet.
        pods = [PodInfo("stray", "motion")]
        kill, seen = reconcile(pods=pods, lease=None, first_seen={}, now=0.0)
        self.assertEqual(kill, [])
        self.assertEqual(seen, {"stray": 0.0})

    def test_orphan_past_grace_is_destroyed(self):
        pods = [PodInfo("stray", "motion")]
        kill, _ = reconcile(pods=pods, lease=None,
                            first_seen={"stray": 0.0}, now=11 * MIN)
        self.assertEqual(kill, ["stray"])

    def test_pod_that_is_not_the_leased_one_is_an_orphan(self):
        # Two machines both believing they own a pod is the failure mode this
        # tier exists for. A lease for "mine" does not protect "other".
        kill, _ = reconcile(pods=[PodInfo("other", "motion")], lease=LEASE,
                            first_seen={"other": 0.0}, now=11 * MIN)
        self.assertEqual(kill, ["other"])

    def test_vanished_pod_is_forgotten(self):
        _, seen = reconcile(pods=[], lease=None,
                            first_seen={"gone": 0.0}, now=11 * MIN)
        self.assertEqual(seen, {})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_podctl.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'batchlib_ext.podctl'`

- [ ] **Step 3: Write the adapter**

```python
# scripts/batchlib_ext/podctl.py
"""Thin adapter over runpodctl, so the watchdog's logic can be tested with a fake.

Deliberately minimal: list and destroy. Provisioning stays in
scripts/pod-provision.sh, which already carries the POD_MAX_HOURS safety net
and the dry-run gate.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PodInfo:
    pod_id: str
    name: str


class PodControl(Protocol):
    def list_pods(self) -> list[PodInfo]: ...
    def destroy(self, pod_id: str) -> None: ...


class RunpodCtl:
    def list_pods(self) -> list[PodInfo]:
        out = subprocess.run(["runpodctl", "get", "pod", "-o", "json"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            # Returning [] would read as "no pods", and tier 3 would then do
            # nothing — which is the safe direction when we cannot see.
            # Destroying on a failed query would be the unsafe one.
            raise RuntimeError(f"runpodctl get pod failed: {out.stderr.strip()}")
        data = json.loads(out.stdout or "[]")
        return [PodInfo(pod_id=str(p["id"]), name=str(p.get("name", "")))
                for p in data]

    def destroy(self, pod_id: str) -> None:
        subprocess.run(["runpodctl", "remove", "pod", pod_id],
                       check=True, timeout=120)
```

- [ ] **Step 4: Add `reconcile` to watchdog.py**

Append to `scripts/batchlib_ext/watchdog.py`:

```python
def reconcile(*, pods: list["PodInfo"], lease: Lease | None,
              first_seen: dict[str, float], now: float,
              grace_min: int = 10) -> tuple[list[str], dict[str, float]]:
    """Tier 3: any pod we can see that no lease claims is an orphan.

    This is the net for the worst case — the lease file was lost, or two
    machines both think they own the pod (see the ownership decision in the
    spec). It runs on daemon start too, which covers a VPS reboot mid-batch.

    The grace window exists because provisioning writes the lease AFTER the
    pod exists. Without it, a tick landing in that gap kills a pod that is one
    second old.
    """
    leased = lease.pod_id if lease else None
    seen = {p.pod_id: first_seen.get(p.pod_id, now) for p in pods}
    kill = [p.pod_id for p in pods
            if p.pod_id != leased and (now - seen[p.pod_id]) / 60.0 > grace_min]
    return kill, seen
```

Add `from .podctl import PodInfo` to the imports at the top of `watchdog.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_*.py' -v`
Expected: all pass, including the pre-existing `batchlib` suites

- [ ] **Step 6: Commit**

```bash
git add scripts/batchlib_ext/podctl.py scripts/batchlib_ext/watchdog.py scripts/tests/test_batch_podctl.py
git commit -m "Watchdog: tier-3 reconciliation against RunPod

A pod no lease claims is an orphan. This is the net for a lost lease
file and for two machines both believing they own the pod, and it runs
on daemon start so a VPS reboot mid-batch is covered.

A failed 'runpodctl get pod' raises rather than returning []: not seeing
must never be read as 'nothing there', because tier 3 destroys things."
```

---

### Task 4: The watchdog daemon

**Files:**
- Create: `scripts/pod_watchdog.py`
- Create: `scripts/vps/pod-watchdog.service`
- Modify: `Makefile` (add `watchdog-dry` target)

**Interfaces:**
- Consumes: `read_lease`, `clear_lease`, `decide`, `reconcile`, `RunpodCtl`, `PodInfo`.
- Produces: the executable `python3 scripts/pod_watchdog.py [--once] [--dry-run]`.

- [ ] **Step 1: Write the daemon**

```python
#!/usr/bin/env python3
"""Destroy any GPU pod that outlives its deadline. Runs on the VPS, forever.

This is a SEPARATE process from the runner on purpose. A `finally: destroy`
inside the runner does not survive the case it exists for: a hung or
OOM-killed runner never reaches `finally`. Killing something already dead
requires a second clock.

    python3 scripts/pod_watchdog.py            # daemon
    python3 scripts/pod_watchdog.py --once     # one tick, for cron or testing
    python3 scripts/pod_watchdog.py --dry-run  # report verdicts, destroy nothing
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.manifest import load_state, state_path_for
from batchlib_ext.lease import clear_lease, read_lease
from batchlib_ext.podctl import RunpodCtl
from batchlib_ext.watchdog import decide, reconcile

ROOT = Path(__file__).resolve().parents[1]
LEASE_PATH = ROOT / "batch" / "pod-lease.json"
TICK_SEC = 60


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} watchdog: {msg}", flush=True)


def tick(pods_api, first_seen: dict[str, float], *, now: float,
         dry_run: bool) -> dict[str, float]:
    lease = read_lease(LEASE_PATH)

    if lease is not None:
        journal = state_path_for(ROOT / lease.manifest)
        mtime = journal.stat().st_mtime if journal.is_file() else lease.provisioned_at
        verdict = decide(lease=lease, state=load_state(journal),
                         journal_mtime=mtime, now=now)
        if verdict.kill:
            log(f"KILL {lease.pod_id} — {verdict.reason}")
            if not dry_run:
                pods_api.destroy(lease.pod_id)
                clear_lease(LEASE_PATH)
            return first_seen

    try:
        pods = pods_api.list_pods()
    except RuntimeError as exc:
        # Not seeing is not the same as nothing being there. Skip this tick.
        log(f"cannot list pods, skipping reconciliation: {exc}")
        return first_seen

    kill, seen = reconcile(pods=pods, lease=lease, first_seen=first_seen, now=now)
    for pod_id in kill:
        log(f"KILL {pod_id} — orphan, no lease claims it")
        if not dry_run:
            pods_api.destroy(pod_id)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pods_api = RunpodCtl()
    first_seen: dict[str, float] = {}
    log(f"started, lease={LEASE_PATH}, dry_run={args.dry_run}")
    while True:
        try:
            first_seen = tick(pods_api, first_seen, now=time.time(),
                              dry_run=args.dry_run)
        except Exception as exc:            # never let one bad tick end the guard
            log(f"tick failed, continuing: {exc!r}")
        if args.once:
            return 0
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify it runs and destroys nothing**

Run: `python3 scripts/pod_watchdog.py --once --dry-run`
Expected: prints `started, …` then either a `cannot list pods` line (no `runpodctl` configured locally) or nothing. Exit code 0. **No pod is destroyed.**

- [ ] **Step 3: Write the systemd unit**

```ini
# scripts/vps/pod-watchdog.service
# Install: cp to /etc/systemd/system/, then
#   systemctl daemon-reload && systemctl enable --now pod-watchdog
[Unit]
Description=Destroy GPU pods that outlive their deadline
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/motion-clone
ExecStart=/usr/bin/python3 /opt/motion-clone/scripts/pod_watchdog.py
Restart=always
RestartSec=10
# Restart=always matters more than it looks: this process is the only thing
# standing between a crashed runner and an overnight $0.99/hour bill.

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Add the Makefile target**

Add to `Makefile` (next to the other gates):

```makefile
watchdog-dry: ## Report what the watchdog would destroy right now — destroys nothing
	@python3 scripts/pod_watchdog.py --once --dry-run
```

Add `watchdog-dry` to the `.PHONY` line at `Makefile:2`.

- [ ] **Step 5: Verify the target**

Run: `make watchdog-dry`
Expected: same output as Step 2, exit 0

- [ ] **Step 6: Commit**

```bash
git add scripts/pod_watchdog.py scripts/vps/pod-watchdog.service Makefile
git commit -m "Watchdog: daemon, systemd unit, and a dry-run gate

Separate process from the runner on purpose: a 'finally: destroy' does
not survive a hung or OOM-killed runner, which is the case it exists
for. Killing something already dead needs a second clock.

A failing tick logs and continues rather than exiting — this process is
the only thing between a crashed runner and an overnight bill."
```

---

### Task 5: `make drain` — the happy path

**Files:**
- Create: `scripts/drain.py`
- Modify: `scripts/batch_run.py` (one new exit code)
- Modify: `Makefile` (add `drain` target)
- Test: `scripts/tests/test_batch_drain.py`

**Interfaces:**
- Consumes: `load_manifest` and `state_path_for` from `batchlib.manifest`; `PIPELINES` and `STAGES` from `batchlib.pipelines`; `env_get` from `batchlib.config`; `Lease`, `write_lease`, `clear_lease` from Task 1.
- Produces: `EXIT_NEEDS_POD = 3` in `scripts/batch_run.py`, `abs_max_min(manifest) -> int` in `scripts/drain.py`, and the executable `python3 scripts/drain.py --file batch/x.yaml [--yes]`.

**The `abs_max_min` rule, from the spec:** the sum of the timeouts of every stage in the manifest, plus 30 minutes. For a large batch this converges on `POD_MAX_HOURS`; that is accepted, because tier 1 is the tight one and tier 2 still destroys where tier 0 only stops.

**`drain.py` must NOT re-wire the phases.** `batch_run.py:184-189` passes
`prepared=(out_dir, state, state_file)` from the local try-on phase into `run_batch`, and
`resolve_batch_id` (`batch_run.py:38`) handles three distinct resume branches. Reimplementing either
in `drain.py` reintroduces the "resume mints a new empty `out_dir`, re-runs finished stages, pays
GPU twice" bug that `resolve_batch_id`'s docstring documents as already having happened once.
`drain.py` therefore calls `batch_run.py` as a subprocess — twice, matching the two-scenario flow in
`docs/batch-runner.md` section 2.9: once with no pod (which does the local Gemini try-on and stops),
then again with `--resume` after the pod exists.

That flow needs one thing the current CLI cannot express: "I stopped because there is no pod, and
nothing is wrong" is currently `return 1`, indistinguishable from fail-fast and from a mid-batch
network loss. Renting a $0.99/hour pod on a misread of that is the failure this exit code prevents.

**Provisioning failure is already correct — do not "fix" it.** If `pod-provision.sh` finds no
RTX 5090, `check=True` raises and `main()` propagates. No lease was written (the lease is written
*after* provisioning returns), and the local try-on output is already journalled, so re-running
later skips it and does not bill Gemini twice. That is exactly the defer semantics the spec
specifies. The interactive stockout prompt with its four buttons is Plan 2's job; the correct
non-interactive behaviour is to fail without a lease and without spending, which is what this does.

- [ ] **Step 1: Write the failing test for the ceiling calculation**

```python
# scripts/tests/test_batch_drain.py
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import load_manifest
from drain import abs_max_min

YAML = """
runs:
  - id: a
    pipeline: motion-enhance
    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}
  - id: b
    pipeline: character-swap-enhance
    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}
"""


class TestAbsMax(unittest.TestCase):
    def test_sums_stage_timeouts_plus_30(self):
        # motion 60 + enhance 90 + character-swap 60 + enhance 90 = 300, +30
        path = Path(tempfile.mkdtemp()) / "m.yaml"
        path.write_text(YAML, encoding="utf-8")
        self.assertEqual(abs_max_min(load_manifest(path)), 330)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_drain.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'drain'`

- [ ] **Step 3: Give `batch_run.py` a distinct exit code for "no pod"**

In `scripts/batch_run.py`, add near the other module constants:

```python
# Distinct from 1 ("something went wrong") on purpose. scripts/drain.py rents a
# $0.99/hour pod when it sees this, so "I stopped because there is no pod, and
# nothing is wrong" must not be confusable with fail-fast or a dropped
# connection, both of which also return 1.
EXIT_NEEDS_POD = 3
```

Then change the no-pod branch at `scripts/batch_run.py:176-182` — the `return 1` that fires when
`preflight()` fails — to `return EXIT_NEEDS_POD`. Leave every other `return 1` alone.

- [ ] **Step 4: Verify the exit code by hand**

Use a manifest with **no try-on stage**. `batch_run.py` runs the local Gemini phase *before* the
preflight branch, so verifying this against a `tryon-*` manifest would spend real Gemini calls just
to read an exit code.

```bash
cat > /tmp/exitcode.yaml <<'YAML'
runs:
  - id: probe
    pipeline: motion-enhance
    inputs:
      character: ~/Desktop/materials/characters/c1.jpeg
      driver:    ~/Desktop/materials/drivers/m1.mp4
YAML
python3 scripts/batch_run.py --file /tmp/exitcode.yaml --no-start; echo "exit=$?"
```

Expected: it reports the pod is unreachable and prints `exit=3`. `exit=1` means the wrong `return 1`
was changed — there are three others in that function.

(Adjust the two input paths to files that exist; validation rejects missing ones before anything
else happens, which is the point of that gate.)

- [ ] **Step 5: Write `scripts/drain.py`**

```python
#!/usr/bin/env python3
"""Rent a pod, run a batch on it, destroy it. The unattended entry point.

    python3 scripts/drain.py --file batch/2026-08-30.yaml --yes

`make batch` assumes a pod already exists and never rents one. This does the
whole cycle, and writes a lease so scripts/pod_watchdog.py can clean up if
this process dies partway.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batch_run import EXIT_NEEDS_POD
from batchlib.config import env_get, load_settings
from batchlib.manifest import Manifest, load_manifest, load_state, state_path_for
from batchlib.pipelines import PIPELINES, STAGES
from batchlib_ext.lease import Lease, clear_lease, write_lease

ROOT = Path(__file__).resolve().parents[1]
LEASE_PATH = ROOT / "batch" / "pod-lease.json"
CEILING_SLACK_MIN = 30


def abs_max_min(manifest: Manifest) -> int:
    """Tier-2 ceiling: every stage's timeout in this manifest, plus slack."""
    total = sum(STAGES[stage].timeout_min
                for run in manifest.runs
                for stage in PIPELINES[run.pipeline])
    return total + CEILING_SLACK_MIN


def sh(*argv: str) -> None:
    subprocess.run(argv, check=True, cwd=ROOT)


def provision_and_wait(*, ceiling_min: int) -> str:
    """Rent a pod, bootstrap it, return its instance id.

    POD_MAX_HOURS is passed rather than left at its default of 8: an
    unattended drain knows its own ceiling, so the RunPod-side --stop-after
    net (pod-provision.sh:66) should be tightened to match instead of always
    granting 8 hours. pod-provision.sh already reads it from the environment.
    """
    hours = max(1, -(-ceiling_min // 60))   # ceil
    env_prefix = f"POD_MAX_HOURS={hours} CONFIRM=yes"
    subprocess.run(f"{env_prefix} bash scripts/pod-provision.sh",
                   shell=True, check=True, cwd=ROOT)
    sh("bash", "scripts/pod-wait.sh")
    sh("bash", "scripts/pod-bootstrap.sh")
    return env_get(ROOT / ".env", "GPU_INSTANCE_ID")


def batch_run(*args: str) -> int:
    """Run the existing CLI. Never reimplement what it wires together."""
    return subprocess.run([sys.executable, "scripts/batch_run.py", *args],
                          cwd=ROOT).returncode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--yes", action="store_true",
                    help="required to actually rent — without it, dry run")
    args = ap.parse_args()

    manifest_path = Path(args.file)
    manifest = load_manifest(manifest_path)
    ceiling = abs_max_min(manifest)

    if not args.yes:
        print(f"DRY RUN. {len(manifest.runs)} runs, tier-2 ceiling {ceiling} min.")
        print("Re-run with --yes to rent a pod.")
        return 0

    # Phase A. --no-start so it cannot quietly resume a stopped pod behind our
    # back: renting is this script's job, and it must be the only one doing it.
    # Local Gemini try-on happens here, before any GPU clock starts, so a 429
    # costs nothing. See docs/batch-runner.md section 2.9.
    rc = batch_run("--file", str(manifest_path), "--no-start")
    if rc == 0:
        print("finished without needing a pod")
        return 0
    if rc != EXIT_NEEDS_POD:
        print(f"local phase failed (exit {rc}) — NOT renting a pod", file=sys.stderr)
        return rc

    pod_id = provision_and_wait(ceiling_min=ceiling)
    write_lease(LEASE_PATH, Lease(pod_id=pod_id, provisioned_at=time.time(),
                                  manifest=str(manifest_path),
                                  abs_max_min=ceiling))
    try:
        # --resume, always: phase A already journalled the try-on stages, and
        # resume is what makes them skipped rather than paid for twice.
        rc = batch_run("--file", str(manifest_path), "--resume")
    finally:
        # Best effort only. The watchdog is the guarantee, not this block:
        # `finally` does not run when the process is SIGKILLed or the VPS dies.
        sh("make", "gpu-destroy")
        clear_lease(LEASE_PATH)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_drain.py' -v`
Expected: PASS

- [ ] **Step 7: Verify the dry run spends nothing**

Run: `python3 scripts/drain.py --file batch/2026-08-28-lanczos-6cap.yaml`
Expected: prints `DRY RUN. 6 runs, tier-2 ceiling <N> min.` and exits 0 **without renting anything**

- [ ] **Step 8: Add the Makefile target**

```makefile
drain: ## Rent a pod, run FILE, destroy it (dry run unless CONFIRM=yes)
	@test -n "$(FILE)" || { echo "usage: make drain FILE=batch/….yaml [CONFIRM=yes] [RESUME=1]"; exit 1; }
	@python3 scripts/drain.py --file "$(FILE)" \
		$(if $(filter yes,$(CONFIRM)),--yes) $(if $(RESUME),--resume)
```

Add `drain` to `.PHONY` at `Makefile:2`.

- [ ] **Step 9: Verify the target defaults to dry run**

Run: `make drain FILE=batch/2026-08-28-lanczos-6cap.yaml`
Expected: the same DRY RUN line. Renting requires `CONFIRM=yes`, matching `pod-provision.sh`.

- [ ] **Step 10: Commit**

```bash
git add scripts/drain.py scripts/tests/test_batch_drain.py Makefile
git commit -m "drain: rent, run, destroy in one unattended command

make batch assumes a pod exists; this does the whole cycle and writes a
lease so the watchdog can finish the job if this process dies partway.
The finally: destroy here is best effort only — the watchdog is the
guarantee, because finally does not run when the process is killed.

POD_MAX_HOURS is tightened per-drain instead of always granting 8
hours: an unattended run knows its own ceiling, so the RunPod-side
--stop-after net should match it."
```

---

### Task 6: Pull diagnostics before destroying

**Files:**
- Modify: `scripts/drain.py` (the `finally` block)
- Test: `scripts/tests/test_batch_drain.py` (add a case)

**Interfaces:**
- Consumes: `BatchResult` from `batchlib.runner`.
- Produces: `failed_job_ids(state: dict) -> list[tuple[str, str]]` returning `(run_id, job_id)` pairs.

**Why:** `docs/batch-runner.md` section 4 says plainly that a failed batch is exactly when you must not destroy the pod, because the worker log is only readable while it lives. Auto-destroy contradicts that, so the diagnostics have to be pulled first. `GET /jobs/:id/logs` matters specifically: `face_crop=vitpose` versus the DWPose fallback is written with `api_log` (`linux.py:3962`) and is therefore **not** in `~/.pm2/logs/worker-out.log`. That exact trap has already cost one debugging session.

- [ ] **Step 1: Write the failing test**

```python
# add to scripts/tests/test_batch_drain.py
from drain import failed_job_ids


class TestFailedJobIds(unittest.TestCase):
    def test_collects_job_ids_from_error_stages(self):
        state = {"runs": {
            "r1": {"status": "error", "stages": {
                "motion": {"status": "done", "job_id": "j1"},
                "enhance": {"status": "error", "job_id": "j2"}}},
            "r2": {"status": "done", "stages": {
                "motion": {"status": "done", "job_id": "j3"}}},
        }}
        self.assertEqual(failed_job_ids(state), [("r1", "j2")])

    def test_error_stage_without_a_job_id_is_skipped(self):
        # A run can fail before a job was ever submitted — there is nothing
        # to fetch logs for, and inventing an id would 404 noisily.
        state = {"runs": {"r1": {"status": "error",
                                 "stages": {"motion": {"status": "error"}}}}}
        self.assertEqual(failed_job_ids(state), [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_drain.py' -v`
Expected: FAIL with `ImportError: cannot import name 'failed_job_ids'`

- [ ] **Step 3: Implement**

Add to `scripts/drain.py`:

```python
def failed_job_ids(state: dict) -> list[tuple[str, str]]:
    """(run_id, job_id) for every stage that errored with a job actually sent."""
    out = []
    for run_id, entry in (state.get("runs") or {}).items():
        for stage in (entry.get("stages") or {}).values():
            if stage.get("status") == "error" and stage.get("job_id"):
                out.append((run_id, str(stage["job_id"])))
    return out


def collect_diagnostics(settings, state: dict, out_dir: Path) -> None:
    """Pull the pod-side logs BEFORE the pod dies.

    docs/batch-runner.md section 4: a failed batch is exactly when the pod is
    needed, to read the worker log. Auto-destroy would otherwise throw that
    away. This is strictly better than the manual flow, which relies on
    remembering to do it before typing `make gpu-destroy`.
    """
    # _request is private to batchlib.client, but it is the only thing that
    # knows the x-api-key and user-agent headers the API requires, and
    # duplicating those here would be a second place to keep in sync. It
    # returns (status_code, body_bytes) — client.py:93.
    from batchlib.client import _request
    for run_id, job_id in failed_job_ids(state):
        dest = out_dir / "runs" / run_id / "pod-job.log"
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            # GET /jobs/:id/logs, not pm2: face_crop=vitpose vs the DWPose
            # fallback is written with api_log (linux.py:3962) and never
            # reaches ~/.pm2/logs/worker-out.log.
            code, body = _request(settings, f"/jobs/{job_id}/logs")
            dest.write_bytes(body if code == 200
                             else f"HTTP {code}\n".encode() + body)
        except Exception as exc:
            dest.write_text(f"could not fetch job logs: {exc!r}\n", encoding="utf-8")

    worker = out_dir / "pod-worker.log"
    try:
        tail = subprocess.run(["make", "gpu-logs", "LOG=worker"], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)
        worker.write_text(tail.stdout + tail.stderr, encoding="utf-8")
    except Exception as exc:
        worker.write_text(f"could not fetch worker log: {exc!r}\n", encoding="utf-8")
```

Then change the `finally` block in `main()` to:

```python
    finally:
        state = load_state(state_path_for(manifest_path))
        batch_id = state.get("batch") or ""
        if batch_id and failed_job_ids(state):
            # load_settings() here rather than at the top of main(): on a fresh
            # VPS, NUXT_MOTION_API_KEY does not exist in motions/.env until
            # gpu-bootstrap has written it, so calling it before provisioning
            # would raise ConfigError on the very first drain.
            collect_diagnostics(load_settings(), state, ROOT / "out" / batch_id)
        sh("make", "gpu-destroy")
        clear_lease(LEASE_PATH)
```

Note the ordering: diagnostics are collected **inside** `finally`, **before** `gpu-destroy`. Moving
`gpu-destroy` earlier would make this task pointless — the logs only exist while the pod lives.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_*.py' -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add scripts/drain.py scripts/tests/test_batch_drain.py
git commit -m "drain: pull pod logs before destroying on failure

docs/batch-runner.md section 4 says a failed batch is exactly when you
must not destroy the pod. Auto-destroy contradicts that, so collect
first. GET /jobs/:id/logs specifically: face_crop=vitpose vs the DWPose
fallback is written with api_log (linux.py:3962) and never reaches
~/.pm2/logs/worker-out.log — a trap that already cost one session."
```

---

### Task 7: Pod ownership guard and VPS install docs

**Files:**
- Modify: `scripts/gpu-preflight.sh`
- Create: `scripts/vps/README.md`

**Interfaces:**
- Consumes: `batch/pod-lease.json` written by Task 5.
- Produces: nothing importable — this is a guard and a document.

**Why:** the spec's ownership decision. Today the Mac's `.env` is the only place that tracks a pod. Adding the VPS creates two machines that each believe they own it — double provisioning, or the Mac running `gpu-destroy` mid-batch. This is the same class of drift that made `make check-job-types` necessary. Tier 3 catches the collision eventually, but only after money is spent.

- [ ] **Step 1: Add the guard to `scripts/gpu-preflight.sh`**

Append before the final summary block:

```bash
# Pod ownership. Since 2026-08-30 the VPS is the sole owner of pod lifecycle
# (see docs/superpowers/specs/2026-08-30-telegram-batch-control-design.md).
# Two machines both provisioning is the same drift class check-job-types.mjs
# exists to stop — and here it costs $0.99/hour, not a confusing error.
if [ -f batch/pod-lease.json ]; then
  LEASE_POD="$(python3 -c 'import json,sys; print(json.load(open("batch/pod-lease.json"))["pod_id"])' 2>/dev/null || echo '?')"
  warn "A pod lease is held: $LEASE_POD"
  warn "  The VPS owns pod lifecycle. Do NOT run gpu-provision or gpu-destroy here"
  warn "  until 'make watchdog-dry' on the VPS reports no lease."
fi
```

- [ ] **Step 2: Verify preflight still passes and prints the warning**

Run: `make gpu-preflight`
Expected: normal output, no lease warning (no `batch/pod-lease.json` exists yet)

Run: `printf '{"pod_id":"test123","provisioned_at":0,"manifest":"x.yaml","abs_max_min":240}' > batch/pod-lease.json && make gpu-preflight; rm batch/pod-lease.json`
Expected: the warning names `test123`

- [ ] **Step 3: Write `scripts/vps/README.md`**

````markdown
# VPS setup

The VPS exists for one reason: something has to be awake to rent a pod and to guarantee it dies.
The Mac is a laptop that sleeps and travels, so it cannot be that something.

## Box

Hetzner CX22 or equivalent — 2 vCPU / 4 GB / 40 GB, ~EUR 4/month. Cheaper than any RunPod CPU pod,
and not billed by the hour. 40 GB is enough for material plus `out/`; keep it that way with
`make batch-clean KEEP=3`.

## Install

```bash
apt update && apt install -y python3 git rsync ffmpeg make
# runpodctl: see https://docs.runpod.io/cli/install
git clone <this repo> /opt/motion-clone && cd /opt/motion-clone
```

## Secrets — copy, never commit

`.env` and `motions/.env` are gitignored and must be copied from the Mac by hand:

```bash
scp .env       vps:/opt/motion-clone/.env
scp motions/.env vps:/opt/motion-clone/motions/.env
```

The VPS needs `GEMINI_API_KEY` (local try-on), `RUNPOD_API_KEY`, `DOMAIN`, `POD_VOLUME_ID`, and the
SSH private key `pod-bootstrap.sh` uses to reach the pod. Nothing here is ever committed — the repo
is public and `motions-studio/setup/scrub-secrets.sh --check` gates every commit.

## First run must be manual

`load_settings()` needs `NUXT_MOTION_API_KEY` in `motions/.env`, and that value is written by
`gpu-bootstrap` when it configures a pod. On a brand-new VPS it does not exist yet, so
`drain.py` would fail before renting anything. Do one manual cycle first:

```bash
make gpu-preflight
CONFIRM=yes bash scripts/pod-provision.sh && make gpu-wait && make gpu-bootstrap
make gpu-destroy
```

After that, `motions/.env` has the key and drains are unattended.

## Watchdog

```bash
cp scripts/vps/pod-watchdog.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now pod-watchdog
systemctl status pod-watchdog          # must be active (running)
make watchdog-dry                      # must report no lease and destroy nothing
journalctl -u pod-watchdog -f          # watch it tick
```

## Pod ownership — the rule that matters

**From this point the VPS is the sole owner of pod lifecycle.** Do not run `gpu-provision`,
`gpu-up` or `gpu-destroy` on the Mac. Two machines each tracking a pod in their own `.env` means
double provisioning, or the Mac destroying a pod mid-batch — at $0.99/hour. `make gpu-preflight`
on the Mac warns when the VPS holds a lease; tier-3 reconciliation catches the collision, but only
after the money is spent.

The Mac keeps `make batch` for debugging against a pod the VPS already rented. That is read-only
with respect to ownership and is fine.

## Running a batch

```bash
make drain FILE=batch/2026-08-30.yaml               # dry run: prints the plan, rents nothing
make drain FILE=batch/2026-08-30.yaml CONFIRM=yes   # rents, runs, destroys
```
````

- [ ] **Step 4: Add `batch/pod-lease.json` to `.gitignore`**

It is machine state about a rented pod, in the same category as `batch/*.state.json`. Verify the pattern actually matches:

Run: `printf '{}' > batch/pod-lease.json && git status --short batch/ && rm batch/pod-lease.json`
Expected: no output (ignored)

- [ ] **Step 5: Run every free gate**

```bash
make batch-test
make check-job-types
make check-comfy-nodes
make check-batch-params
motions-studio/setup/scrub-secrets.sh --check
git status --short scripts/batchlib/    # MUST be empty
```
Expected: all green, exit 0, and `scripts/batchlib/` untouched

- [ ] **Step 6: Commit**

```bash
git add scripts/gpu-preflight.sh scripts/vps/README.md .gitignore
git commit -m "Pod ownership: the VPS owns lifecycle, preflight warns the Mac

Two machines each tracking a pod in their own .env is the drift class
check-job-types.mjs exists to stop, except here it costs \$0.99/hour.
Tier-3 reconciliation catches the collision, but only after the money
is spent; warning beforehand is cheaper."
```

---

## Acceptance — the first real drain

These cannot be unit-tested; they cost GPU time and must be run once, deliberately, on the VPS.

- [ ] **A1.** `make watchdog-dry` reports no lease and destroys nothing.
- [ ] **A2.** `make drain FILE=<a one-run manifest> CONFIRM=yes` completes: pod rented, batch run, pod destroyed, `batch/pod-lease.json` gone.
- [ ] **A3.** **Measure `gpu-bootstrap` duration** with models already on the Network Volume. Spec section 13 lists this as unknown; it is added to every drain and sets the floor for the stockout economics table. Record the number and the date in `docs/gpu-pod.md`.
- [ ] **A4.** Kill `drain.py` with `SIGKILL` mid-batch (not Ctrl-C — `finally` must not get a chance to run). Confirm the watchdog destroys the pod on its own, and that the log names the tier that fired.
- [ ] **A5.** Confirm the real cost with `runpodctl billing pods`, not `currentSpendPerHr`.

A4 is the one that matters. Everything else in this plan is scaffolding around the claim that a crashed runner cannot leave a pod billing overnight — and that claim is only proven by killing the runner.

---

## Notes for Plan 2

Plan 2 (the Telegram bot) consumes from this plan: `python3 scripts/drain.py --file … --yes` as the drain entry point, `batch/pod-lease.json` as the source of truth for "is a pod alive", and `batchlib_ext.watchdog.decide` for the deadline shown in the progress message.

Plan 2 is blocked on spec section 13's first unknown — whether Telegram preserves bytes for a `.MP4` sent as File, verified by `sha256` at both ends from the user's actual iPhone. That measurement needs the user's phone and cannot be done here. It does not block this plan.
