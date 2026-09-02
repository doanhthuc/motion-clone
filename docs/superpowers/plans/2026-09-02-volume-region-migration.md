# Cross-Datacenter Volume Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate the manual EU-CZ-1 (or any other datacenter) Network Volume migration
runbook — copy the volume, verify byte-for-byte, swap `.env`, delete the original — trigger it
from the Telegram bot's Run-confirm GPU picker, and report progress the same way a batch does.

**Architecture:** One new generic, bot-independent script (`scripts/volume_migrate.py`, same
layering as `drain.py`) does all six phases against the RunPod API/CLI directly. A new small
lease file (`batch/volume-migrate-lease.json`, its own dataclass — the existing `Lease` shape has
one pod id, this needs two) lets `pod_watchdog.py` clean up the two temporary CPU pods if the
script dies mid-run, mirroring the existing GPU-pod lease/watchdog pattern exactly. The bot polls
a progress file the same way `tick_progress` already does for a drain.

**Tech Stack:** Python 3 stdlib only (`subprocess`, `argparse`, `json`, `tempfile`) — no new
dependency. `runpodctl` CLI for volume/SSH-info operations, raw `curl`/REST for CPU pod creation
(mirrors `pod-provision.sh`'s own CPU branch, which already established that `runpodctl pod
create` cannot set `cpuFlavorIds`/`vcpuCount`).

**Spec:** `docs/superpowers/specs/2026-09-02-volume-region-migration-design.md`

## Global Constraints

- Temp pods get 4 vCPU, not 2 — `docs/gpu-pod.md#volume-migrate` measured `rsync` CPU-bound at
  ~94MB/s with 2 vCPU / 4 threads; more vCPU before more threads is the doc's own recommendation.
- `rsync`, never `dd` — chosen for resume + built-in checksum verify (spec §1/§4), even though
  `dd|ssh cat` was the faster MEASURED number in the doc. Do not "optimize" this back to `dd`.
- Never claim a specific throughput number for this script's own transfers — the spec explicitly
  flags the ~427MB/s figure as measured for `dd`, not `rsync` at 8 threads. Only report what a run
  actually measures.
- The OLD volume is the source of truth until `verify()` reports exactly 0 pending changes.
  Nothing may delete it before that gate passes.
- The two temp pods are torn down in EVERY exit path — success, verify failure, or any exception —
  same discipline as `drain.py::teardown` and this session's own `chain_or_teardown`.
- Every new pod name this script creates (`migrate-tmp-a`, `migrate-tmp-b`) MUST be added to
  `scripts/batchlib_ext/watchdog.py`'s destroyable-names bookkeeping, or the watchdog cannot ever
  clean one up after a crash — this is the exact hand-copied-list trap `DESTROYABLE_NAMES`'s own
  docstring already warns about.
- Cross-datacenter automation only ever moves the Network Volume + `.env`'s `POD_VOLUME_ID`. It
  never renames `.env`'s `GPU_INSTANCE_ID`/`GPU_SSH_HOST`/`GPU_SSH_PORT` — those name the real,
  currently-rented GPU pod, a completely different resource from the two temporary CPU pods this
  script rents and destroys itself.

---

## Task 1: `MigrateLease` — the temp-pod lease file

**Files:**
- Create: `scripts/batchlib_ext/migrate_lease.py`
- Test: `scripts/tests/test_batch_migrate_lease.py`

**Interfaces:**
- Produces: `MigrateLease(pod_a_id: str, pod_b_id: str, started_at: float, to_dc: str)`,
  `write_migrate_lease(path: Path, lease: MigrateLease) -> None`,
  `read_migrate_lease(path: Path) -> MigrateLease | None`,
  `clear_migrate_lease(path: Path) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_batch_migrate_lease.py
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.migrate_lease import (MigrateLease, clear_migrate_lease,
                                        read_migrate_lease, write_migrate_lease)


class TestMigrateLease(unittest.TestCase):
    def test_round_trips_every_field(self):
        path = Path(tempfile.mkdtemp()) / "lease.json"
        lease = MigrateLease(pod_a_id="pod-a", pod_b_id="pod-b",
                             started_at=1000.5, to_dc="EU-CZ-1")
        write_migrate_lease(path, lease)
        self.assertEqual(read_migrate_lease(path), lease)

    def test_missing_file_returns_none_rather_than_raising(self):
        self.assertIsNone(read_migrate_lease(Path(tempfile.mkdtemp()) / "nope.json"))

    def test_malformed_json_returns_none_rather_than_raising(self):
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text("not json", encoding="utf-8")
        self.assertIsNone(read_migrate_lease(path))

    def test_missing_field_returns_none_rather_than_raising(self):
        path = Path(tempfile.mkdtemp()) / "bad.json"
        path.write_text('{"pod_a_id": "a"}', encoding="utf-8")
        self.assertIsNone(read_migrate_lease(path))

    def test_write_is_atomic_no_leftover_tmp(self):
        path = Path(tempfile.mkdtemp()) / "lease.json"
        write_migrate_lease(path, MigrateLease(pod_a_id="a", pod_b_id="b",
                                               started_at=1.0, to_dc="EU-CZ-1"))
        self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_clear_removes_the_file_and_tolerates_it_missing(self):
        path = Path(tempfile.mkdtemp()) / "lease.json"
        write_migrate_lease(path, MigrateLease(pod_a_id="a", pod_b_id="b",
                                               started_at=1.0, to_dc="EU-CZ-1"))
        clear_migrate_lease(path)
        self.assertFalse(path.exists())
        clear_migrate_lease(path)   # must not raise the second time


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails on import**

Run: `python3 -m unittest scripts.tests.test_batch_migrate_lease -v`
Expected: `ModuleNotFoundError: No module named 'batchlib_ext.migrate_lease'`

- [ ] **Step 3: Implement `scripts/batchlib_ext/migrate_lease.py`**

```python
"""Who currently owns the two TEMPORARY CPU pods a volume migration rents —
distinct from batchlib_ext.lease.Lease, which names the one real GPU pod.
That shape has a single pod_id; a migration needs two, so this is its own
small dataclass rather than overloading the GPU pod's contract.

Storage only — no policy. The kill decision lives in watchdog.py, same
split as the GPU pod's own lease.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrateLease:
    pod_a_id: str      # temp pod mounting the SOURCE volume
    pod_b_id: str      # temp pod mounting the DESTINATION volume
    started_at: float  # unix seconds, set once when both pods are confirmed provisioned
    to_dc: str         # destination datacenter id


def write_migrate_lease(path: Path, lease: MigrateLease) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(lease), indent=2), encoding="utf-8")
    tmp.replace(path)


def read_migrate_lease(path: Path) -> MigrateLease | None:
    """None means 'no lease' — including an unreadable file. Tier 3
    reconciliation then treats the two temp pods as ordinary orphans, which
    is the safe direction to fail in (same reasoning as
    batchlib_ext.lease.read_lease)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return MigrateLease(pod_a_id=str(raw["pod_a_id"]),
                            pod_b_id=str(raw["pod_b_id"]),
                            started_at=float(raw["started_at"]),
                            to_dc=str(raw["to_dc"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_migrate_lease(path: Path) -> None:
    path.unlink(missing_ok=True)
```

- [ ] **Step 4: Run the tests again**

Run: `python3 -m unittest scripts.tests.test_batch_migrate_lease -v`
Expected: all 6 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib_ext/migrate_lease.py scripts/tests/test_batch_migrate_lease.py
git commit -m "feat(migrate): add MigrateLease for the two temp CPU pods"
```

---

## Task 2: Watchdog tiers for the migration's temp pods

**Files:**
- Modify: `scripts/batchlib_ext/watchdog.py`
- Test: `scripts/tests/test_batch_watchdog.py`

**Interfaces:**
- Consumes: `MigrateLease` from Task 1 (`scripts/batchlib_ext/migrate_lease.py`).
- Produces: `MIGRATE_DESTROYABLE_NAMES: frozenset[str]`, `MIGRATE_CEILING_MIN: int`,
  `decide_migration(*, lease: MigrateLease, now: float) -> Verdict`,
  `reconcile_migration(*, pods: list[PodInfo], lease: MigrateLease | None, first_seen: dict[str, float], now: float, grace_min: int = GRACE_MIN) -> tuple[list[str], dict[str, float]]`.

- [ ] **Step 1: Write the failing tests**

Read the existing `scripts/tests/test_batch_watchdog.py` first to match its exact fixture style
(it already builds `PodInfo` and calls `decide`/`reconcile` — mirror those patterns, don't
reinvent). Add:

```python
# appended to scripts/tests/test_batch_watchdog.py
from batchlib_ext.migrate_lease import MigrateLease
from batchlib_ext.watchdog import (MIGRATE_CEILING_MIN, decide_migration,
                                   reconcile_migration)


class TestDecideMigration(unittest.TestCase):
    def test_under_ceiling_is_not_killed(self):
        lease = MigrateLease(pod_a_id="a", pod_b_id="b", started_at=1000.0, to_dc="EU-CZ-1")
        verdict = decide_migration(lease=lease, now=1000.0 + 10 * 60)
        self.assertFalse(verdict.kill)

    def test_over_ceiling_is_killed(self):
        lease = MigrateLease(pod_a_id="a", pod_b_id="b", started_at=1000.0, to_dc="EU-CZ-1")
        verdict = decide_migration(
            lease=lease, now=1000.0 + (MIGRATE_CEILING_MIN + 1) * 60)
        self.assertTrue(verdict.kill)
        self.assertIn("ceiling", verdict.reason)


class TestReconcileMigration(unittest.TestCase):
    def test_leased_pods_are_left_alone(self):
        pods = [PodInfo(pod_id="a", name="migrate-tmp-a"),
               PodInfo(pod_id="b", name="migrate-tmp-b")]
        lease = MigrateLease(pod_a_id="a", pod_b_id="b", started_at=1.0, to_dc="EU-CZ-1")
        kill, _ = reconcile_migration(pods=pods, lease=lease, first_seen={}, now=1000.0)
        self.assertEqual(kill, [])

    def test_an_unleased_temp_pod_past_grace_is_killed(self):
        pods = [PodInfo(pod_id="orphan-a", name="migrate-tmp-a")]
        kill, _ = reconcile_migration(
            pods=pods, lease=None, first_seen={"orphan-a": 0.0}, now=999999.0)
        self.assertEqual(kill, ["orphan-a"])

    def test_a_pod_with_an_unrelated_name_is_never_touched(self):
        # Same authority scoping as tier 3's own reconcile() for the real GPU
        # pod — this net only ever reaches pods this script itself creates.
        pods = [PodInfo(pod_id="someone-elses-box", name="not-ours")]
        kill, _ = reconcile_migration(
            pods=pods, lease=None, first_seen={"someone-elses-box": 0.0}, now=999999.0)
        self.assertEqual(kill, [])
```

Check the existing file's `PodInfo` import/construction and use the SAME import line and
constructor shape already present at the top of `scripts/tests/test_batch_watchdog.py`.

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 -m unittest scripts.tests.test_batch_watchdog -v`
Expected: `ImportError: cannot import name 'decide_migration'`

- [ ] **Step 3: Implement in `scripts/batchlib_ext/watchdog.py`**

Add near the top, right after the existing `DESTROYABLE_NAMES`/`GRACE_MIN` block:

```python
from .migrate_lease import MigrateLease

# 2026-09-02: this repo used to stand up these two pods BY HAND for the
# EU-CZ-1 failover runbook, which is exactly why the DESTROYABLE_NAMES
# docstring above says a CPU box "is not ours to kill" — now that
# scripts/volume_migrate.py automates that runbook, its own two pod names
# need the SAME tier-3 authority the real GPU pod already has, or a crashed
# migration bills forever with nothing watching it.
MIGRATE_DESTROYABLE_NAMES = frozenset({"migrate-tmp-a", "migrate-tmp-b"})

# Spec estimate is 15-25 min end-to-end (docs/superpowers/specs/2026-09-02-
# volume-region-migration-design.md §4) — doubled for margin, same
# reasoning as GRACE_MIN's own margin over the provisioning window it covers.
MIGRATE_CEILING_MIN = 40


def decide_migration(*, lease: MigrateLease, now: float) -> Verdict:
    """Tier-2-equivalent for a migration: there is no per-stage journal to
    dead-man's-switch against (a migration is six phases, not a stage
    pipeline), so age alone against a fixed ceiling is the whole check.
    """
    age_min = (now - lease.started_at) / 60.0
    if age_min > MIGRATE_CEILING_MIN:
        return Verdict(True, f"migration ceiling: alive {age_min:.0f} min "
                             f"> {MIGRATE_CEILING_MIN} min")
    return Verdict(False, "")


def reconcile_migration(*, pods: list[PodInfo], lease: MigrateLease | None,
                        first_seen: dict[str, float], now: float,
                        grace_min: int = GRACE_MIN) -> tuple[list[str], dict[str, float]]:
    """Tier-3-equivalent for the two temp pods — same shape as reconcile(),
    kept separate rather than generalizing reconcile() itself: that function
    is already tested against the real GPU pod's single-pod-id Lease, and a
    lease shape change there is exactly the kind of edit that should not be
    able to accidentally affect this migration's authority scope, or vice
    versa.
    """
    leased = {lease.pod_a_id, lease.pod_b_id} if lease else set()
    seen = {p.pod_id: first_seen.get(p.pod_id, now) for p in pods}
    kill = [p.pod_id for p in pods
            if p.pod_id not in leased and p.name in MIGRATE_DESTROYABLE_NAMES
            and (now - seen[p.pod_id]) / 60.0 > grace_min]
    return kill, seen
```

Also update the STALE comment on the existing `DESTROYABLE_NAMES` (it currently says the two
EU-CZ-1 temp pods are deliberately NOT tier 3's authority — that sentence describes the pre-
automation state and is now wrong):

```python
# Tier 3 destroys things, so its authority is scoped to the names this repo's
# own provisioning creates. Anything else in the account — a CPU box someone
# stood up by hand for an unrelated reason — is not ours to kill. The two
# temporary pods a volume migration rents (scripts/volume_migrate.py) have
# their OWN names and their own authority list, MIGRATE_DESTROYABLE_NAMES
# below — kept separate rather than merged into this one, so a lease-shape
# change for one can never silently change the other's scope.
```

- [ ] **Step 4: Run the tests again**

Run: `python3 -m unittest scripts.tests.test_batch_watchdog -v`
Expected: all PASS, including every pre-existing test in the file (no regression)

- [ ] **Step 5: Commit**

```bash
git add scripts/batchlib_ext/watchdog.py scripts/tests/test_batch_watchdog.py
git commit -m "feat(migrate): give the temp-pod lease its own watchdog tiers"
```

---

## Task 3: Wire the migration tiers into the watchdog daemon

**Files:**
- Modify: `scripts/pod_watchdog.py`
- Test: `scripts/tests/test_batch_pod_watchdog.py` (create if it does not already exist — check
  first with `ls scripts/tests/ | grep pod_watchdog`; if `tick()` already has tests elsewhere,
  extend that file instead of creating a duplicate)

**Interfaces:**
- Consumes: `MigrateLease`, `read_migrate_lease`, `clear_migrate_lease` (Task 1);
  `decide_migration`, `reconcile_migration`, `MIGRATE_DESTROYABLE_NAMES` (Task 2).
- Produces: `MIGRATE_LEASE_PATH: Path` (module constant in `pod_watchdog.py`).

- [ ] **Step 1: Find the existing test coverage for `tick()`**

Run: `ls scripts/tests/ | grep -i watchdog` and open whichever file already exercises
`pod_watchdog.tick` (look for a `FakePods`/`PodInfo`-based fixture — follow its exact style for
the new tests, including however it fakes `RunpodCtl`).

- [ ] **Step 2: Write the failing test**

```python
# added to whichever file already tests pod_watchdog.tick — match its imports
def test_a_migration_past_ceiling_is_killed_and_lease_cleared(self):
    import pod_watchdog
    from batchlib_ext.migrate_lease import MigrateLease, write_migrate_lease
    tmp_lease_path = Path(tempfile.mkdtemp()) / "migrate-lease.json"
    write_migrate_lease(tmp_lease_path, MigrateLease(
        pod_a_id="tmp-a", pod_b_id="tmp-b", started_at=0.0, to_dc="EU-CZ-1"))
    fake_pods = FakePods(pods=[PodInfo(pod_id="tmp-a", name="migrate-tmp-a"),
                              PodInfo(pod_id="tmp-b", name="migrate-tmp-b")])
    with mock.patch.object(pod_watchdog, "MIGRATE_LEASE_PATH", tmp_lease_path), \
         mock.patch.object(pod_watchdog, "LEASE_PATH", Path(tempfile.mkdtemp()) / "no-gpu-lease.json"):
        pod_watchdog.tick(fake_pods, {}, now=100000.0, dry_run=False)
    self.assertIn("tmp-a", fake_pods.destroyed)
    self.assertIn("tmp-b", fake_pods.destroyed)
    self.assertFalse(tmp_lease_path.exists())
```

Adapt `FakePods`/`PodInfo` construction to whatever fixture helper the existing test file already
defines — do not invent a second one.

- [ ] **Step 3: Run it and confirm it fails**

Run: `python3 -m unittest <the test module> -v`
Expected: `AttributeError: module 'pod_watchdog' has no attribute 'MIGRATE_LEASE_PATH'`

- [ ] **Step 4: Implement in `scripts/pod_watchdog.py`**

```python
from batchlib_ext.migrate_lease import clear_migrate_lease, read_migrate_lease
from batchlib_ext.watchdog import (DESTROYABLE_NAMES, GRACE_MIN,
                                   MIGRATE_DESTROYABLE_NAMES, decide,
                                   decide_migration, reconcile,
                                   reconcile_migration)

MIGRATE_LEASE_PATH = ROOT / "batch" / "volume-migrate-lease.json"
```

Inside `tick()`, right after the existing tier-1/2 `try/except` block for the real GPU pod lease
(i.e. right before the tier-3 `try: pods = pods_api.list_pods()` line), add a parallel
tier-1/2-equivalent for the migration:

```python
    migrate_lease = read_migrate_lease(MIGRATE_LEASE_PATH)
    if migrate_lease is not None:
        verdict = decide_migration(lease=migrate_lease, now=now)
        if verdict.kill:
            log(f"KILL migration temp pods {migrate_lease.pod_a_id},"
                f"{migrate_lease.pod_b_id} — {verdict.reason}")
            if not dry_run:
                ok_a = destroy_verified(pods_api, migrate_lease.pod_a_id)
                ok_b = destroy_verified(pods_api, migrate_lease.pod_b_id)
                if ok_a and ok_b:
                    clear_migrate_lease(MIGRATE_LEASE_PATH)
                else:
                    log("DESTROY NOT CONFIRMED for one or both migration temp "
                        "pods — still billing, retrying next tick")
```

Then, inside the existing tier-3 block, right after `kill, seen = reconcile(...)`, merge in the
migration reconciliation against the SAME `pods` list already fetched:

```python
    kill, seen = reconcile(pods=pods, lease=lease, first_seen=first_seen, now=now)
    migrate_kill, _ = reconcile_migration(pods=pods, lease=migrate_lease,
                                          first_seen=first_seen, now=now)
    kill = kill + migrate_kill
```

And widen the final "leaving X alone" reporting loop's name check from just `DESTROYABLE_NAMES`
to include the migration names too:

```python
    for p in untouched:
        if p.name in DESTROYABLE_NAMES or p.name in MIGRATE_DESTROYABLE_NAMES:
```

- [ ] **Step 5: Run the tests again**

Run: `python3 -m unittest <the test module> -v`
Expected: all PASS, including every pre-existing `tick()` test (the real GPU pod's tier 1/2/3
behavior must be completely unchanged)

- [ ] **Step 6: Commit**

```bash
git add scripts/pod_watchdog.py <the test file>
git commit -m "feat(migrate): watchdog daemon also guards the temp migration pods"
```

---

## Task 4: `volume_migrate.py` — CLI skeleton, dry run, and volume creation

**Files:**
- Create: `scripts/volume_migrate.py`
- Test: `scripts/tests/test_batch_volume_migrate.py`

**Interfaces:**
- Consumes: `env_get`, `env_set` (`scripts/batchlib/config.py`); `MigrateLease`,
  `write_migrate_lease`, `clear_migrate_lease` (Task 1).
- Produces: `create_volume(source_volume_id: str, to_dc: str) -> tuple[str, int, str]` (new id,
  size_gb, source_dc); `write_progress(phase: str, **extra) -> None`; module constants `ROOT`,
  `ENV_PATH`, `LEASE_PATH`, `PROGRESS_PATH`.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_batch_volume_migrate.py
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import volume_migrate


class TestCreateVolume(unittest.TestCase):
    @mock.patch("volume_migrate.sh")
    def test_reads_size_and_dc_from_the_source_writes_a_new_volume(self, mock_sh):
        get_result = mock.Mock(stdout=json.dumps(
            {"size": 100, "dataCenterId": "EU-RO-1", "name": "motion-100"}))
        create_result = mock.Mock(stdout=json.dumps({"id": "vol-new"}))
        mock_sh.side_effect = [get_result, create_result]
        new_id, size_gb, source_dc = volume_migrate.create_volume("vol-old", "EU-CZ-1")
        self.assertEqual(new_id, "vol-new")
        self.assertEqual(size_gb, 100)
        self.assertEqual(source_dc, "EU-RO-1")
        create_call = mock_sh.call_args_list[1].args
        self.assertIn("--size", create_call)
        self.assertIn("100", create_call)
        self.assertIn("--data-center-id", create_call)
        self.assertIn("EU-CZ-1", create_call)


class TestWriteProgress(unittest.TestCase):
    def test_writes_phase_and_extra_fields(self):
        tmpdir = Path(tempfile.mkdtemp())
        with mock.patch.object(volume_migrate, "PROGRESS_PATH", tmpdir / "p.json"):
            volume_migrate.write_progress("sync", pod_a="a", pod_b="b")
            payload = json.loads((tmpdir / "p.json").read_text())
        self.assertEqual(payload["phase"], "sync")
        self.assertEqual(payload["pod_a"], "a")
        self.assertIn("at", payload)


class TestMainDryRun(unittest.TestCase):
    def test_no_yes_flag_prints_plan_and_touches_nothing_destructive(self):
        tmpdir = Path(tempfile.mkdtemp())
        env_path = tmpdir / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch.object(volume_migrate, "create_volume",
                               return_value=("vol-new", 100, "EU-RO-1")) as mock_create, \
             mock.patch.object(volume_migrate, "provision_temp_pod") as mock_provision:
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1"])
        self.assertEqual(rc, 0)
        mock_create.assert_called_once_with("vol-old", "EU-CZ-1")
        mock_provision.assert_not_called()

    def test_no_pod_volume_id_in_env_is_refused(self):
        tmpdir = Path(tempfile.mkdtemp())
        env_path = tmpdir / ".env"
        env_path.write_text("", encoding="utf-8")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path):
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1"])
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 -m unittest scripts.tests.test_batch_volume_migrate -v`
Expected: `ModuleNotFoundError: No module named 'volume_migrate'`

- [ ] **Step 3: Implement the skeleton in `scripts/volume_migrate.py`**

```python
#!/usr/bin/env python3
"""Copy a Network Volume to another datacenter, swap .env to it, delete the
original — the automated form of docs/gpu-pod.md's manual EU-CZ-1 runbook.

    python3 scripts/volume_migrate.py --to-dc EU-CZ-1              # dry run
    python3 scripts/volume_migrate.py --to-dc EU-CZ-1 --yes        # for real

See docs/superpowers/specs/2026-09-02-volume-region-migration-design.md.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.config import env_get, env_set
from batchlib_ext.migrate_lease import (MigrateLease, clear_migrate_lease,
                                        write_migrate_lease)

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
LEASE_PATH = ROOT / "batch" / "volume-migrate-lease.json"
PROGRESS_PATH = ROOT / "batch" / "volume-migrate.progress.json"


def sh(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=ROOT, check=True, capture_output=True, text=True)


def _rp_json(*argv: str) -> dict:
    out = sh("runpodctl", *argv, "-o", "json")
    return json.loads(out.stdout or "{}")


def write_progress(phase: str, **extra) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps({"phase": phase, "at": time.time(), **extra},
                                        indent=2), encoding="utf-8")


def create_volume(source_volume_id: str, to_dc: str) -> tuple[str, int, str]:
    """Create the destination volume, same size as the source (RunPod only
    allows growing a volume later, never shrinking — under-sizing here means
    a second migration). Returns (new_volume_id, size_gb, source_dc)."""
    source = _rp_json("network-volume", "get", source_volume_id)
    size_gb = int(source["size"])
    source_dc = source.get("dataCenterId") or source.get("datacenterId") or ""
    name = f"{source.get('name', 'motion')}-{to_dc.lower()}"
    created = _rp_json("network-volume", "create", "--name", name,
                       "--size", str(size_gb), "--data-center-id", to_dc)
    return created["id"], size_gb, source_dc


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to-dc", required=True)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    old_volume_id = env_get(ENV_PATH, "POD_VOLUME_ID")
    if not old_volume_id:
        print("✗ POD_VOLUME_ID not set in .env — nothing to migrate", file=sys.stderr)
        return 1

    new_volume_id, size_gb, source_dc = create_volume(old_volume_id, args.to_dc)
    if not args.yes:
        print(f"DRY RUN. Would migrate {size_gb}GB from POD_VOLUME_ID={old_volume_id} "
             f"({source_dc}) to {args.to_dc} (created {new_volume_id} already — "
             f"delete it by hand if you do not proceed: "
             f"runpodctl network-volume delete {new_volume_id}).")
        print("Re-run with --yes to actually copy and swap.")
        return 0

    # Phases 2-6 land in later tasks of this plan.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

Note the dry-run test's expectation: `create_volume` DOES run even in dry-run (so the printed
plan can quote a real size), but nothing after it does — this matches `pod-provision.sh`'s own
"print the exact command, rent nothing without `--yes`" convention.

- [ ] **Step 4: Run the tests again**

Run: `python3 -m unittest scripts.tests.test_batch_volume_migrate -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/volume_migrate.py scripts/tests/test_batch_volume_migrate.py
git commit -m "feat(migrate): volume_migrate.py CLI skeleton + dry run"
```

---

## Task 5: Temp CPU pod provisioning + SSH-ready polling

**Files:**
- Modify: `scripts/volume_migrate.py`
- Modify: `scripts/tests/test_batch_volume_migrate.py`

**Interfaces:**
- Consumes: `env_get` (already imported in Task 4).
- Produces: `provision_temp_pod(name: str, volume_id: str, dc: str, disk_gb: int) -> str` (pod
  id); `wait_for_ssh(pod_id: str, timeout_min: int = 10) -> tuple[str, int]` (host, port).

- [ ] **Step 1: Write the failing tests**

```python
class TestProvisionTempPod(unittest.TestCase):
    def test_posts_the_expected_rest_body_and_returns_the_pod_id(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("RUNPOD_API_KEY=rp_test123\n", encoding="utf-8")
        fake_curl = mock.Mock(stdout=json.dumps({"id": "pod-a"}), returncode=0)
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch("subprocess.run", return_value=fake_curl) as mock_run:
            pod_id = volume_migrate.provision_temp_pod(
                "migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        self.assertEqual(pod_id, "pod-a")
        body = json.loads(mock_run.call_args.args[0][-1])
        self.assertEqual(body["name"], "migrate-tmp-a")
        self.assertEqual(body["vcpuCount"], 4)
        self.assertEqual(body["networkVolumeId"], "vol-old")
        self.assertEqual(body["dataCenterIds"], ["EU-RO-1"])
        self.assertEqual(body["containerDiskInGb"], 120)

    def test_missing_api_key_raises_a_clear_error(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("", encoding="utf-8")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.provision_temp_pod("migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        self.assertIn("RUNPOD_API_KEY", str(cm.exception))

    def test_a_response_with_no_id_raises_rather_than_returning_none(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("RUNPOD_API_KEY=rp_test123\n", encoding="utf-8")
        fake_curl = mock.Mock(stdout=json.dumps({"error": "insufficient capacity"}),
                              returncode=0)
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch("subprocess.run", return_value=fake_curl):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.provision_temp_pod("migrate-tmp-a", "vol-old", "EU-RO-1", 120)
        self.assertIn("migrate-tmp-a", str(cm.exception))


class TestWaitForSsh(unittest.TestCase):
    def test_returns_host_and_port_once_ssh_info_has_both_and_a_probe_succeeds(self):
        ssh_info = mock.Mock(stdout=json.dumps({"host": "1.2.3.4", "port": 40001}))
        probe_ok = mock.Mock(returncode=0)
        with mock.patch.object(volume_migrate, "sh", return_value=ssh_info), \
             mock.patch("subprocess.run", return_value=probe_ok):
            host, port = volume_migrate.wait_for_ssh("pod-a", timeout_min=1)
        self.assertEqual((host, port), ("1.2.3.4", 40001))

    def test_gives_up_after_the_timeout_with_no_endpoint_ever_appearing(self):
        not_ready = mock.Mock(stdout=json.dumps({"error": "pod not ready"}))
        with mock.patch.object(volume_migrate, "sh", return_value=not_ready), \
             mock.patch("time.sleep"), \
             mock.patch("time.time", side_effect=[0, 0, 61, 61]):
            with self.assertRaises(RuntimeError) as cm:
                volume_migrate.wait_for_ssh("pod-a", timeout_min=1)
        self.assertIn("pod-a", str(cm.exception))
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 -m unittest scripts.tests.test_batch_volume_migrate.TestProvisionTempPod scripts.tests.test_batch_volume_migrate.TestWaitForSsh -v`
Expected: `AttributeError: module 'volume_migrate' has no attribute 'provision_temp_pod'`

- [ ] **Step 3: Implement in `scripts/volume_migrate.py`**

Add after `create_volume`:

```python
def _cpu_pod_body(name: str, volume_id: str, dc: str, disk_gb: int) -> dict:
    return {
        "name": name,
        "computeType": "CPU",
        "cpuFlavorIds": ["cpu5c"],
        "vcpuCount": 4,
        "imageName": "runpod/base:0.6.2-cpu",
        "containerDiskInGb": disk_gb,
        "ports": ["22/tcp"],
        "networkVolumeId": volume_id,
        "volumeMountPath": "/workspace",
        "dataCenterIds": [dc],
    }


def provision_temp_pod(name: str, volume_id: str, dc: str, disk_gb: int) -> str:
    """A temp CPU pod via REST — same reason pod-provision.sh's own CPU
    branch goes through REST instead of `runpodctl pod create`: that CLI has
    no flag for cpuFlavorIds/vcpuCount, only a fixed 2 vCPU/4GB default.
    Returns the pod id.
    """
    api_key = env_get(ENV_PATH, "RUNPOD_API_KEY")
    if not api_key:
        raise RuntimeError(
            "RUNPOD_API_KEY not set in .env — required for the REST pod-create call")
    body = _cpu_pod_body(name, volume_id, dc, disk_gb)
    result = subprocess.run(
        ["curl", "-sS", "-X", "POST", "https://rest.runpod.io/v1/pods",
         "-H", f"Authorization: Bearer {api_key}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(body)],
        capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    pod_id = data.get("id") or data.get("podId")
    if not pod_id:
        raise RuntimeError(f"pod create for {name!r} did not return an id: {result.stdout}")
    return pod_id


def wait_for_ssh(pod_id: str, timeout_min: int = 10) -> tuple[str, int]:
    """Poll `runpodctl ssh info` until it carries a host+port AND a real SSH
    handshake succeeds — status strings alone are not proof (pod-wait.sh's
    own docstring: RunPod reports RUNNING well before sshd is listening).
    Key names are read permissively, matching pod-wait.sh's probe(): this
    CLI's JSON shape has moved across releases.
    """
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        raw = sh("runpodctl", "ssh", "info", pod_id).stdout
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        host = (data.get("host") or data.get("hostname")
               or data.get("publicIp") or data.get("ip"))
        port = data.get("port") or data.get("sshPort") or data.get("publicPort")
        if host and port:
            probe = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "ConnectTimeout=5", "-p", str(port), f"root@{host}", "true"],
                capture_output=True)
            if probe.returncode == 0:
                return host, int(port)
        time.sleep(10)
    raise RuntimeError(f"pod {pod_id} did not accept SSH within {timeout_min} min")
```

`sh()` already appends `-o json` in its callers elsewhere in this file (via `_rp_json`), but
`wait_for_ssh` calls `sh("runpodctl", "ssh", "info", pod_id)` WITHOUT `-o json` appended by `sh`
itself — `sh` is a thin passthrough, so call it here as `sh("runpodctl", "ssh", "info", pod_id,
"-o", "json")` instead. Fix this in the implementation above (the argument list shown already has
it — double check the actual file matches before moving on).

- [ ] **Step 4: Run the tests again**

Run: `python3 -m unittest scripts.tests.test_batch_volume_migrate -v`
Expected: all PASS (both new classes and Task 4's existing ones)

- [ ] **Step 5: Commit**

```bash
git add scripts/volume_migrate.py scripts/tests/test_batch_volume_migrate.py
git commit -m "feat(migrate): provision temp CPU pods and wait for SSH"
```

---

## Task 6: SSH key exchange, sync, and verify

**Files:**
- Modify: `scripts/volume_migrate.py`
- Modify: `scripts/tests/test_batch_volume_migrate.py`

**Interfaces:**
- Produces: `make_temp_keypair() -> tuple[Path, Path]` (private, public);
  `install_key_on(host: str, port: int, pub_key_path: Path) -> None`;
  `place_key_on(host: str, port: int, priv_key_path: Path) -> None`;
  `existing_subdirs(host: str, port: int, mount: str = "/workspace") -> list[str]`;
  `sync(host_a: str, port_a: int, host_b: str, port_b: int, subdirs: list[str], mount: str = "/workspace") -> None`;
  `count_pending_changes(rsync_dry_run_output: str) -> int`;
  `verify(host_a: str, port_a: int, host_b: str, port_b: int, subdirs: list[str], mount: str = "/workspace") -> int`;
  module constant `MODEL_SUBDIRS: list[str]`, `MAX_RSYNC_THREADS: int`.

`count_pending_changes` is the single most safety-critical function in this whole feature (spec
§8) — it decides whether the original volume is safe to delete. Give it the most test coverage.

- [ ] **Step 1: Write the failing tests**

```python
class TestCountPendingChanges(unittest.TestCase):
    def test_a_clean_verify_with_only_the_directory_line_is_zero(self):
        # Real rsync -avnc output when nothing differs: -a always lists the
        # top-level directory itself even when its contents are identical.
        output = "./\n\nsent 123 bytes  received 45 bytes  336.00 bytes/sec\n" \
                "total size is 79000000000  speedup is 999999.00 (DRY RUN)\n"
        self.assertEqual(volume_migrate.count_pending_changes(output), 0)

    def test_one_changed_file_counts_as_one(self):
        output = "./\nmodel1.safetensors\n\nsent 123 bytes  received 45 bytes\n"
        self.assertEqual(volume_migrate.count_pending_changes(output), 1)

    def test_several_changed_files_and_a_nested_path_all_count(self):
        output = ("./\nmodel1.safetensors\nsubdir/\nsubdir/model2.gguf\n\n"
                  "sent 123 bytes  received 45 bytes\n")
        self.assertEqual(volume_migrate.count_pending_changes(output), 3)

    def test_completely_empty_output_is_zero_not_an_error(self):
        self.assertEqual(volume_migrate.count_pending_changes(""), 0)


class TestSyncAndVerify(unittest.TestCase):
    def test_sync_runs_one_ssh_per_subdir_from_pod_a(self):
        fake_proc = mock.Mock()
        fake_proc.wait.return_value = 0
        with mock.patch("subprocess.Popen", return_value=fake_proc) as mock_popen:
            volume_migrate.sync("host-a", 1001, "host-b", 1002, ["loras", "checkpoints"])
        self.assertEqual(mock_popen.call_count, 2)
        first_call_argv = mock_popen.call_args_list[0].args[0]
        self.assertIn("root@host-a", first_call_argv)

    def test_sync_raises_if_any_leg_exits_non_zero(self):
        fake_proc = mock.Mock()
        fake_proc.wait.return_value = 1
        with mock.patch("subprocess.Popen", return_value=fake_proc):
            with self.assertRaises(RuntimeError):
                volume_migrate.sync("host-a", 1001, "host-b", 1002, ["loras"])

    def test_verify_sums_pending_changes_across_every_subdir(self):
        clean = mock.Mock(stdout="./\n\nsent 1 bytes\n")
        dirty = mock.Mock(stdout="./\nfile.gguf\n\nsent 1 bytes\n")
        with mock.patch("subprocess.run", side_effect=[clean, dirty]):
            total = volume_migrate.verify("host-a", 1001, "host-b", 1002,
                                          ["loras", "checkpoints"])
        self.assertEqual(total, 1)


class TestKeyExchange(unittest.TestCase):
    def test_make_temp_keypair_creates_a_private_and_public_file(self):
        priv, pub = volume_migrate.make_temp_keypair()
        self.assertTrue(priv.is_file())
        self.assertTrue(pub.is_file())
        self.assertEqual(pub.name, priv.name + ".pub")

    def test_install_key_on_pipes_the_public_key_over_stdin(self):
        priv, pub = volume_migrate.make_temp_keypair()
        with mock.patch("subprocess.run") as mock_run:
            volume_migrate.install_key_on("host-b", 1002, pub)
        self.assertEqual(mock_run.call_args.kwargs.get("input"), pub.read_text())


class TestExistingSubdirs(unittest.TestCase):
    def test_only_returns_subdirs_that_are_actually_present(self):
        listing = mock.Mock(stdout="loras\ncheckpoints\nsome_other_dir\n")
        with mock.patch("subprocess.run", return_value=listing):
            present = volume_migrate.existing_subdirs("host-a", 1001)
        self.assertIn("loras", present)
        self.assertIn("checkpoints", present)
        self.assertNotIn("some_other_dir", present)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 -m unittest scripts.tests.test_batch_volume_migrate -v`
Expected: `AttributeError: module 'volume_migrate' has no attribute 'count_pending_changes'`

- [ ] **Step 3: Implement in `scripts/volume_migrate.py`**

```python
import tempfile

MODEL_SUBDIRS = ["diffusion_models", "text_encoders", "loras", "checkpoints",
                 "clip_vision", "vae", "upscale_models", "PGDATA", "minio"]
# 16 concurrent got some connections MaxStartups-rejected — measured
# 2026-08-29, docs/gpu-pod.md#volume-migrate.
MAX_RSYNC_THREADS = 8


def make_temp_keypair() -> tuple[Path, Path]:
    tmpdir = Path(tempfile.mkdtemp(prefix="migrate-ssh-"))
    priv = tmpdir / "id_migrate"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv), "-q"],
                  check=True)
    return priv, priv.with_suffix(".pub")


def install_key_on(host: str, port: int, pub_key_path: Path) -> None:
    """Appends the temp public key to pod B's authorized_keys — piped over
    stdin rather than interpolated into a shell command."""
    pub_key = pub_key_path.read_text(encoding="utf-8")
    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port), f"root@{host}",
         "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys"],
        input=pub_key, text=True, check=True)


def place_key_on(host: str, port: int, priv_key_path: Path) -> None:
    """Copies the temp private key onto pod A, so pod A can SSH straight to
    pod B without routing the transfer through this machine."""
    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port), f"root@{host}",
         "mkdir -p ~/.ssh_migrate"], check=True)
    subprocess.run(
        ["scp", "-o", "StrictHostKeyChecking=accept-new", "-P", str(port),
         str(priv_key_path), f"root@{host}:~/.ssh_migrate/id_migrate"], check=True)
    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port), f"root@{host}",
         "chmod 600 ~/.ssh_migrate/id_migrate"], check=True)


def existing_subdirs(host: str, port: int, mount: str = "/workspace") -> list[str]:
    """Only sync subdirectories that actually exist — a smaller/newer volume
    will not have every entry in MODEL_SUBDIRS."""
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port), f"root@{host}",
         f"ls {mount}"], capture_output=True, text=True, check=True)
    present = set(result.stdout.split())
    return [d for d in MODEL_SUBDIRS if d in present]


def _rsync_cmd(mount: str, subdir: str, dest_host: str, dest_port: int,
              dry_run: bool) -> str:
    flags = "-avnc" if dry_run else "-a"
    return (f"rsync {flags} -e 'ssh -i ~/.ssh_migrate/id_migrate "
           f"-o StrictHostKeyChecking=accept-new -p {dest_port}' "
           f"{mount}/{subdir}/ root@{dest_host}:{mount}/{subdir}/")


def sync(host_a: str, port_a: int, host_b: str, port_b: int, subdirs: list[str],
        mount: str = "/workspace") -> None:
    """rsync -a, one process per subdir, run FROM pod A (using the temp key
    placed there by place_key_on) straight to pod B — never routed through
    this machine. Capped at MAX_RSYNC_THREADS concurrent.
    """
    for start in range(0, len(subdirs), MAX_RSYNC_THREADS):
        batch = subdirs[start:start + MAX_RSYNC_THREADS]
        procs = [
            subprocess.Popen(
                ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port_a),
                 f"root@{host_a}", _rsync_cmd(mount, d, host_b, port_b, dry_run=False)])
            for d in batch
        ]
        for p in procs:
            rc = p.wait()
            if rc != 0:
                raise RuntimeError(f"rsync leg exited {rc} — see pod A's own stderr above")


def count_pending_changes(rsync_dry_run_output: str) -> int:
    """How many real changes `rsync -avnc`'s stdout reports.

    -v lists one line per file that WOULD be transferred, then a blank line,
    then a summary block starting with "sent". Everything before that
    summary counts, EXCEPT the bare "./" line -a always prints for the top
    directory itself even when nothing inside it changed — counting that
    line would report 1 pending change on a perfectly clean verify.
    """
    count = 0
    for line in rsync_dry_run_output.splitlines():
        if line.startswith("sent ") or not line.strip():
            break
        if line.strip() == "./":
            continue
        count += 1
    return count


def verify(host_a: str, port_a: int, host_b: str, port_b: int, subdirs: list[str],
          mount: str = "/workspace") -> int:
    """rsync -avnc (dry-run + checksum) for every subdir, summed. The ONE
    gate between "copied" and "safe to delete the original" — see
    count_pending_changes and the Global Constraints at the top of this plan.
    """
    total = 0
    for d in subdirs:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port_a),
             f"root@{host_a}", _rsync_cmd(mount, d, host_b, port_b, dry_run=True)],
            capture_output=True, text=True, check=True)
        total += count_pending_changes(result.stdout)
    return total
```

- [ ] **Step 4: Run the tests again**

Run: `python3 -m unittest scripts.tests.test_batch_volume_migrate -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/volume_migrate.py scripts/tests/test_batch_volume_migrate.py
git commit -m "feat(migrate): SSH key exchange, rsync sync, and checksum verify"
```

---

## Task 7: Teardown, swap-or-abort, and the real `main()`

**Files:**
- Modify: `scripts/volume_migrate.py`
- Modify: `scripts/tests/test_batch_volume_migrate.py`

**Interfaces:**
- Consumes: everything from Tasks 4-6.
- Produces: `teardown_temp_pods(pod_a: str | None, pod_b: str | None) -> None`;
  `swap(*, new_volume_id: str, old_volume_id: str) -> None`; the completed `main()`.

- [ ] **Step 1: Write the failing tests**

```python
class TestTeardownTempPods(unittest.TestCase):
    def test_deletes_both_and_clears_the_lease(self):
        with mock.patch("subprocess.run") as mock_run, \
             mock.patch("volume_migrate.clear_migrate_lease") as mock_clear:
            volume_migrate.teardown_temp_pods("pod-a", "pod-b")
        deleted = [c.args[0][-1] for c in mock_run.call_args_list]
        self.assertEqual(set(deleted), {"pod-a", "pod-b"})
        mock_clear.assert_called_once()

    def test_tolerates_one_pod_never_having_been_provisioned(self):
        with mock.patch("subprocess.run") as mock_run, \
             mock.patch("volume_migrate.clear_migrate_lease"):
            volume_migrate.teardown_temp_pods("pod-a", None)
        self.assertEqual(mock_run.call_count, 1)


class TestSwap(unittest.TestCase):
    def test_writes_env_and_deletes_the_old_volume(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        ok = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH",
                               env_path.parent / "p.json"), \
             mock.patch("subprocess.run", return_value=ok) as mock_run:
            volume_migrate.swap(new_volume_id="vol-new", old_volume_id="vol-old")
        self.assertEqual(env_get(env_path, "POD_VOLUME_ID"), "vol-new")
        self.assertIn("vol-old", mock_run.call_args.args[0])

    def test_a_failed_delete_is_reported_but_env_is_still_swapped(self):
        env_path = Path(tempfile.mkdtemp()) / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        failed = mock.Mock(returncode=1, stderr="still referenced")
        with mock.patch.object(volume_migrate, "ENV_PATH", env_path), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH",
                               env_path.parent / "p.json"), \
             mock.patch("subprocess.run", return_value=failed):
            volume_migrate.swap(new_volume_id="vol-new", old_volume_id="vol-old")
        self.assertEqual(env_get(env_path, "POD_VOLUME_ID"), "vol-new")
        payload = json.loads((env_path.parent / "p.json").read_text())
        self.assertIn("still referenced", payload["warning"])


class TestMainEndToEnd(unittest.TestCase):
    def _env(self, tmpdir):
        env_path = tmpdir / ".env"
        env_path.write_text("POD_VOLUME_ID=vol-old\n", encoding="utf-8")
        return env_path

    def test_a_verify_mismatch_aborts_without_deleting_anything(self):
        tmpdir = Path(tempfile.mkdtemp())
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env(tmpdir)), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH", tmpdir / "p.json"), \
             mock.patch.object(volume_migrate, "LEASE_PATH", tmpdir / "lease.json"), \
             mock.patch.object(volume_migrate, "create_volume",
                               return_value=("vol-new", 10, "EU-RO-1")), \
             mock.patch.object(volume_migrate, "provision_temp_pod",
                               side_effect=["pod-a", "pod-b"]), \
             mock.patch.object(volume_migrate, "wait_for_ssh",
                               side_effect=[("host-a", 1), ("host-b", 2)]), \
             mock.patch.object(volume_migrate, "make_temp_keypair",
                               return_value=(Path("/tmp/k"), Path("/tmp/k.pub"))), \
             mock.patch.object(volume_migrate, "install_key_on"), \
             mock.patch.object(volume_migrate, "place_key_on"), \
             mock.patch.object(volume_migrate, "existing_subdirs", return_value=["loras"]), \
             mock.patch.object(volume_migrate, "sync"), \
             mock.patch.object(volume_migrate, "verify", return_value=2), \
             mock.patch.object(volume_migrate, "teardown_temp_pods") as mock_teardown, \
             mock.patch("subprocess.run") as mock_run:
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
        self.assertEqual(rc, 1)
        mock_teardown.assert_called_once_with("pod-a", "pod-b")
        # Nothing may call network-volume delete when verify found a mismatch.
        for call in mock_run.call_args_list:
            self.assertNotIn("delete", call.args[0])

    def test_a_provisioning_failure_still_tears_down_whatever_was_created(self):
        tmpdir = Path(tempfile.mkdtemp())
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env(tmpdir)), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH", tmpdir / "p.json"), \
             mock.patch.object(volume_migrate, "LEASE_PATH", tmpdir / "lease.json"), \
             mock.patch.object(volume_migrate, "create_volume",
                               return_value=("vol-new", 10, "EU-RO-1")), \
             mock.patch.object(volume_migrate, "provision_temp_pod",
                               side_effect=["pod-a", RuntimeError("no capacity")]), \
             mock.patch.object(volume_migrate, "teardown_temp_pods") as mock_teardown:
            with self.assertRaises(RuntimeError):
                volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
        # pod-a was created before pod-b failed — it must still be torn down,
        # and pod-b (never assigned) must be passed as None, not omitted.
        mock_teardown.assert_called_once_with("pod-a", None)

    def test_a_clean_verify_swaps_env_and_reports_done(self):
        tmpdir = Path(tempfile.mkdtemp())
        with mock.patch.object(volume_migrate, "ENV_PATH", self._env(tmpdir)), \
             mock.patch.object(volume_migrate, "PROGRESS_PATH", tmpdir / "p.json"), \
             mock.patch.object(volume_migrate, "LEASE_PATH", tmpdir / "lease.json"), \
             mock.patch.object(volume_migrate, "create_volume",
                               return_value=("vol-new", 10, "EU-RO-1")), \
             mock.patch.object(volume_migrate, "provision_temp_pod",
                               side_effect=["pod-a", "pod-b"]), \
             mock.patch.object(volume_migrate, "wait_for_ssh",
                               side_effect=[("host-a", 1), ("host-b", 2)]), \
             mock.patch.object(volume_migrate, "make_temp_keypair",
                               return_value=(Path("/tmp/k"), Path("/tmp/k.pub"))), \
             mock.patch.object(volume_migrate, "install_key_on"), \
             mock.patch.object(volume_migrate, "place_key_on"), \
             mock.patch.object(volume_migrate, "existing_subdirs", return_value=["loras"]), \
             mock.patch.object(volume_migrate, "sync"), \
             mock.patch.object(volume_migrate, "verify", return_value=0), \
             mock.patch.object(volume_migrate, "teardown_temp_pods"), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0, stderr="")):
            rc = volume_migrate.main(["--to-dc", "EU-CZ-1", "--yes"])
        self.assertEqual(rc, 0)
        self.assertEqual(env_get(volume_migrate.ENV_PATH, "POD_VOLUME_ID"), "vol-new")
        payload = json.loads((tmpdir / "p.json").read_text())
        self.assertEqual(payload["phase"], "done")
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 -m unittest scripts.tests.test_batch_volume_migrate -v`
Expected: `AttributeError: module 'volume_migrate' has no attribute 'teardown_temp_pods'`

- [ ] **Step 3: Implement in `scripts/volume_migrate.py`**

```python
def teardown_temp_pods(pod_a: str | None, pod_b: str | None) -> None:
    """ALWAYS called, success or failure — same discipline as
    drain.py::teardown. A pod_id of None means it was never provisioned
    (an earlier phase failed first) and is simply skipped, not an error.
    """
    for pod_id in (pod_a, pod_b):
        if pod_id is not None:
            subprocess.run(["runpodctl", "pod", "delete", pod_id], check=False)
    clear_migrate_lease(LEASE_PATH)


def swap(*, new_volume_id: str, old_volume_id: str) -> None:
    """Only ever called after verify() has reported exactly 0 pending
    changes — the caller (main) is where that gate lives."""
    env_set(ENV_PATH, "POD_VOLUME_ID", new_volume_id)
    delete = subprocess.run(["runpodctl", "network-volume", "delete", old_volume_id],
                            capture_output=True, text=True)
    if delete.returncode != 0:
        write_progress("done",
                       warning=f"copied and swapped .env, but could not delete "
                               f"{old_volume_id}: {delete.stderr.strip()}")
        return
    write_progress("done")
```

Replace the `main()` body's `# Phases 2-6 land in later tasks of this plan.` placeholder line
with:

```python
    write_progress("create", to_dc=args.to_dc, new_volume_id=new_volume_id)
    pod_a: str | None = None
    pod_b: str | None = None
    try:
        pod_a = provision_temp_pod("migrate-tmp-a", old_volume_id, source_dc, size_gb + 20)
        pod_b = provision_temp_pod("migrate-tmp-b", new_volume_id, args.to_dc, size_gb + 20)
        write_migrate_lease(LEASE_PATH, MigrateLease(
            pod_a_id=pod_a, pod_b_id=pod_b, started_at=time.time(), to_dc=args.to_dc))

        write_progress("sync", pod_a=pod_a, pod_b=pod_b)
        host_a, port_a = wait_for_ssh(pod_a)
        host_b, port_b = wait_for_ssh(pod_b)
        priv, pub = make_temp_keypair()
        install_key_on(host_b, port_b, pub)
        place_key_on(host_a, port_a, priv)
        subdirs = existing_subdirs(host_a, port_a)
        sync(host_a, port_a, host_b, port_b, subdirs)

        write_progress("verify", pod_a=pod_a, pod_b=pod_b)
        total_changes = verify(host_a, port_a, host_b, port_b, subdirs)
    finally:
        teardown_temp_pods(pod_a, pod_b)

    if total_changes != 0:
        write_progress("failed",
                       reason=f"{total_changes} file(s) still differ after sync — "
                              f"NOT deleting {old_volume_id}. Both volumes kept: "
                              f"{old_volume_id} (original), {new_volume_id} (partial copy).")
        print(f"✗ verify found {total_changes} pending change(s) — aborting, "
             f"both volumes kept", file=sys.stderr)
        return 1

    swap(new_volume_id=new_volume_id, old_volume_id=old_volume_id)
    return 0
```

And wrap the ENTIRE body of `main()` (from right after argument parsing) in an outer
`try/except Exception`, so any truly unexpected failure still leaves a readable "failed" progress
entry instead of an uncaught traceback with no final phase written:

```python
    try:
        old_volume_id = env_get(ENV_PATH, "POD_VOLUME_ID")
        ... (everything above, unchanged) ...
        swap(new_volume_id=new_volume_id, old_volume_id=old_volume_id)
        return 0
    except Exception as exc:
        write_progress("failed", reason=repr(exc))
        raise
```

Re-raising (rather than swallowing into a `return 1`) is deliberate: Task's own
`test_a_provisioning_failure_still_tears_down_whatever_was_created` asserts the exception
propagates, matching how a truly unexpected error should surface (a non-zero exit AND a visible
traceback for whoever is watching the process directly), while the EXPECTED failure case — a
verify mismatch — is handled earlier as a plain `return 1`, no exception.

- [ ] **Step 4: Run the tests again**

Run: `python3 -m unittest scripts.tests.test_batch_volume_migrate -v`
Expected: all PASS

- [ ] **Step 5: Run the FULL script's test file one more time standalone to catch any import-order
issue introduced by wrapping `main()`**

Run: `python3 -m unittest scripts.tests.test_batch_volume_migrate -v`
Expected: all PASS (same command as Step 4 — run twice is deliberate, `main()`'s outer try/except
is exactly the kind of change that can silently swallow a real regression if step 4 was run before
the edit was complete)

- [ ] **Step 6: Commit**

```bash
git add scripts/volume_migrate.py scripts/tests/test_batch_volume_migrate.py
git commit -m "feat(migrate): teardown, verified swap, and the complete main()"
```

---

## Task 8: Trigger button in the bot's Run-confirm GPU picker

**Files:**
- Modify: `scripts/tgbot/bot.py`
- Modify: `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: `MigrateLease`, `read_migrate_lease` (Task 1); `_offer_run_confirm`,
  `_STOCK_ICON`, `_GPU_SHORT`, `_esc`, `_CB_RUN_NO` (existing, `scripts/tgbot/bot.py`).
- Produces: `_CB_MIGRATE_ASK`, `_CB_MIGRATE_GO`, `_CB_MIGRATE_NO` (callback prefixes);
  `_ask_migrate(tg: Tg, chat_id: int, gpu_id: str, to_dc: str) -> None`;
  `_start_migration(tg: Tg, chat_id: int, to_dc: str) -> None`;
  `migration_running() -> bool`.

- [ ] **Step 1: Read the current "Other regions" loop before touching it**

Run: `grep -n "Other regions" scripts/tgbot/bot.py` and read `_offer_run_confirm` in full — the
button this task adds attaches to the SAME `other_lines`/`elsewhere` loop already built there
(2026-09-02, "the 5090 being out at home still surfaces other regions"). Do not rebuild that
loop; extend it.

- [ ] **Step 2: Write the failing tests**

```python
# added to scripts/tests/test_batch_bot.py, near the other TestFlow GPU-picker tests
def test_other_regions_offer_a_migrate_button(self):
    stock = {
        "NVIDIA GeForce RTX 5090": [
            Stock(gpu_id="NVIDIA GeForce RTX 5090", display_name="RTX 5090",
                 price_per_hr=0.99, datacenter_id="EU-RO-1", stock_status="none"),
            Stock(gpu_id="NVIDIA GeForce RTX 5090", display_name="RTX 5090",
                 price_per_hr=0.99, datacenter_id="EU-CZ-1", stock_status="High"),
        ],
        "NVIDIA GeForce RTX 4090": [
            Stock(gpu_id="NVIDIA GeForce RTX 4090", display_name="RTX 4090",
                 price_per_hr=0.74, datacenter_id="EU-RO-1", stock_status="Medium"),
        ],
        "NVIDIA RTX PRO 4500 Blackwell": [
            Stock(gpu_id="NVIDIA RTX PRO 4500 Blackwell", display_name="RTX PRO 4500",
                 price_per_hr=0.72, datacenter_id="EU-RO-1", stock_status="Medium"),
        ],
    }
    with mock.patch("tgbot.bot.drain_running", return_value=False), \
         mock.patch("tgbot.bot.volume_datacenter", return_value="EU-RO-1"), \
         mock.patch("tgbot.bot.stock_at", return_value=stock), \
         mock.patch("tgbot.bot.migration_running", return_value=False):
        self._fill_required_slots()
        bot.handle(self.tg, cb_from(ME, bot._CB_RUN_ASK), allowed_user_id=ME)
    flat_data = [data for row in self.tg.buttons[-1] for _, data in row]
    self.assertTrue(any(d.startswith(bot._CB_MIGRATE_ASK) for d in flat_data))

def test_tapping_migrate_ask_shows_the_destructive_confirm(self):
    with mock.patch("tgbot.bot.migration_running", return_value=False):
        bot.handle(self.tg,
                  cb_from(ME, bot._CB_MIGRATE_ASK + "5090,EU-CZ-1"),
                  allowed_user_id=ME)
    text = self.tg.messages[-1]
    self.assertIn("cannot be undone", text.lower())
    self.assertIn("EU-CZ-1", text)
    flat_data = [data for row in self.tg.buttons[-1] for _, data in row]
    self.assertTrue(any(d.startswith(bot._CB_MIGRATE_GO) for d in flat_data))
    self.assertIn(bot._CB_MIGRATE_NO, flat_data)

def test_migrate_go_launches_the_script_exactly_once(self):
    with mock.patch("tgbot.bot.subprocess.Popen") as mock_popen, \
         mock.patch("tgbot.bot.migration_running", return_value=False):
        bot.handle(self.tg,
                  cb_from(ME, bot._CB_MIGRATE_GO + "EU-CZ-1"),
                  allowed_user_id=ME)
    mock_popen.assert_called_once()
    argv = mock_popen.call_args.args[0]
    self.assertIn("scripts/volume_migrate.py", argv)
    self.assertIn("EU-CZ-1", argv)
    self.assertIn("--yes", argv)

def test_a_second_migrate_attempt_while_one_runs_is_refused(self):
    with mock.patch("tgbot.bot.migration_running", return_value=True):
        bot.handle(self.tg,
                  cb_from(ME, bot._CB_MIGRATE_ASK + "5090,EU-CZ-1"),
                  allowed_user_id=ME)
    self.assertIn("already", self.tg.messages[-1].lower())

def test_confirm_is_refused_while_a_migration_is_in_flight(self):
    with mock.patch("tgbot.bot.migration_running", return_value=True):
        self._fill_required_slots()
        bot.handle(self.tg, cmd_from(ME, "/confirm"), allowed_user_id=ME)
    self.assertIn("migrat", self.tg.messages[-1].lower())
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `python3 -m unittest scripts.tests.test_batch_bot -v -k migrate`
Expected: `AttributeError: module 'tgbot.bot' has no attribute '_CB_MIGRATE_ASK'`

- [ ] **Step 4: Implement in `scripts/tgbot/bot.py`**

Add the import near the other `batchlib_ext` imports at the top:

```python
from batchlib_ext.migrate_lease import read_migrate_lease
```

Add near the other `_CB_RUN_*` constants:

```python
_CB_MIGRATE_ASK = "mig:ask:"    # + "<gpu_short>,<to_dc>"
_CB_MIGRATE_GO = "mig:go:"      # + "<to_dc>"
_CB_MIGRATE_NO = "mig:no"
_MIGRATE_LEASE_PATH = ROOT / "batch" / "volume-migrate-lease.json"
```

Add near `_offer_run_confirm`:

```python
def migration_running() -> bool:
    """A volume migration currently in flight — checked before offering a
    NEW migration button, and before letting /confirm rent at the OLD
    datacenter mid-copy. The lease existing is proof enough; nothing here
    needs to reach the pods themselves.
    """
    return read_migrate_lease(_MIGRATE_LEASE_PATH) is not None
```

In `_offer_run_confirm`'s "Other regions" loop (the one iterating `elsewhere[:2]` and appending to
`other_lines`), add a migrate button per entry, ONLY when no migration is already running:

```python
        for e in elsewhere[:2]:
            icon = _STOCK_ICON.get(e.stock_status.lower(), "⬜")
            other_lines.append(f"  {icon} {_esc(e.display_name)} — "
                               f"{_esc(e.datacenter_id)}: {_esc(e.stock_status)}")
            short = _GPU_SHORT.get(gpu_id)
            if short and not migration_running():
                switch_row.append((f"Switch to {e.display_name} ({e.datacenter_id})",
                                  _CB_MIGRATE_ASK + f"{short},{e.datacenter_id}"))
```

(This sits in the same `for gpu_id in wanted:` loop as the existing "Other regions" collection —
add it right after the `other_lines.append(...)` line already there, reusing the same `e` and
`gpu_id` the loop already has in scope.)

Add the confirm-then-launch handlers, near `_ask_kill`/`_do_kill`:

```python
def _ask_migrate(tg: Tg, chat_id: int, gpu_id: str, to_dc: str) -> None:
    if migration_running():
        tg.send_message(chat_id, "a volume migration is already in progress — "
                                 "wait for it to finish before starting another")
        return
    tg.send_message(
        chat_id,
        f"This copies your Network Volume to {_esc(to_dc)}: ~2 temporary CPU pods "
        f"for the duration, then <b>deletes the current volume</b> once the copy is "
        f"verified byte-for-byte. Estimated 15-25 minutes. "
        f"<b>Cannot be undone</b> once the old volume is deleted.",
        parse_mode=PARSE_HTML,
        buttons=[[("Yes, migrate", _CB_MIGRATE_GO + to_dc),
                  ("Cancel", _CB_MIGRATE_NO)]])


def _start_migration(tg: Tg, chat_id: int, to_dc: str) -> None:
    if migration_running():
        tg.send_message(chat_id, "a volume migration is already in progress")
        return
    subprocess.Popen(["python3", "scripts/volume_migrate.py", "--to-dc", to_dc, "--yes"],
                     cwd=_REPO_ROOT)
    tg.send_message(chat_id, f"🚀 Migration to {_esc(to_dc)} started — this will take "
                             "15-25 minutes. I will report progress here.",
                    parse_mode=PARSE_HTML)
```

Wire the dispatch in `_handle_callback` (near the other `_CB_RUN_*`/`_CB_KILL_*` branches):

```python
    elif data.startswith(_CB_MIGRATE_ASK):
        gpu_short, to_dc = data[len(_CB_MIGRATE_ASK):].split(",", 1)
        gpu_id = _GPU_BY_SHORT.get(gpu_short, gpu_short)
        _ask_migrate(tg, chat_id, gpu_id, to_dc)
    elif data.startswith(_CB_MIGRATE_GO):
        _start_migration(tg, chat_id, data[len(_CB_MIGRATE_GO):])
    elif data == _CB_MIGRATE_NO:
        tg.send_message(chat_id, "kept — nothing migrated")
```

Finally, add the sibling guard in `_do_confirm` — right next to (not replacing) the existing
`drain_running` check that decides `running`/queue-vs-start:

```python
    if migration_running():
        tg.send_message(chat_id, "a volume migration is in progress for this pod's "
                                 "datacenter — wait for it to finish before renting")
        return
```

Place this check at the very top of `_do_confirm`, before the `job = _STATE.get(chat_id)` line —
renting must not be allowed to race a migration regardless of how complete the job is.

- [ ] **Step 5: Run the tests again**

Run: `python3 -m unittest scripts.tests.test_batch_bot -v`
Expected: all PASS, including every pre-existing test in the file (no regression to the GPU
picker or the queue/handoff flow from earlier the same day)

- [ ] **Step 6: Commit**

```bash
git add scripts/tgbot/bot.py scripts/tests/test_batch_bot.py
git commit -m "feat(migrate): trigger button + destructive confirm in the Run picker"
```

---

## Task 9: Migration progress reporting in the bot's poll loop

**Files:**
- Modify: `scripts/tgbot/bot.py`
- Modify: `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: `_MIGRATE_LEASE_PATH`... actually the progress file, not the lease:
  `_MIGRATE_PROGRESS_PATH = ROOT / "batch" / "volume-migrate.progress.json"` (new constant, same
  path `volume_migrate.py`'s `PROGRESS_PATH` writes to).
- Produces: `tick_migration_progress(tg: Tg, chat_id: int) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_tick_migration_progress_edits_one_message_across_phases(self):
    prog_path = bot._MIGRATE_PROGRESS_PATH
    prog_path.parent.mkdir(parents=True, exist_ok=True)
    prog_path.write_text(json.dumps({"phase": "sync", "at": 0.0}), encoding="utf-8")
    bot.tick_migration_progress(self.tg, ME)
    self.assertTrue(bot._migrate_progress_message_path(ME).exists())
    first_message_id = json.loads(
        bot._migrate_progress_message_path(ME).read_text())["message_id"]

    prog_path.write_text(json.dumps({"phase": "verify", "at": 1.0}), encoding="utf-8")
    bot.tick_migration_progress(self.tg, ME)
    second_message_id = json.loads(
        bot._migrate_progress_message_path(ME).read_text())["message_id"]
    self.assertEqual(first_message_id, second_message_id)   # edited, not re-sent

def test_tick_migration_progress_delivers_a_final_message_on_done(self):
    prog_path = bot._MIGRATE_PROGRESS_PATH
    prog_path.parent.mkdir(parents=True, exist_ok=True)
    prog_path.write_text(json.dumps({"phase": "sync", "at": 0.0}), encoding="utf-8")
    bot.tick_migration_progress(self.tg, ME)

    prog_path.write_text(json.dumps({"phase": "done", "at": 1.0}), encoding="utf-8")
    bot.tick_migration_progress(self.tg, ME)
    self.assertIn("done", self.tg.messages[-1].lower())
    self.assertFalse(prog_path.exists())   # consumed, like the drain progress file

def test_tick_migration_progress_reports_a_failed_phase(self):
    prog_path = bot._MIGRATE_PROGRESS_PATH
    prog_path.parent.mkdir(parents=True, exist_ok=True)
    prog_path.write_text(json.dumps(
        {"phase": "failed", "at": 0.0, "reason": "2 file(s) still differ"}),
        encoding="utf-8")
    bot.tick_migration_progress(self.tg, ME)
    self.assertIn("2 file(s) still differ", self.tg.messages[-1])

def test_a_tick_with_no_migration_in_progress_does_nothing(self):
    bot.tick_migration_progress(self.tg, ME)
    self.assertEqual(self.tg.messages, [])
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python3 -m unittest scripts.tests.test_batch_bot -v -k migration_progress`
Expected: `AttributeError: module 'tgbot.bot' has no attribute 'tick_migration_progress'`

- [ ] **Step 3: Implement in `scripts/tgbot/bot.py`**

Add the path constants near `_PROGRESS_SUFFIX`:

```python
_MIGRATE_PROGRESS_PATH = ROOT / "batch" / "volume-migrate.progress.json"


def _migrate_progress_message_path(chat_id: int) -> Path:
    """Which message this bot process is keeping edited for a migration in
    progress — mirrors _progress_path's own reasoning (a migration can
    outlive a bot restart) but is its OWN file: a migration is a single
    system-wide operation, not one per chat, and must never be confused with
    a per-chat batch drain's progress file."""
    return ROOT / "batch" / f"tg-{chat_id}.migrate-progress.json"
```

Add the tick function near `tick_progress`:

```python
def tick_migration_progress(tg: Tg, chat_id: int) -> None:
    """Re-render the migration progress message, same shape as tick_progress
    for a drain — one message, edited in place, a final delivery on
    done/failed. Called every poll tick alongside tick_progress; harmless
    no-op when no migration is running.
    """
    if not _MIGRATE_PROGRESS_PATH.exists():
        return
    try:
        payload = json.loads(_MIGRATE_PROGRESS_PATH.read_text(encoding="utf-8"))
        phase = str(payload["phase"])
    except (ValueError, KeyError, TypeError) as exc:
        log(f"migration progress file unreadable, ignoring: {exc!r}")
        return

    text = {
        "create": "🔄 <b>Migrating volume</b> — creating the destination volume…",
        "sync": "🔄 <b>Migrating volume</b> — copying data between temp pods…",
        "verify": "🔄 <b>Migrating volume</b> — verifying checksums…",
        "done": "✅ Migration finished.",
        "failed": f"⚠️ Migration failed: {_esc(payload.get('reason', 'unknown error'))}",
    }.get(phase, f"🔄 <b>Migrating volume</b> — {_esc(phase)}")
    if phase == "done" and payload.get("warning"):
        text += f"\n{_esc(payload['warning'])}"

    msg_path = _migrate_progress_message_path(chat_id)
    if msg_path.exists():
        message_id = json.loads(msg_path.read_text(encoding="utf-8"))["message_id"]
        tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)
    else:
        message_id = tg.send_message(chat_id, text, parse_mode=PARSE_HTML)
        msg_path.write_text(json.dumps({"message_id": message_id}), encoding="utf-8")

    if phase in ("done", "failed"):
        _MIGRATE_PROGRESS_PATH.unlink(missing_ok=True)
        msg_path.unlink(missing_ok=True)
```

Wire it into `main()`'s poll loop, right next to the existing `tick_progress(tg, allowed_user_id)`
call (find that call with `grep -n "tick_progress(tg" scripts/tgbot/bot.py` and add the new call
on the line right after it):

```python
        tick_migration_progress(tg, allowed_user_id)
```

- [ ] **Step 4: Run the tests again**

Run: `python3 -m unittest scripts.tests.test_batch_bot -v`
Expected: all PASS, including every pre-existing test (no regression)

- [ ] **Step 5: Commit**

```bash
git add scripts/tgbot/bot.py scripts/tests/test_batch_bot.py
git commit -m "feat(migrate): report migration progress the same way a drain does"
```

---

## Task 10: Docs — point the manual runbook at the automated path

**Files:**
- Modify: `docs/gpu-pod.md`

- [ ] **Step 1: Add a note above the existing manual runbook**

Find the `<a id="volume-migrate"></a>` heading in `docs/gpu-pod.md` and add, directly under it,
before the existing "RunPod chỉ cho tăng dung lượng..." paragraph:

```markdown
> **Từ 2026-09-02 có đường tự động**: `python3 scripts/volume_migrate.py --to-dc EU-CZ-1 --yes`
> làm đúng 6 bước dưới đây (tạo volume mới, 2 pod CPU tạm, rsync, verify checksum, xoá volume cũ
> CHỈ khi khớp 100%, luôn destroy 2 pod tạm) và báo tiến độ qua bot Telegram. Runbook tay dưới đây
> vẫn giữ lại làm tài liệu tham khảo / phương án khi script không chạy được (thiếu RUNPOD_API_KEY,
> muốn kiểm soát từng bước bằng tay, …). Thiết kế: docs/superpowers/specs/2026-09-02-volume-
> region-migration-design.md.
```

- [ ] **Step 2: After the first real live run, replace the unmeasured throughput note**

This step is NOT done now — it is a reminder for whoever runs this migration for real the first
time. §4 of the design spec explicitly flags that this script's own `rsync` throughput at 8
threads/4 vCPU is unmeasured. The first live run must have its real throughput number (MB/s and
total wall-clock minutes) added to the measured table in this same section of `docs/gpu-pod.md`,
replacing the open question in spec §9 rather than leaving it a permanent guess.

- [ ] **Step 3: Commit**

```bash
git add docs/gpu-pod.md
git commit -m "docs(gpu-pod): point the manual EU-CZ-1 runbook at the automated script"
```

---

## Self-Review Notes (already applied above, kept for the record)

- **Spec coverage:** §3 architecture → Tasks 4-7. §4 step detail (rsync over dd, 4 vCPU, 8
  threads, verify gate) → Tasks 5-6, encoded directly in the Global Constraints. §5 temp-pod
  lease → Tasks 1-3. §6 progress reporting → Task 9. §7 trigger point → Task 8 (including the
  `/confirm`-blocking guard). §8 testing strategy → every task's own test-first step, with
  `count_pending_changes` given the most coverage as called out explicitly. §9 open questions →
  Task 10 Step 2 carries the unmeasured-throughput note forward rather than silently dropping it.
- **Placeholder scan:** no "TODO"/"handle errors"/"similar to Task N" left in any step; every
  code block is complete, runnable code.
- **Type consistency:** `MigrateLease` fields (`pod_a_id`, `pod_b_id`, `started_at`, `to_dc`) are
  identical across Tasks 1, 2, 3, 7, 8. `create_volume`'s three-tuple return
  `(new_volume_id, size_gb, source_dc)` is consumed with that exact unpacking in Task 4's `main()`
  skeleton and never renamed later. `teardown_temp_pods(pod_a: str | None, pod_b: str | None)`
  accepts `None` from Task 7 onward, matching `main()`'s `pod_a = pod_b = None` initialization.
