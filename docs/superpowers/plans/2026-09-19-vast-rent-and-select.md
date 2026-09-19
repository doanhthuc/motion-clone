# Vast provider — choosing and renting a machine (Plan 2 of 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `pod-provision.sh`'s cheapest-first Vast search with a rent function that ranks machines by time-to-ready and total cost, remembers what each machine actually did, gives up on a machine whose image pull is too slow, and can never leave an abandoned instance billing. Also closes the hardening items Plan 1's review parked.

**Architecture:** Two pure modules (`batchlib_ext/vast_scoreboard.py` — per-machine history; `batchlib_ext/vast_select.py` — filter + rank) and one orchestration script (`scripts/vast_rent.py`) that talks to the `vastai` CLI through a small injectable `VastApi`. `pod-provision.sh`'s Vast branch becomes `exec python3 scripts/vast_rent.py …`. All logic is unit-tested with fakes; nothing in this plan spends money (the first paid session is Plan 4's gate).

**Tech Stack:** Python 3 `unittest` (`scripts/tests/`), bash, GNU make 3.81, `vastai` CLI 1.3.0.

**Spec:** `docs/superpowers/specs/2026-09-19-vast-fallback-design.md` — §3.2 (renting, all five numbered points and the "other facts"), §5 (unproven items). Plan 1: `docs/superpowers/plans/2026-09-19-vast-provider-safety-net.md` (landed; its "Carry-forward" section feeds Tasks 6–7 here). Plan 3 (models per batch + download probe) and Plan 4 (bot picker + first paid sessions) follow.

## Global Constraints

- Docs, comments and commit messages in **English**. Do not add `# #region ALD` markers. Do not translate existing Vietnamese comments.
- `motions-studio/setup/scrub-secrets.sh --check` must exit 0 before **every** commit (the repo is public). Never commit `.env` files.
- Nothing in this plan may rent, create, or destroy a real machine: every test uses fakes, `make -n`, or a fake `vastai` on `PATH`. Never run `vastai`, `runpodctl`, `pod-*.sh`, `make gpu-*` (without `-n`) or `make drain`.
- Run tests from the repo root: `python3 -m unittest discover -s scripts/tests -p '<file>' [-k <name>] -v`. Full gate: `make batch-test`. Make here is GNU Make 3.81.
- Commit trailers on every commit:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` and
  `Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb`
- Vast facts verified with `vastai` 1.3.0 on 2026-09-19: `vastai search offers "<query>" -o 'dph+' --raw` returns a JSON list (a random ~40-row sample); real offer rows carry `id`, `machine_id`, `dph_total`, `inet_down` (Mbps), `internet_down_cost_per_tb` ($), `disk_bw` (MB/s), `cpu_ghz`, `direct_port_count`, `rentable`, `reliability2`, `geolocation`, `gpu_ram`; `vastai create instance <id> … --cancel-unavail --label L --raw` (both flags exist); `vastai show instance <id> --raw` carries `actual_status`; `vastai ssh-url <id>` exists. **Unverified until the first paid session:** the exact `ssh-url` output text (assumed `ssh://root@HOST:PORT`, parsed tolerantly) and the `create --raw` reply keys (`new_contract`, assumed from the existing script).
- Measured numbers this plan encodes (docs/gpu-pod.md `#vast-ghcr`): slowest `create -> running` ever seen 556 s (Washington), fastest cold 325 s (Bulgaria), warm 32–35 s; `inet_down` advertised 1593 Mbps yet ghcr layer 15.9 MB/s (so advertised bandwidth is necessary, not sufficient); bandwidth priced per TB from ~$0 to $40/TB (a 50 GB boot is $2.00 at $40/TB, $0.07 at $1.37/TB).
- Tier-3 grace in the watchdog is `GRACE_MIN = 10` minutes **per instance**: the pull deadline plus overhead must stay under it, and a test enforces that.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `scripts/batchlib_ext/vast_scoreboard.py` | create | per-machine measured history, blacklist, atomic JSON store |
| `scripts/batchlib_ext/vast_select.py` | create | filter offers, rank by `dph × ready_s + bandwidth $`, explain rejections |
| `scripts/vast_rent.py` | create | `VastApi`, `RealVastApi`, `rent()` (create/wait/deadline/retry/abandon), CLI, `--ssh-target` |
| `scripts/pod-provision.sh` | modify | Vast branch → `exec vast_rent.py` |
| `scripts/pod-wait.sh` | modify | prefer the direct SSH address for Vast |
| `.gitignore` | modify | `batch/vast-machines.json` |
| `Makefile` | modify | harden the Vast destroy verify |
| `scripts/pod_watchdog.py` | modify | `destroy_verified` tolerates an already-gone instance |
| `scripts/tests/test_batch_vast_scoreboard.py` | create | |
| `scripts/tests/test_batch_vast_select.py` | create | |
| `scripts/tests/test_batch_vast_rent.py` | create | |
| `scripts/tests/test_batch_provider_wiring.py` | modify | label test follows the create call into Python; Makefile verify tests |
| `scripts/tests/test_batch_pod_watchdog.py` | modify | already-gone destroy tests |
| `docs/gpu-pod.md` | modify | rent behaviour, knobs, assumptions |

---

### Task 1: Scoreboard

**Files:**
- Create: `scripts/batchlib_ext/vast_scoreboard.py`
- Modify: `.gitignore`
- Test: `scripts/tests/test_batch_vast_scoreboard.py`

**Interfaces:**
- Produces:
  - `WORST_KNOWN_PULL_S: float = 556.0`, `BLACKLIST_TTL_S: float = 86400.0`
  - `@dataclass(frozen=True) MachineRecord(machine_id: int, pull_s: float | None, model_mbps: float | None, gb: float | None, measured_at: float, outcome: str)` — `outcome` is `"ok" | "slow_pull" | "failed"`
  - `class Scoreboard`: `get(machine_id: int) -> MachineRecord | None`; `is_blacklisted(machine_id: int, now: float, ttl: float = BLACKLIST_TTL_S) -> bool`; `ready_estimate_s(machine_id: int) -> float`; `known_good(limit: int = 3) -> list[int]`; `record(rec: MachineRecord) -> None`; `records() -> dict[int, MachineRecord]`
  - `load_board(path: Path) -> Scoreboard` (missing or corrupt file → empty); `save_board(path: Path, board: Scoreboard) -> None` (atomic)

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_batch_vast_scoreboard.py`:

```python
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from batchlib_ext.vast_scoreboard import (BLACKLIST_TTL_S, WORST_KNOWN_PULL_S,
                                          MachineRecord, Scoreboard, load_board,
                                          save_board)


def rec(machine_id=1, pull_s=300.0, outcome="ok", measured_at=1000.0, **kw):
    return MachineRecord(machine_id=machine_id, pull_s=pull_s,
                         model_mbps=kw.get("model_mbps"), gb=kw.get("gb"),
                         measured_at=measured_at, outcome=outcome)


class TestScoreboard(unittest.TestCase):
    def test_an_unknown_machine_is_scored_as_the_slowest_ever_seen(self):
        # Optimism about a machine nobody measured is how a 556 s pull gets rented twice.
        self.assertEqual(Scoreboard({}).ready_estimate_s(7), WORST_KNOWN_PULL_S)

    def test_a_measured_ok_machine_uses_its_own_pull_time(self):
        board = Scoreboard({})
        board.record(rec(machine_id=144253, pull_s=35.0))
        self.assertEqual(board.ready_estimate_s(144253), 35.0)

    def test_a_failed_machine_is_not_trusted_for_its_time_even_after_the_ttl(self):
        board = Scoreboard({})
        board.record(rec(machine_id=2, pull_s=481.0, outcome="slow_pull"))
        self.assertEqual(board.ready_estimate_s(2), WORST_KNOWN_PULL_S)

    def test_a_slow_machine_is_blacklisted_until_the_ttl_passes(self):
        board = Scoreboard({})
        board.record(rec(machine_id=2, outcome="slow_pull", measured_at=1000.0))
        self.assertTrue(board.is_blacklisted(2, now=1000.0 + BLACKLIST_TTL_S - 1))
        self.assertFalse(board.is_blacklisted(2, now=1000.0 + BLACKLIST_TTL_S))

    def test_an_ok_machine_is_never_blacklisted(self):
        board = Scoreboard({})
        board.record(rec(machine_id=3, outcome="ok", measured_at=0.0))
        self.assertFalse(board.is_blacklisted(3, now=10.0))

    def test_an_unknown_machine_is_not_blacklisted(self):
        self.assertFalse(Scoreboard({}).is_blacklisted(9, now=1.0))

    def test_known_good_is_fastest_first_ok_only_and_limited(self):
        board = Scoreboard({})
        board.record(rec(machine_id=1, pull_s=300.0))
        board.record(rec(machine_id=2, pull_s=35.0))
        board.record(rec(machine_id=3, pull_s=100.0))
        board.record(rec(machine_id=4, pull_s=10.0, outcome="failed"))
        board.record(rec(machine_id=5, pull_s=None, outcome="ok"))
        self.assertEqual(board.known_good(limit=2), [2, 3])

    def test_a_newer_measurement_replaces_the_older_one(self):
        board = Scoreboard({})
        board.record(rec(machine_id=1, pull_s=300.0, measured_at=1.0))
        board.record(rec(machine_id=1, pull_s=40.0, measured_at=2.0))
        self.assertEqual(board.get(1).pull_s, 40.0)


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "vast-machines.json"

    def test_roundtrip(self):
        board = Scoreboard({})
        board.record(rec(machine_id=144253, pull_s=35.0, model_mbps=241.7, gb=34.4))
        save_board(self.path, board)
        again = load_board(self.path)
        self.assertEqual(again.get(144253), board.get(144253))

    def test_a_missing_file_is_an_empty_board(self):
        self.assertEqual(load_board(self.path).records(), {})

    def test_a_corrupt_file_is_an_empty_board_not_a_crash(self):
        # A rent that crashes on a half-written history would block the fallback exactly when
        # RunPod is out of stock. Unreadable means "no history".
        self.path.write_text('{"1": {"machine_id": 1,', encoding="utf-8")
        self.assertEqual(load_board(self.path).records(), {})

    def test_save_leaves_no_temp_file_behind(self):
        save_board(self.path, Scoreboard({}))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], [self.path.name])


class TestGitignore(unittest.TestCase):
    def test_the_scoreboard_file_is_git_ignored(self):
        # It is per-machine state about rented hosts, not repo content, and the repo is public.
        out = subprocess.run(["git", "check-ignore", "-q", "batch/vast-machines.json"],
                             cwd=ROOT)
        self.assertEqual(out.returncode, 0, "batch/vast-machines.json is not git-ignored")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_vast_scoreboard.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib_ext.vast_scoreboard'`.

- [ ] **Step 3: Implement**

Create `scripts/batchlib_ext/vast_scoreboard.py`:

```python
"""What each Vast machine ACTUALLY did, not what it advertised.

Advertised bandwidth does not predict boot time: the Washington host advertised 1593 Mbps yet
pulled a ghcr layer at 15.9 MB/s and took 556 s to reach `running`; the Bulgaria host advertised
2038 Mbps and took 325 s; the same Bulgaria machine took 35 s the second time (its docker layer
cache survived the destroy). Two hosts is not a model, so this keeps a per-machine history and
lets measurements outrank the marketplace's own numbers (docs/gpu-pod.md#vast-ghcr).

Storage only, no policy about WHICH machine to rent — that lives in vast_select.py. Read like
lease.py: an unreadable file is "no history", never an exception, because a rent that crashes on
its own bookkeeping would block the fallback exactly when RunPod is out of stock.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

# Slowest create -> running ever measured (Washington US, 2026-09-19). A machine nobody has
# measured is scored as this, never optimistically.
WORST_KNOWN_PULL_S = 556.0
# How long a slow or failed machine stays out of the candidate list.
BLACKLIST_TTL_S = 24 * 3600.0


@dataclass(frozen=True)
class MachineRecord:
    machine_id: int
    pull_s: float | None       # create -> actual_status running; a lower bound when it timed out
    model_mbps: float | None   # measured model download throughput; filled by the model probe
    gb: float | None           # GB that measurement covered
    measured_at: float         # unix seconds
    outcome: str               # "ok" | "slow_pull" | "failed"


class Scoreboard:
    def __init__(self, records: dict[int, MachineRecord]):
        self._records: dict[int, MachineRecord] = dict(records)

    def records(self) -> dict[int, MachineRecord]:
        return dict(self._records)

    def get(self, machine_id: int) -> MachineRecord | None:
        return self._records.get(int(machine_id))

    def record(self, rec: MachineRecord) -> None:
        self._records[int(rec.machine_id)] = rec

    def is_blacklisted(self, machine_id: int, now: float,
                       ttl: float = BLACKLIST_TTL_S) -> bool:
        rec = self.get(machine_id)
        return rec is not None and rec.outcome != "ok" and (now - rec.measured_at) < ttl

    def ready_estimate_s(self, machine_id: int) -> float:
        rec = self.get(machine_id)
        if rec is not None and rec.outcome == "ok" and rec.pull_s is not None:
            return rec.pull_s
        return WORST_KNOWN_PULL_S

    def known_good(self, limit: int = 3) -> list[int]:
        good = [r for r in self._records.values()
                if r.outcome == "ok" and r.pull_s is not None]
        good.sort(key=lambda r: r.pull_s)
        return [r.machine_id for r in good[:limit]]


def load_board(path: Path) -> Scoreboard:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        records = {}
        for key, value in raw.items():
            records[int(key)] = MachineRecord(
                machine_id=int(value["machine_id"]),
                pull_s=None if value.get("pull_s") is None else float(value["pull_s"]),
                model_mbps=(None if value.get("model_mbps") is None
                            else float(value["model_mbps"])),
                gb=None if value.get("gb") is None else float(value["gb"]),
                measured_at=float(value["measured_at"]),
                outcome=str(value["outcome"]))
        return Scoreboard(records)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return Scoreboard({})


def save_board(path: Path, board: Scoreboard) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({str(k): asdict(v) for k, v in board.records().items()},
                              indent=2), encoding="utf-8")
    tmp.replace(path)   # atomic: a reader never sees a half-written history
```

In `.gitignore`, after the `batch/volume-migrate.launching.json` line, add:

```
# Per-machine history of Vast rentals (scripts/vast_rent.py): what each host actually did.
batch/vast-machines.json
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_vast_scoreboard.py' -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/batchlib_ext/vast_scoreboard.py scripts/tests/test_batch_vast_scoreboard.py .gitignore
git commit -m "$(cat <<'EOF'
vast: per-machine scoreboard of what each host actually did

Measured pull times outrank advertised bandwidth; unknown machines are scored as the slowest ever
seen; slow or failed machines are blacklisted for a day. The file is git-ignored.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 2: Filter and rank

**Files:**
- Create: `scripts/batchlib_ext/vast_select.py`
- Test: `scripts/tests/test_batch_vast_select.py`

**Interfaces:**
- Consumes: `Scoreboard`, `WORST_KNOWN_PULL_S` (Task 1).
- Produces:
  - `@dataclass(frozen=True) Criteria(max_dph: float, min_disk_bw: float, min_cpu_ghz: float, min_inet_mbps: float, max_down_usd_per_tb: float, skip_offers: frozenset[str] = frozenset())`
  - `@dataclass(frozen=True) Ranked(offer: dict, machine_id: int | None, ready_s: float, known: bool, bandwidth_usd: float, score: float)`
  - `reject_reason(offer: dict, c: Criteria, board: Scoreboard, now: float) -> str | None`
  - `rank(offers: list[dict], c: Criteria, board: Scoreboard, *, gb: float, now: float) -> tuple[list[Ranked], collections.Counter]` (Counter of rejection reason → count)
  - `dedupe(offers: list[dict]) -> list[dict]` (first occurrence per `id` wins)
  - `explain_rejections(rejected: Counter, total: int) -> str`
  - `format_table(ranked: list[Ranked], top: int = 5) -> str`

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_batch_vast_select.py`:

```python
import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib_ext.vast_scoreboard import (BLACKLIST_TTL_S, WORST_KNOWN_PULL_S,
                                          MachineRecord, Scoreboard)
from batchlib_ext.vast_select import (Criteria, dedupe, explain_rejections,
                                      format_table, rank, reject_reason)

NOW = 10_000.0


def offer(**over):
    """A real-shaped RTX 5090 offer (field names and magnitudes from a 2026-09-19 search)."""
    base = dict(id=42230244, machine_id=58908, gpu_name="RTX 5090", num_gpus=1,
                dph_total=0.40, inet_down=1800.0, internet_down_cost_per_tb=2.0,
                disk_bw=3800.0, cpu_ghz=3.0, reliability2=0.99,
                geolocation="Bulgaria, BG", direct_port_count=12, rentable=True)
    base.update(over)
    return base


CRIT = Criteria(max_dph=0.60, min_disk_bw=3000.0, min_cpu_ghz=2.5,
                min_inet_mbps=1000.0, max_down_usd_per_tb=20.0)


def board_with(*records):
    board = Scoreboard({})
    for r in records:
        board.record(r)
    return board


class TestRejectReason(unittest.TestCase):
    def test_a_good_offer_qualifies(self):
        self.assertIsNone(reject_reason(offer(), CRIT, Scoreboard({}), NOW))

    def test_each_criterion_rejects_with_its_own_reason(self):
        cases = [
            (dict(dph_total=0.61), "over price cap"),
            (dict(disk_bw=2999.0), "disk too slow"),
            (dict(cpu_ghz=2.4), "cpu too slow"),
            (dict(inet_down=999.0), "advertised bandwidth too low"),
            (dict(internet_down_cost_per_tb=20.5), "bandwidth too expensive"),
            (dict(internet_down_cost_per_tb=None), "bandwidth cost unknown"),
            (dict(direct_port_count=0), "no direct ports"),
            (dict(rentable=False), "not rentable"),
        ]
        for over, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(reject_reason(offer(**over), CRIT, Scoreboard({}), NOW),
                                 reason)

    def test_a_skipped_offer_is_rejected(self):
        crit = Criteria(0.60, 3000.0, 2.5, 1000.0, 20.0, skip_offers=frozenset({"42230244"}))
        self.assertEqual(reject_reason(offer(), crit, Scoreboard({}), NOW), "skipped")

    def test_a_blacklisted_machine_is_rejected_until_the_ttl_passes(self):
        bad = MachineRecord(58908, 481.0, None, None, measured_at=NOW - 10, outcome="slow_pull")
        self.assertEqual(reject_reason(offer(), CRIT, board_with(bad), NOW),
                         "machine blacklisted")
        later = NOW + BLACKLIST_TTL_S
        self.assertIsNone(reject_reason(offer(), CRIT, board_with(bad), later))

    def test_the_hungarian_flat_forty_dollars_per_tb_is_excluded_by_default(self):
        # A 50 GB boot is $2.00 there against a $0.07 boot at $1.37/TB.
        self.assertEqual(
            reject_reason(offer(internet_down_cost_per_tb=40.0), CRIT, Scoreboard({}), NOW),
            "bandwidth too expensive")


class TestRank(unittest.TestCase):
    def test_bandwidth_is_priced_per_gb_from_the_per_tb_rate(self):
        ranked, _ = rank([offer(internet_down_cost_per_tb=40.0)],
                         Criteria(0.60, 0, 0, 0, 100.0), Scoreboard({}), gb=50.0, now=NOW)
        self.assertAlmostEqual(ranked[0].bandwidth_usd, 2.00)
        ranked, _ = rank([offer(internet_down_cost_per_tb=1.37)],
                         Criteria(0.60, 0, 0, 0, 100.0), Scoreboard({}), gb=50.0, now=NOW)
        self.assertAlmostEqual(ranked[0].bandwidth_usd, 0.0685)

    def test_an_unknown_machine_is_scored_at_the_slowest_ever_seen(self):
        ranked, _ = rank([offer(dph_total=0.36, internet_down_cost_per_tb=0.0)], CRIT,
                         Scoreboard({}), gb=50.0, now=NOW)
        self.assertEqual(ranked[0].ready_s, WORST_KNOWN_PULL_S)
        self.assertFalse(ranked[0].known)
        self.assertAlmostEqual(ranked[0].score, 0.36 * WORST_KNOWN_PULL_S / 3600.0)

    def test_a_known_fast_machine_beats_a_cheaper_unknown_one(self):
        # The point of measuring: 0.60 $/h with a 35 s warm start is cheaper to GET READY than
        # 0.40 $/h that might take 556 s.
        cheap_unknown = offer(id=1, machine_id=1, dph_total=0.40)
        dear_known = offer(id=2, machine_id=2, dph_total=0.60)
        fast = MachineRecord(2, 35.0, None, None, measured_at=NOW - 60, outcome="ok")
        ranked, _ = rank([cheap_unknown, dear_known], CRIT, board_with(fast), gb=50.0, now=NOW)
        self.assertEqual([r.offer["id"] for r in ranked], [2, 1])
        self.assertTrue(ranked[0].known)
        self.assertEqual(ranked[0].ready_s, 35.0)

    def test_a_tie_goes_to_the_cheaper_machine(self):
        a = offer(id=1, machine_id=1, dph_total=0.50, internet_down_cost_per_tb=0.0)
        b = offer(id=2, machine_id=2, dph_total=0.45, internet_down_cost_per_tb=0.0)
        fast = lambda mid: MachineRecord(mid, 100.0, None, None, NOW, "ok")
        ranked, _ = rank([a, b], CRIT, board_with(fast(1), fast(2)), gb=0.0, now=NOW)
        self.assertEqual([r.offer["id"] for r in ranked], [2, 1])

    def test_rejections_are_counted_by_reason(self):
        offers = [offer(id=1), offer(id=2, dph_total=0.90), offer(id=3, dph_total=0.95),
                  offer(id=4, disk_bw=100.0)]
        ranked, rejected = rank(offers, CRIT, Scoreboard({}), gb=50.0, now=NOW)
        self.assertEqual([r.offer["id"] for r in ranked], [1])
        self.assertEqual(rejected, Counter({"over price cap": 2, "disk too slow": 1}))

    def test_an_offer_without_a_machine_id_is_ranked_as_unknown(self):
        ranked, _ = rank([offer(machine_id=None)], CRIT, Scoreboard({}), gb=10.0, now=NOW)
        self.assertIsNone(ranked[0].machine_id)
        self.assertEqual(ranked[0].ready_s, WORST_KNOWN_PULL_S)


class TestHelpers(unittest.TestCase):
    def test_dedupe_keeps_the_first_row_per_offer_id(self):
        rows = [offer(id=1, dph_total=0.4), offer(id=2), offer(id=1, dph_total=0.9)]
        out = dedupe(rows)
        self.assertEqual([o["id"] for o in out], [1, 2])
        self.assertEqual(out[0]["dph_total"], 0.4)

    def test_explain_names_every_reason_and_the_knobs(self):
        text = explain_rejections(Counter({"over price cap": 12, "disk too slow": 3}), 40)
        self.assertIn("0 of 40", text)
        self.assertIn("12 over price cap", text)
        self.assertIn("MAX_DPH", text)
        self.assertIn("VAST_MIN_INET_MBPS", text)

    def test_explain_an_empty_search(self):
        self.assertIn("no offers matched", explain_rejections(Counter(), 0))

    def test_table_marks_measured_versus_unmeasured(self):
        fast = MachineRecord(58908, 35.0, None, None, NOW, "ok")
        ranked, _ = rank([offer(), offer(id=2, machine_id=2)], CRIT, board_with(fast),
                         gb=50.0, now=NOW)
        table = format_table(ranked)
        self.assertIn("measured", table)
        self.assertIn("unmeasured", table)
        self.assertIn("42230244", table)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_vast_select.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'batchlib_ext.vast_select'`.

- [ ] **Step 3: Implement**

Create `scripts/batchlib_ext/vast_select.py`:

```python
"""Which Vast offers qualify, and in what order — pure functions, no I/O, no clock.

Rank by what it costs to GET READY, not by $/hour alone (spec §3.2):

    score = dph_total * ready_s / 3600  +  gb_to_download * internet_down_cost_per_tb / 1000

`ready_s` is the machine's own measured create -> running time when the scoreboard has one, and
the slowest ever measured (556 s) when it does not. Bandwidth is billed per TB: a 50 GB boot is
$2.00 at the Hungarian flat $40/TB and $0.07 at Bulgaria's $1.37/TB, so an expensive-bandwidth host
is excluded up front rather than discovered on the invoice (docs/gpu-pod.md#vast-ghcr).

`inet_down` is a FILTER here, not a predictor: the Washington host advertised 1593 Mbps and pulled
a ghcr layer at 15.9 MB/s. It keeps obviously slow lines out; the scoreboard does the real work.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .vast_scoreboard import Scoreboard


@dataclass(frozen=True)
class Criteria:
    max_dph: float
    min_disk_bw: float
    min_cpu_ghz: float
    min_inet_mbps: float
    max_down_usd_per_tb: float
    skip_offers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Ranked:
    offer: dict
    machine_id: int | None
    ready_s: float
    known: bool            # True when ready_s is a measurement, False when it is the worst case
    bandwidth_usd: float
    score: float


def reject_reason(offer: dict, c: Criteria, board: Scoreboard, now: float) -> str | None:
    """The first reason this offer does not qualify; None means it does."""
    if str(offer.get("id")) in c.skip_offers:
        return "skipped"
    if not offer.get("rentable", True):
        return "not rentable"
    dph = offer.get("dph_total")
    if dph is None or dph > c.max_dph:
        return "over price cap"
    if (offer.get("disk_bw") or 0) < c.min_disk_bw:
        return "disk too slow"
    if (offer.get("cpu_ghz") or 0) < c.min_cpu_ghz:
        return "cpu too slow"
    if (offer.get("inet_down") or 0) < c.min_inet_mbps:
        return "advertised bandwidth too low"
    cost = offer.get("internet_down_cost_per_tb")
    if cost is None:
        return "bandwidth cost unknown"        # unknown is assumed expensive, never free
    if cost > c.max_down_usd_per_tb:
        return "bandwidth too expensive"
    if not (offer.get("direct_port_count") or 0):
        return "no direct ports"               # `create --direct` needs them
    machine_id = offer.get("machine_id")
    if machine_id is not None and board.is_blacklisted(int(machine_id), now):
        return "machine blacklisted"
    return None


def rank(offers: list[dict], c: Criteria, board: Scoreboard, *, gb: float,
         now: float) -> tuple[list[Ranked], Counter]:
    rejected: Counter = Counter()
    ranked: list[Ranked] = []
    for o in offers:
        reason = reject_reason(o, c, board, now)
        if reason is not None:
            rejected[reason] += 1
            continue
        raw_id = o.get("machine_id")
        machine_id = None if raw_id is None else int(raw_id)
        record = None if machine_id is None else board.get(machine_id)
        known = record is not None and record.outcome == "ok" and record.pull_s is not None
        ready_s = board.ready_estimate_s(machine_id) if machine_id is not None \
            else board.ready_estimate_s(-1)
        bandwidth_usd = gb * float(o["internet_down_cost_per_tb"]) / 1000.0
        score = float(o["dph_total"]) * ready_s / 3600.0 + bandwidth_usd
        ranked.append(Ranked(o, machine_id, ready_s, known, bandwidth_usd, score))
    ranked.sort(key=lambda r: (r.score, r.offer["dph_total"]))
    return ranked, rejected


def dedupe(offers: list[dict]) -> list[dict]:
    seen: set = set()
    out = []
    for o in offers:
        key = o.get("id")
        if key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


def explain_rejections(rejected: Counter, total: int) -> str:
    if total == 0:
        return "no offers matched the search at all — loosen GPU= or DISK="
    parts = ", ".join(f"{n} {reason}" for reason, n in rejected.most_common())
    return (f"0 of {total} offers qualify ({parts}).\n"
            "  Knobs (env or .env): MAX_DPH, MIN_DISK_BW, MIN_CPU_GHZ, VAST_MIN_INET_MBPS, "
            "VAST_MAX_DOWN_USD_PER_TB; SKIP=<offer ids> to exclude one.")


def format_table(ranked: list[Ranked], top: int = 5) -> str:
    lines = []
    for r in ranked[:top]:
        o = r.offer
        gb = (o.get("gpu_ram") or 0) / 1024
        lines.append(
            f"  id={o.get('id')!s:<10} ${o.get('dph_total', 0):.3f}/hr  "
            f"ready≈{r.ready_s:.0f}s ({'measured' if r.known else 'unmeasured'})  "
            f"bw ${r.bandwidth_usd:.2f}  score ${r.score:.3f}  {o.get('gpu_name')} {gb:.0f}GB  "
            f"disk={o.get('disk_bw') or 0:.0f}MB/s  down={o.get('inet_down') or 0:.0f}Mbps  "
            f"{o.get('geolocation', '?')}")
    return "\n".join(lines)
```

(`board.ready_estimate_s(-1)` returns the worst-case for a machine that has no id — the scoreboard has no record for `-1`.)

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_vast_select.py' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/batchlib_ext/vast_select.py scripts/tests/test_batch_vast_select.py
git commit -m "$(cat <<'EOF'
vast: rank offers by time-to-ready and total cost, not $/h

score = dph * ready_s/3600 + GB * $/TB/1000, with measured ready time when the scoreboard has it
and the slowest ever seen when it does not. Expensive-bandwidth, slow-disk and blacklisted hosts
are rejected with a named reason.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 3: The rent function

**Files:**
- Create: `scripts/vast_rent.py`
- Test: `scripts/tests/test_batch_vast_rent.py`

**Interfaces:**
- Consumes: `Criteria`, `Ranked`, `rank`, `dedupe`, `explain_rejections`, `format_table` (Task 2); `Scoreboard`, `MachineRecord`, `load_board`, `save_board` (Task 1); `VastCtl` (Plan 1); `batchlib.config.env_get/env_set`; `pod_watchdog`'s `GRACE_MIN` via `batchlib_ext.watchdog`.
- Produces (module `vast_rent`, importable as `import vast_rent` with `scripts/` on `sys.path`):
  - constants `VAST_LABEL = "motion-transfer"`, `DEFAULT_PULL_DEADLINE_S = 480`, `MAX_PULL_RETRIES = 2`, `MAX_CREATE_FAILURES = 6`, `POLL_S = 10`
  - `class RentError(RuntimeError)`, `class NoOffers(RentError)`
  - `class VastApi(Protocol)`: `search_offers(query: str) -> list[dict]`; `create_instance(offer_id, *, image: str, disk_gb: int, label: str) -> str`; `instance_status(instance_id: str) -> dict`; `destroy_instance(instance_id: str) -> None`; `ssh_url(instance_id: str) -> str`
  - `class RealVastApi` implementing it over the `vastai` CLI
  - `@dataclass(frozen=True) RentConfig(gpu: str, disk_gb: int, image: str, reliability: float, criteria: Criteria, gb: float, pull_deadline_s: float, pin: str | None = None)` with property `query -> str`
  - `@dataclass(frozen=True) RentResult(instance_id: str | None, chosen: Ranked, ranked: list[Ranked], pull_s: float | None)`
  - `rent(api, cfg, board, *, confirm, now=time.time, sleep=time.sleep, log=..., on_created=..., on_released=..., persist=...) -> RentResult`
  - `parse_ssh_url(text: str) -> tuple[str, str] | None`
  - `make_api() -> VastApi`, `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_batch_vast_rent.py`:

```python
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import vast_rent
from batchlib_ext.vast_scoreboard import MachineRecord, Scoreboard, load_board
from batchlib_ext.vast_select import Criteria
from batchlib_ext.watchdog import DESTROYABLE_NAMES, GRACE_MIN


def offer(id, machine_id, dph=0.40, **over):
    base = dict(id=id, machine_id=machine_id, gpu_name="RTX 5090", dph_total=dph,
                inet_down=1800.0, internet_down_cost_per_tb=2.0, disk_bw=3800.0, cpu_ghz=3.0,
                direct_port_count=12, rentable=True, geolocation="Bulgaria, BG")
    base.update(over)
    return base


CRIT = Criteria(0.60, 3000.0, 2.5, 1000.0, 20.0)


def cfg(**over):
    base = dict(gpu="RTX_5090", disk_gb=120, image="ghcr.io/x/motion-prebuilt:t",
                reliability=0.95, criteria=CRIT, gb=50.0, pull_deadline_s=480.0)
    base.update(over)
    return vast_rent.RentConfig(**base)


class FakeVast:
    """Scripted Vast. `statuses[instance_id]` is the sequence of actual_status values returned
    one per poll (the last repeats). `create_fail` lists offer ids whose create raises."""

    def __init__(self, offers, statuses=None, create_fail=(), destroy_fail=()):
        self.offers = offers
        self.statuses = statuses or {}
        self.create_fail = set(create_fail)
        self.destroy_fail = set(destroy_fail)
        self.queries, self.created, self.destroyed = [], [], []
        self._polls = {}
        self._n = 0

    def search_offers(self, query):
        self.queries.append(query)
        if query.startswith("machine_id="):
            mid = int(query.split()[0].split("=")[1])
            return [o for o in self.offers if o["machine_id"] == mid]
        return list(self.offers)

    def create_instance(self, offer_id, *, image, disk_gb, label):
        if offer_id in self.create_fail:
            raise vast_rent.RentError(f"offer {offer_id} is gone")
        self._n += 1
        iid = f"i{self._n}"
        self.created.append(dict(offer=offer_id, iid=iid, image=image, disk=disk_gb,
                                 label=label))
        return iid

    def instance_status(self, instance_id):
        seq = self.statuses.get(instance_id, ["running"])
        i = self._polls.get(instance_id, 0)
        self._polls[instance_id] = i + 1
        return {"actual_status": seq[min(i, len(seq) - 1)]}

    def destroy_instance(self, instance_id):
        if instance_id in self.destroy_fail:
            raise vast_rent.RentError("destroy refused")
        self.destroyed.append(instance_id)

    def ssh_url(self, instance_id):
        return "ssh://root@1.2.3.4:40022"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def run_rent(api, *, confirm=True, board=None, config=None, clock=None, **kw):
    clock = clock or Clock()
    board = board if board is not None else Scoreboard({})
    events = {"created": [], "released": [], "persisted": 0}
    result = vast_rent.rent(
        api, config or cfg(), board, confirm=confirm, now=clock.now, sleep=clock.sleep,
        log=lambda m: None,
        on_created=events["created"].append, on_released=events["released"].append,
        persist=lambda: events.__setitem__("persisted", events["persisted"] + 1), **kw)
    return result, events, board, clock


class TestDryRun(unittest.TestCase):
    def test_a_dry_run_creates_nothing_and_returns_the_ranking(self):
        api = FakeVast([offer(1, 11, 0.50), offer(2, 22, 0.40)])
        result, events, _, _ = run_rent(api, confirm=False)
        self.assertEqual(api.created, [])
        self.assertIsNone(result.instance_id)
        self.assertEqual(result.chosen.offer["id"], 2)   # equal readiness -> cheaper first
        self.assertEqual(events["created"], [])

    def test_no_qualifying_offer_raises_with_the_reasons(self):
        api = FakeVast([offer(1, 11, dph=0.99)])
        with self.assertRaises(vast_rent.NoOffers) as cm:
            run_rent(api, confirm=False)
        self.assertIn("over price cap", str(cm.exception))

    def test_a_pinned_offer_must_still_qualify(self):
        api = FakeVast([offer(1, 11), offer(2, 22)])
        result, *_ = run_rent(api, confirm=False, config=cfg(pin="1"))
        self.assertEqual(result.chosen.offer["id"], 1)
        with self.assertRaises(vast_rent.NoOffers):
            run_rent(api, confirm=False, config=cfg(pin="999"))

    def test_known_good_machines_are_queried_directly_by_machine_id(self):
        # `search offers` returns a random ~40-row sample; machine_id= queries are deterministic.
        board = Scoreboard({})
        board.record(MachineRecord(144253, 35.0, None, None, 1.0, "ok"))
        api = FakeVast([offer(1, 144253)])
        run_rent(api, confirm=False, board=board)
        self.assertTrue(any(q.startswith("machine_id=144253") for q in api.queries), api.queries)

    def test_the_base_query_carries_gpu_disk_and_reliability(self):
        api = FakeVast([offer(1, 11)])
        run_rent(api, confirm=False)
        self.assertEqual(api.queries[0],
                         "gpu_name=RTX_5090 num_gpus=1 disk_space>=120 reliability>0.95 "
                         "rentable=true")


class TestRent(unittest.TestCase):
    def test_success_creates_with_the_label_records_the_pull_and_returns(self):
        api = FakeVast([offer(1, 11)], statuses={"i1": ["loading", "loading", "running"]})
        result, events, board, clock = run_rent(api)
        self.assertEqual(result.instance_id, "i1")
        self.assertEqual(api.created[0]["label"], "motion-transfer")
        self.assertEqual(api.created[0]["image"], "ghcr.io/x/motion-prebuilt:t")
        self.assertEqual(api.created[0]["disk"], 120)
        self.assertEqual(events["created"], ["i1"])
        self.assertEqual(events["released"], [])
        rec = board.get(11)
        self.assertEqual(rec.outcome, "ok")
        self.assertEqual(rec.pull_s, result.pull_s)
        self.assertEqual(result.pull_s, 2 * vast_rent.POLL_S)
        self.assertGreaterEqual(events["persisted"], 1)

    def test_a_pull_past_the_deadline_is_destroyed_blacklisted_and_the_next_machine_tried(self):
        api = FakeVast([offer(1, 11, 0.40), offer(2, 22, 0.45)],
                       statuses={"i1": ["loading"], "i2": ["running"]})
        result, events, board, clock = run_rent(api)
        self.assertEqual(api.destroyed, ["i1"])
        self.assertEqual(events["released"], ["i1"])
        self.assertEqual(board.get(11).outcome, "slow_pull")
        self.assertTrue(board.is_blacklisted(11, now=clock.now()))
        self.assertEqual(result.instance_id, "i2")
        self.assertEqual(events["created"], ["i1", "i2"])

    def test_at_most_two_retries_then_a_clear_failure(self):
        offers = [offer(i, i * 10, 0.40 + i / 100) for i in range(1, 6)]
        api = FakeVast(offers, statuses={f"i{n}": ["loading"] for n in range(1, 6)})
        with self.assertRaises(vast_rent.RentError) as cm:
            run_rent(api)
        self.assertEqual(len(api.created), 3)         # the first pull plus MAX_PULL_RETRIES
        self.assertEqual(api.destroyed, ["i1", "i2", "i3"])
        self.assertIn("3", str(cm.exception))

    def test_a_vanished_offer_does_not_cost_a_pull_retry(self):
        # Offers disappear between search and create (measured: pinning failed ~3 of 4 times).
        api = FakeVast([offer(1, 11), offer(2, 22), offer(3, 33)], create_fail={1, 2})
        result, *_ = run_rent(api)
        self.assertEqual(result.instance_id, "i1")
        self.assertEqual(api.created[0]["offer"], 3)

    def test_too_many_create_failures_give_up(self):
        offers = [offer(i, i * 10, 0.40 + i / 100) for i in range(1, 9)]
        api = FakeVast(offers, create_fail={o["id"] for o in offers})
        with self.assertRaises(vast_rent.RentError):
            run_rent(api)
        self.assertEqual(api.created, [])

    def test_an_exited_container_is_a_failure_not_a_wait(self):
        api = FakeVast([offer(1, 11), offer(2, 22, 0.45)],
                       statuses={"i1": ["exited"], "i2": ["running"]})
        result, _, board, clock = run_rent(api)
        self.assertEqual(board.get(11).outcome, "failed")
        self.assertEqual(result.instance_id, "i2")

    def test_an_instance_that_cannot_be_destroyed_is_reported_loudly_with_its_id(self):
        api = FakeVast([offer(1, 11), offer(2, 22)], statuses={"i1": ["loading"]},
                       destroy_fail={"i1"})
        with self.assertRaises(vast_rent.RentError) as cm:
            run_rent(api)
        self.assertIn("i1", str(cm.exception))
        self.assertIn("STILL BILLING", str(cm.exception))
        self.assertEqual(len(api.created), 1, "kept renting while an instance was undestroyed")

    def test_an_interrupt_while_waiting_destroys_the_new_instance(self):
        api = FakeVast([offer(1, 11)], statuses={"i1": ["loading"]})
        clock = Clock()

        def boom(_s):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            vast_rent.rent(api, cfg(), Scoreboard({}), confirm=True, now=clock.now,
                           sleep=boom, log=lambda m: None, on_created=lambda i: None,
                           on_released=lambda i: None, persist=lambda: None)
        self.assertEqual(api.destroyed, ["i1"])

    def test_a_transient_status_error_is_tolerated(self):
        api = FakeVast([offer(1, 11)])
        real = api.instance_status
        calls = {"n": 0}

        def flaky(iid):
            calls["n"] += 1
            if calls["n"] == 1:
                raise vast_rent.RentError("api hiccup")
            return real(iid)

        api.instance_status = flaky
        result, *_ = run_rent(api)
        self.assertEqual(result.instance_id, "i1")

    def test_the_pull_deadline_leaves_room_inside_the_watchdog_grace(self):
        # Tier 3 destroys an unleased labelled instance GRACE_MIN minutes after it first sees it.
        # The instance we keep must be `running` and leased before that, so the deadline plus a
        # minute of bookkeeping has to stay under it.
        self.assertLess(vast_rent.DEFAULT_PULL_DEADLINE_S + 60, GRACE_MIN * 60)

    def test_the_label_is_one_tier_three_may_destroy(self):
        self.assertIn(vast_rent.VAST_LABEL, DESTROYABLE_NAMES)


class TestParseSshUrl(unittest.TestCase):
    def test_the_usual_shape(self):
        self.assertEqual(vast_rent.parse_ssh_url("ssh://root@1.2.3.4:40022"),
                         ("1.2.3.4", "40022"))

    def test_trailing_newline_and_a_preceding_warning_line(self):
        self.assertEqual(vast_rent.parse_ssh_url("Welcome to vast.ai\nssh://root@h.example:2222\n"),
                         ("h.example", "2222"))

    def test_no_scheme_or_no_user(self):
        self.assertEqual(vast_rent.parse_ssh_url("root@1.2.3.4:22"), ("1.2.3.4", "22"))
        self.assertEqual(vast_rent.parse_ssh_url("1.2.3.4:22"), ("1.2.3.4", "22"))

    def test_garbage_is_none(self):
        for text in ("", "no instance", "ssh://root@host"):
            with self.subTest(text=text):
                self.assertIsNone(vast_rent.parse_ssh_url(text))


class TestRealVastApi(unittest.TestCase):
    def _run(self, stdout="", rc=0, stderr=""):
        return mock.patch.object(vast_rent.subprocess, "run",
                                 return_value=mock.Mock(returncode=rc, stdout=stdout,
                                                        stderr=stderr))

    def test_create_uses_the_safety_flags_and_returns_the_new_contract(self):
        with self._run(json.dumps({"success": True, "new_contract": 51518664})) as run:
            iid = vast_rent.RealVastApi().create_instance(
                42230244, image="img:t", disk_gb=120, label="motion-transfer")
        argv = run.call_args[0][0]
        self.assertEqual(iid, "51518664")
        self.assertEqual(argv[:4], ["vastai", "create", "instance", "42230244"])
        for flag in ("--cancel-unavail", "--direct", "--ssh", "--raw"):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--label") + 1], "motion-transfer")
        self.assertEqual(argv[argv.index("--image") + 1], "img:t")
        self.assertEqual(argv[argv.index("--disk") + 1], "120")

    def test_create_failure_and_missing_id_raise_rent_error(self):
        with self._run(stderr="offer no longer available", rc=1):
            with self.assertRaises(vast_rent.RentError) as cm:
                vast_rent.RealVastApi().create_instance(1, image="i", disk_gb=1, label="l")
        self.assertIn("no longer available", str(cm.exception))
        with self._run(json.dumps({"success": False})):
            with self.assertRaises(vast_rent.RentError):
                vast_rent.RealVastApi().create_instance(1, image="i", disk_gb=1, label="l")

    def test_search_passes_the_query_as_one_argument_and_parses_a_list(self):
        with self._run(json.dumps([{"id": 1}])) as run:
            out = vast_rent.RealVastApi().search_offers("gpu_name=RTX_5090 rentable=true")
        self.assertEqual(out, [{"id": 1}])
        self.assertEqual(run.call_args[0][0][:4],
                         ["vastai", "search", "offers", "gpu_name=RTX_5090 rentable=true"])

    def test_search_rejects_non_list_output(self):
        with self._run(json.dumps({"error": "x"})):
            with self.assertRaises(vast_rent.RentError):
                vast_rent.RealVastApi().search_offers("q")

    def test_a_missing_binary_is_a_rent_error(self):
        with mock.patch.object(vast_rent.subprocess, "run", side_effect=FileNotFoundError("vastai")):
            with self.assertRaises(vast_rent.RentError):
                vast_rent.RealVastApi().search_offers("q")

    def test_status_reads_show_instance_and_is_empty_on_failure(self):
        with self._run(json.dumps({"actual_status": "running"})):
            self.assertEqual(vast_rent.RealVastApi().instance_status("1")["actual_status"],
                             "running")
        with self._run(rc=1):
            self.assertEqual(vast_rent.RealVastApi().instance_status("1"), {})

    def test_destroy_goes_through_vastctl_so_the_prompt_is_answered(self):
        with mock.patch.object(vast_rent.VastCtl, "destroy") as destroy:
            vast_rent.RealVastApi().destroy_instance("51518664")
        destroy.assert_called_once_with("51518664")


class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / ".env").write_text("GPU_INSTANCE_ID=old\n", encoding="utf-8")
        self.patches = [mock.patch.object(vast_rent, "ROOT", self.tmp),
                        mock.patch.object(vast_rent, "BOARD_PATH", self.tmp / "vast-machines.json")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def _main(self, api, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(vast_rent, "make_api", return_value=api), \
             redirect_stdout(out), redirect_stderr(err):
            rc = vast_rent.main(["--gpu", "RTX_5090", "--disk", "120", "--image", "img:t",
                                 "--max-dph", "0.60", "--reliability", "0.95",
                                 "--min-disk-bw", "3000", "--min-cpu-ghz", "2.5", *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_dry_run_prints_the_shortlist_and_only_the_offer_id_on_stdout(self):
        rc, out, err = self._main(FakeVast([offer(7, 70)]))
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "7")
        self.assertIn("unmeasured", err)
        self.assertIn("CONFIRM=yes", err)

    def test_confirm_prints_only_the_instance_id_and_writes_it_to_env_and_the_board(self):
        rc, out, _ = self._main(FakeVast([offer(7, 70)]), "--confirm")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "i1")
        self.assertIn("GPU_INSTANCE_ID=i1", (self.tmp / ".env").read_text(encoding="utf-8"))
        self.assertEqual(load_board(self.tmp / "vast-machines.json").get(70).outcome, "ok")

    def test_no_offers_is_exit_1_with_the_reason_on_stderr(self):
        rc, out, err = self._main(FakeVast([offer(7, 70, dph=0.99)]))
        self.assertEqual(rc, 1)
        self.assertEqual(out, "")
        self.assertIn("over price cap", err)

    def test_a_failed_rent_leaves_no_stale_instance_id_in_env(self):
        # "exited" fails at once, so the real clock and the real sleep are never needed here.
        api = FakeVast([offer(7, 70)], statuses={"i1": ["exited"]})
        rc, _, _ = self._main(api, "--confirm")
        self.assertEqual(rc, 1)
        self.assertEqual(api.destroyed, ["i1"])
        self.assertNotIn("GPU_INSTANCE_ID=i1", (self.tmp / ".env").read_text(encoding="utf-8"))

    def test_ssh_target_prints_host_and_port(self):
        rc, out, _ = self._main(FakeVast([]), "--ssh-target", "51518664")
        self.assertEqual((rc, out.strip()), (0, "1.2.3.4 40022"))

    def test_ssh_target_exits_1_when_the_url_is_unparseable(self):
        api = FakeVast([])
        api.ssh_url = lambda iid: "not an address"
        rc, out, _ = self._main(api, "--ssh-target", "1")
        self.assertEqual((rc, out), (1, ""))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_vast_rent.py' -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vast_rent'`.

- [ ] **Step 3: Implement**

Create `scripts/vast_rent.py`:

```python
#!/usr/bin/env python3
"""Rent a Vast.ai instance: rank by time-to-ready and cost, create, wait, give up on a slow pull.

    python3 scripts/vast_rent.py --gpu RTX_5090 --disk 120 --image IMG …            # dry run
    python3 scripts/vast_rent.py … --confirm                                        # rents
    python3 scripts/vast_rent.py --ssh-target <instance id>                         # "host port"

Called by pod-provision.sh's Vast branch. Prints ONLY the offer id (dry run) or the instance id
(--confirm) on stdout; everything human-readable goes to stderr.

Safety properties, each pinned by a test:
- every instance carries --label motion-transfer (the name the watchdog's tier 3 may destroy) and
  is created with --cancel-unavail (an error instead of a stopped instance that still bills);
- an instance abandoned for a slow pull is destroyed and the destroy is CHECKED — if it cannot be
  destroyed we stop renting and say "STILL BILLING" with its id;
- any exception (Ctrl-C included) between create and success destroys the new instance;
- GPU_INSTANCE_ID in .env is written the moment an instance exists and cleared when it is
  abandoned, so a stale id can never be read into a lease.
Search returns a random ~40-row sample (measured 2026-09-19), so known-good machines are also
queried by machine_id, which is deterministic.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.config import env_get, env_set
from batchlib_ext.podctl import VastCtl
from batchlib_ext.vast_scoreboard import (MachineRecord, Scoreboard, load_board,
                                          save_board)
from batchlib_ext.vast_select import (Criteria, Ranked, dedupe, explain_rejections,
                                      format_table, rank)

ROOT = Path(__file__).resolve().parents[1]
BOARD_PATH = ROOT / "batch" / "vast-machines.json"

VAST_LABEL = "motion-transfer"           # == a member of batchlib_ext.watchdog.DESTROYABLE_NAMES
DEFAULT_PULL_DEADLINE_S = 8 * 60         # must stay < the watchdog's GRACE_MIN (10 min) with slack
MAX_PULL_RETRIES = 2                     # spec §3.2: at most 2 retries after the first pull
MAX_CREATE_FAILURES = 6                  # offers vanish between search and create
POLL_S = 10
FAILED_STATUSES = frozenset({"exited", "error", "offline", "unknown_error"})
KNOWN_GOOD_QUERIES = 3


class RentError(RuntimeError):
    pass


class NoOffers(RentError):
    pass


class VastApi(Protocol):
    def search_offers(self, query: str) -> list[dict]: ...
    def create_instance(self, offer_id, *, image: str, disk_gb: int, label: str) -> str: ...
    def instance_status(self, instance_id: str) -> dict: ...
    def destroy_instance(self, instance_id: str) -> None: ...
    def ssh_url(self, instance_id: str) -> str: ...


class RealVastApi:
    def _run(self, argv: list[str], timeout: int):
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RentError(f"could not run {argv[0]}: {exc}") from exc

    def search_offers(self, query: str) -> list[dict]:
        out = self._run(["vastai", "search", "offers", query, "-o", "dph+", "--raw"], 90)
        if out.returncode != 0:
            raise RentError(f"vastai search failed: {out.stderr.strip()} — is your API key set?")
        try:
            data = json.loads(out.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RentError(f"vastai search returned invalid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise RentError(f"vastai search returned {type(data).__name__}, not a list")
        return data

    def create_instance(self, offer_id, *, image: str, disk_gb: int, label: str) -> str:
        out = self._run(["vastai", "create", "instance", str(offer_id), "--image", image,
                         "--disk", str(disk_gb), "--ssh", "--direct", "--label", label,
                         "--cancel-unavail", "--raw"], 120)
        if out.returncode != 0:
            raise RentError(f"vastai create failed for offer {offer_id}: "
                            f"{(out.stderr or out.stdout).strip()}")
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            data = {}
        new_id = data.get("new_contract") or data.get("id")
        if not data.get("success", True) or not new_id:
            raise RentError(f"vastai create for offer {offer_id} returned no instance id: "
                            f"{out.stdout.strip()[:200]} — check 'vastai show instances-v1'")
        return str(new_id)

    def instance_status(self, instance_id: str) -> dict:
        try:
            out = self._run(["vastai", "show", "instance", str(instance_id), "--raw"], 60)
            if out.returncode != 0:
                return {}
            data = json.loads(out.stdout or "{}")
            return data if isinstance(data, dict) else {}
        except (RentError, json.JSONDecodeError):
            return {}

    def destroy_instance(self, instance_id: str) -> None:
        VastCtl().destroy(str(instance_id))

    def ssh_url(self, instance_id: str) -> str:
        out = self._run(["vastai", "ssh-url", str(instance_id)], 60)
        if out.returncode != 0:
            raise RentError(f"vastai ssh-url failed: {out.stderr.strip()}")
        return out.stdout


@dataclass(frozen=True)
class RentConfig:
    gpu: str
    disk_gb: int
    image: str
    reliability: float
    criteria: Criteria
    gb: float                       # GB this rental will download (image + models), prices bandwidth
    pull_deadline_s: float
    pin: str | None = None

    @property
    def query(self) -> str:
        return (f"gpu_name={self.gpu} num_gpus=1 disk_space>={self.disk_gb} "
                f"reliability>{self.reliability} rentable=true")


@dataclass(frozen=True)
class RentResult:
    instance_id: str | None
    chosen: Ranked
    ranked: list
    pull_s: float | None


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def gather_offers(api: VastApi, cfg: RentConfig, board: Scoreboard, log) -> list[dict]:
    offers = list(api.search_offers(cfg.query))
    for machine_id in board.known_good(limit=KNOWN_GOOD_QUERIES):
        try:
            offers += api.search_offers(f"machine_id={machine_id} rentable=true")
        except RentError as exc:
            log(f"machine_id={machine_id} lookup failed: {exc}")
    return dedupe(offers)


def _wait_running(api: VastApi, instance_id: str, deadline_s: float, *, now, sleep, log):
    start = now()
    while True:
        try:
            status = str((api.instance_status(instance_id) or {}).get("actual_status")
                         or "").lower()
        except RuntimeError as exc:
            status = ""
            log(f"status check failed (will retry): {exc}")
        elapsed = now() - start
        if status == "running":
            return "running", elapsed
        if status in FAILED_STATUSES:
            return "failed", elapsed
        if elapsed >= deadline_s:
            return "timeout", elapsed
        sleep(POLL_S)


def _abandon(api: VastApi, instance_id: str, on_released, log, *, best_effort: bool) -> None:
    try:
        api.destroy_instance(instance_id)
    except Exception as exc:
        if best_effort:
            log(f"could not destroy {instance_id} while unwinding: {exc}")
            return
        raise RentError(
            f"instance {instance_id} could NOT be destroyed ({exc}) — it is STILL BILLING. "
            f"Destroy it by hand: vastai destroy instance {instance_id}") from exc
    on_released(instance_id)


def _record(board: Scoreboard, machine_id: int | None, pull_s: float | None, outcome: str,
            when: float) -> None:
    if machine_id is None:
        return
    board.record(MachineRecord(machine_id=machine_id, pull_s=pull_s, model_mbps=None,
                               gb=None, measured_at=when, outcome=outcome))


def rent(api: VastApi, cfg: RentConfig, board: Scoreboard, *, confirm: bool,
         now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
         log: Callable[[str], None] = _stderr,
         on_created: Callable[[str], None] = lambda iid: None,
         on_released: Callable[[str], None] = lambda iid: None,
         persist: Callable[[], None] = lambda: None) -> RentResult:
    offers = gather_offers(api, cfg, board, log)
    ranked, rejected = rank(offers, cfg.criteria, board, gb=cfg.gb, now=now())
    if cfg.pin:
        ranked = [r for r in ranked if str(r.offer["id"]) == str(cfg.pin)]
        if not ranked:
            raise NoOffers(f"offer {cfg.pin} is not among the qualifying offers "
                           f"({len(offers)} searched)")
    if not ranked:
        raise NoOffers(explain_rejections(rejected, len(offers)))
    if not confirm:
        return RentResult(None, ranked[0], ranked, None)

    pulls = 0
    create_failures = 0
    for cand in ranked:
        if pulls >= 1 + MAX_PULL_RETRIES:
            break
        try:
            instance_id = api.create_instance(cand.offer["id"], image=cfg.image,
                                              disk_gb=cfg.disk_gb, label=VAST_LABEL)
        except RuntimeError as exc:
            create_failures += 1
            log(f"create failed for offer {cand.offer['id']}: {exc}")
            if create_failures >= MAX_CREATE_FAILURES:
                break
            continue
        pulls += 1
        on_created(instance_id)
        try:
            state, elapsed = _wait_running(api, instance_id, cfg.pull_deadline_s,
                                           now=now, sleep=sleep, log=log)
        except BaseException:
            _abandon(api, instance_id, on_released, log, best_effort=True)
            raise
        if state == "running":
            _record(board, cand.machine_id, elapsed, "ok", now())
            persist()
            return RentResult(instance_id, cand, ranked, elapsed)
        _abandon(api, instance_id, on_released, log, best_effort=False)
        _record(board, cand.machine_id, elapsed,
                "slow_pull" if state == "timeout" else "failed", now())
        persist()
        log(f"offer {cand.offer['id']} (machine {cand.machine_id}): {state} after "
            f"{elapsed:.0f}s — destroyed, trying the next machine")
    raise RentError(f"no machine reached 'running' within {cfg.pull_deadline_s:.0f}s "
                    f"after {pulls} pull attempt(s) and {create_failures} failed create(s)")


def parse_ssh_url(text: str) -> tuple[str, str] | None:
    """`vastai ssh-url` prints ssh://root@HOST:PORT (assumed; parsed tolerantly)."""
    for line in reversed([ln.strip() for ln in text.splitlines() if ln.strip()]):
        s = line[len("ssh://"):] if line.startswith("ssh://") else line
        if "@" in s:
            s = s.split("@", 1)[1]
        s = s.split("/")[0]
        host, sep, port = s.rpartition(":")
        if sep and host and port.isdigit() and " " not in host:
            return host, port
    return None


def make_api() -> VastApi:
    return RealVastApi()


def _cfg_get(key: str, default: str) -> str:
    return os.environ.get(key) or env_get(ROOT / ".env", key) or default


def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gpu")
    ap.add_argument("--disk", type=int)
    ap.add_argument("--image")
    ap.add_argument("--max-dph", type=float)
    ap.add_argument("--reliability", type=float)
    ap.add_argument("--min-disk-bw", type=float, default=3000.0)
    ap.add_argument("--min-cpu-ghz", type=float, default=2.5)
    ap.add_argument("--min-inet-mbps", type=float,
                    default=float(_cfg_get("VAST_MIN_INET_MBPS", "1000")))
    ap.add_argument("--max-down-usd-per-tb", type=float,
                    default=float(_cfg_get("VAST_MAX_DOWN_USD_PER_TB", "20")))
    ap.add_argument("--gb", type=float, default=float(_cfg_get("VAST_GB", "60")))
    ap.add_argument("--pull-deadline", type=float,
                    default=float(_cfg_get("VAST_PULL_DEADLINE_S", str(DEFAULT_PULL_DEADLINE_S))))
    ap.add_argument("--skip", default="")
    ap.add_argument("--offer", default="")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--ssh-target", metavar="INSTANCE_ID")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    api = make_api()

    if args.ssh_target:
        try:
            target = parse_ssh_url(api.ssh_url(args.ssh_target))
        except RentError as exc:
            _stderr(str(exc))
            return 1
        if target is None:
            return 1
        print(f"{target[0]} {target[1]}")
        return 0

    for required in ("gpu", "disk", "image", "max_dph", "reliability"):
        if getattr(args, required) is None:
            _stderr(f"missing --{required.replace('_', '-')}")
            return 2

    criteria = Criteria(
        max_dph=args.max_dph, min_disk_bw=args.min_disk_bw, min_cpu_ghz=args.min_cpu_ghz,
        min_inet_mbps=args.min_inet_mbps, max_down_usd_per_tb=args.max_down_usd_per_tb,
        skip_offers=frozenset(s.strip() for s in args.skip.split(",") if s.strip()))
    cfg = RentConfig(gpu=args.gpu, disk_gb=args.disk, image=args.image,
                     reliability=args.reliability, criteria=criteria, gb=args.gb,
                     pull_deadline_s=args.pull_deadline, pin=args.offer or None)
    board = load_board(BOARD_PATH)
    env_path = ROOT / ".env"
    _stderr(f"searching: {cfg.query}  (<= ${args.max_dph:.2f}/hr, ~{args.gb:.0f} GB to download)")
    try:
        result = rent(
            api, cfg, board, confirm=args.confirm,
            on_created=lambda iid: env_set(env_path, "GPU_INSTANCE_ID", iid),
            on_released=lambda iid: env_set(env_path, "GPU_INSTANCE_ID", ""),
            persist=lambda: save_board(BOARD_PATH, board))
    except RentError as exc:
        print(f"\033[31m ✗ \033[0m{exc}", file=sys.stderr)
        return 1

    if not args.confirm:
        _stderr(f"\n{len(result.ranked)} qualifying offer(s), best first:\n"
                f"{format_table(result.ranked)}\n\n"
                "Dry run — nothing rented. Read the shortlist, then:\n"
                "  CONFIRM=yes bash scripts/pod-provision.sh")
        print(result.chosen.offer["id"])
        return 0
    _stderr(f"rented — instance {result.instance_id} running after {result.pull_s:.0f}s "
            f"(saved to .env as GPU_INSTANCE_ID). Next: make gpu-wait")
    print(result.instance_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_vast_rent.py' -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/vast_rent.py scripts/tests/test_batch_vast_rent.py
git commit -m "$(cat <<'EOF'
vast: rent function — rank, create, wait, give up on a slow pull, never leave one billing

Creates with --label motion-transfer and --cancel-unavail; destroys and blacklists a machine whose
pull passes the deadline (at most 2 retries); checks the destroy and says STILL BILLING with the id
if it fails; destroys a new instance on any interrupt; keeps GPU_INSTANCE_ID in .env honest.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 4: `pod-provision.sh` uses the rent function

**Files:**
- Modify: `scripts/pod-provision.sh` (the Vast branch, from the line `# --- vast.ai branch — the validated default path.` to the end of the file)
- Modify: `scripts/tests/test_batch_provider_wiring.py` (`TestVastInstancesAreLabelled`)

**Interfaces:**
- Consumes: `scripts/vast_rent.py` CLI (Task 3): flags `--gpu --disk --image --max-dph --reliability --min-disk-bw --min-cpu-ghz [--offer ID] [--skip IDS] [--confirm]`; `vast_rent.VAST_LABEL`.
- Produces: the same operator interface as before — `bash scripts/pod-provision.sh` is a dry run, `CONFIRM=yes bash scripts/pod-provision.sh` rents, `GPU_INSTANCE_ID` is left in `.env`; `SKIP=` and `OFFER=` keep working.

- [ ] **Step 1: Update the label test first (it must fail against the current script)**

In `scripts/tests/test_batch_provider_wiring.py`, replace the body of `TestVastInstancesAreLabelled` with:

```python
class TestVastInstancesAreLabelled(unittest.TestCase):
    def test_the_rent_function_labels_with_a_name_tier_three_may_destroy(self):
        # Two hand-copied names that must agree: the label vast_rent.py puts on every instance
        # and watchdog.DESTROYABLE_NAMES. This is the gate the comment above DESTROYABLE_NAMES
        # says did not exist.
        import vast_rent
        self.assertIn(vast_rent.VAST_LABEL, DESTROYABLE_NAMES)

    def test_pod_provision_hands_the_vast_branch_to_the_rent_function(self):
        text = (ROOT / "scripts" / "pod-provision.sh").read_text(encoding="utf-8")
        self.assertIn("vast_rent.py", text)
        # The old cheapest-first search and the raw `vastai create` are gone from the script.
        self.assertNotIn("vastai create instance", text)
        self.assertNotIn("vastai search offers", text)
```

Add `sys.path.insert(0, str(ROOT / "scripts"))` is already at the top of that file (it inserts `ROOT / "scripts"`); keep it.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -k TestVastInstancesAreLabelled -v`
Expected: FAIL — `vast_rent.py` not found in pod-provision.sh.

- [ ] **Step 3: Implement**

In `scripts/pod-provision.sh`, delete everything from the line

```bash
# --- vast.ai branch — the validated default path. --------------------------------------------
```

to the end of the file (the search, the python selection heredoc, `CREATE=(…)`, the dry-run text, the rent, and the `GPU_INSTANCE_ID` write) and put this in its place:

```bash
# --- vast.ai branch — selection, rent, retry and cleanup live in scripts/vast_rent.py ----------
# It ranks by time-to-ready and total cost (not $/hour), remembers what each machine actually did
# (batch/vast-machines.json), abandons a slow pull, labels every instance `motion-transfer` (the
# name the watchdog may destroy) and writes GPU_INSTANCE_ID itself. Dry run unless CONFIRM=yes.
# Design: docs/superpowers/specs/2026-09-19-vast-fallback-design.md §3.2.
command -v vastai >/dev/null || die "vastai CLI not found:  pip install vastai  &&  vastai set api-key <key>"
command -v python3 >/dev/null || die "python3 needed to run scripts/vast_rent.py"

VAST_ARGS=(--gpu "$GPU" --disk "$DISK" --image "$IMAGE" --max-dph "$MAX_DPH"
           --reliability "$RELIABILITY" --min-disk-bw "$MIN_DISK_BW" --min-cpu-ghz "$MIN_CPU_GHZ")
[ -n "$OFFER" ] && VAST_ARGS+=(--offer "$OFFER")
[ -n "$SKIP" ] && VAST_ARGS+=(--skip "$SKIP")
[ "${CONFIRM:-}" = "yes" ] && VAST_ARGS+=(--confirm)

exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vast_rent.py" "${VAST_ARGS[@]}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -v`
Expected: PASS (all).

Run: `bash -n scripts/pod-provision.sh && echo syntax-ok`
Expected: `syntax-ok`

Run: `grep -n "vastai" scripts/pod-provision.sh`
Expected: only the two `command -v` / `die` lines and comments — no `vastai search` or `vastai create`.

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/pod-provision.sh scripts/tests/test_batch_provider_wiring.py
git commit -m "$(cat <<'EOF'
provision: the vast branch hands off to vast_rent.py

The cheapest-first search and the raw create are gone; SKIP= and OFFER= still work. The label test
now ties vast_rent.VAST_LABEL to the watchdog's DESTROYABLE_NAMES.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 5: `pod-wait.sh` prefers the direct SSH address on Vast

**Files:**
- Modify: `scripts/pod-wait.sh` (the Vast half of `probe`)
- Test: `scripts/tests/test_batch_provider_wiring.py`

**Interfaces:**
- Consumes: `python3 scripts/vast_rent.py --ssh-target <id>` (Task 3) → prints `HOST PORT`, exit 1 when unavailable.
- Produces: for a Vast instance `HOST`/`PORT` are the direct `ip:port` when `vastai ssh-url` gives one, else the proxy `ssh_host`/`ssh_port` as before. Measured 2026-09-19: the `sshX.vast.ai` proxy rejected the registered key on one host while the direct address accepted it.

- [ ] **Step 1: Write the failing test**

Append to `scripts/tests/test_batch_provider_wiring.py` before `if __name__`:

```python
class TestPodWaitDirectAddress(unittest.TestCase):
    def test_the_vast_probe_asks_for_the_direct_address_and_keeps_the_proxy_as_fallback(self):
        text = (ROOT / "scripts" / "pod-wait.sh").read_text(encoding="utf-8")
        self.assertIn("vast_rent.py --ssh-target", text)
        # the proxy values are still read, as the fallback when ssh-url gives nothing
        self.assertIn('"ssh_host"', text)
        self.assertIn('"ssh_port"', text)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -k TestPodWaitDirectAddress -v`
Expected: FAIL — `vast_rent.py --ssh-target` not in pod-wait.sh.

- [ ] **Step 3: Implement**

In `scripts/pod-wait.sh`, in `probe`, right after the three python one-liners that set `STATUS`, `HOST` and `PORT` for the Vast case (the block ending with the `PORT="$(printf '%s' "$raw" | python3 -c …)"` assignment, just before the closing `}` of `probe`), add:

```bash
  # Prefer the DIRECT address (`vastai ssh-url`) over the sshX.vast.ai proxy above: measured
  # 2026-09-19, the proxy rejected the registered key on one host while the direct ip:port
  # accepted it. `ssh-url` may not answer while the instance is still loading — then the proxy
  # values stay, and the ssh probe in the main loop decides whether anything is reachable.
  local direct
  if direct="$(python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vast_rent.py" --ssh-target "$ID" 2>/dev/null)" \
     && [ -n "$direct" ]; then
    HOST="${direct% *}"
    PORT="${direct#* }"
  fi
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -v`
Expected: PASS.

Run: `bash -n scripts/pod-wait.sh && echo syntax-ok`
Expected: `syntax-ok`

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/pod-wait.sh scripts/tests/test_batch_provider_wiring.py
git commit -m "$(cat <<'EOF'
wait: prefer the direct ssh address on vast, keep the proxy as fallback

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 6: Harden the Vast destroy verify (Plan 1's parked items)

**Files:**
- Modify: `Makefile` (gpu-destroy, Vast branch)
- Modify: `scripts/tests/test_batch_provider_wiring.py` (`TestGpuDestroyVastVerifyBehaviour`)

**Interfaces:**
- Consumes: the existing fake-`vastai` harness `TestGpuDestroyVastVerifyBehaviour._destroy(listing, rc)` — it runs the real Makefile text in a scratch dir with a fake `vastai`, a stub `env-clear-pod.sh` (the marker file says whether `.env` would have been wiped) and a no-op `sleep`.
- Produces: the Vast verify (a) tolerates whitespace around the id in `.env`, (b) treats an exit-0 output that is not a listing as "could not verify" (never as "gone"). The real listing shape, verified 2026-09-19 with `vastai show instances-v1 --raw --all`, is `{"instances": [...], "next_token": null, "success": true, …}`.

- [ ] **Step 1: Write the failing tests and fix the fixtures**

In `TestGpuDestroyVastVerifyBehaviour`:
1. Give `_destroy` a keyword `env_id: str | None = None` and write `.env` with `GPU_INSTANCE_ID={env_id if env_id is not None else self.ID}` (so a test can put trailing spaces in `.env`).
2. The existing tests pass a bare JSON list as the listing. The real output is an object with an `"instances"` key; change every existing listing fixture to that shape (for example `'{"instances": [{"id": 12345, "label": "x"}], "next_token": null}'`, and `'{"instances": [], "next_token": null}'` for the "gone" case; the "longer id" case becomes `{"instances": [{"id": 123456, "label": "x"}]}`). The behaviour asserted stays the same.
3. Add these tests:

```python
    def test_output_that_is_not_a_listing_is_not_read_as_gone_even_with_exit_zero(self):
        # An auth error printed by a CLI that still exits 0 contains no instance id either, and
        # used to read as "verified gone" while the instance kept billing.
        out, cleared = self._destroy("Error: invalid API key", rc=0)
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("COULD NOT VERIFY", out.stdout)
        self.assertFalse(cleared, ".env was cleared although nothing was verified")

    def test_an_empty_listing_object_is_gone(self):
        out, cleared = self._destroy('{"instances": [], "next_token": null}')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("verified gone", out.stdout)
        self.assertTrue(cleared)

    def test_trailing_whitespace_on_the_env_id_still_finds_a_live_instance(self):
        # `GPU_INSTANCE_ID=12345   ` expanded into the id regex and never matched a real row, so a
        # live instance read as gone.
        out, cleared = self._destroy(
            '{"instances": [{"id": 12345, "label": "x"}]}', env_id="12345   ")
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("STILL ALIVE", out.stdout)
        self.assertFalse(cleared)
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -k TestGpuDestroyVastVerifyBehaviour -v`
Expected: FAIL — `test_output_that_is_not_a_listing…` (exit 0, "verified gone") and `test_trailing_whitespace…` (reads as gone).

- [ ] **Step 3: Implement**

In the Vast branch of `gpu-destroy` in `Makefile`, make two changes to the verify block:
1. Use `$(strip $(call env,GPU_INSTANCE_ID))` wherever the verify builds the id regex or prints the id (keep the earlier `test -n` guard and the `vastai destroy instance` line as they are).
2. After capturing the listing and confirming the command exited 0, require that the output looks like a listing before trusting an absent id: a `case "$$LIST" in *'"instances"'*) ;; *) echo "COULD NOT VERIFY …"; echo "$$LIST"; exit 1;; esac` guard, placed so that the non-zero-exit path and this path both print `COULD NOT VERIFY`, exit 1, and never reach `env-clear-pod.sh`.

Recipe lines keep real TAB characters; shell variables use `$$`.

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_provider_wiring.py' -v`
Expected: PASS.

Run: `make -n gpu-destroy GPU_PROVIDER=vast | grep -c 'instances-v1'` — Expected: at least `1`. Run `make -n gpu-destroy GPU_PROVIDER=runpod | grep -c 'runpodctl pod delete'` — Expected: `1`.

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add Makefile scripts/tests/test_batch_provider_wiring.py
git commit -m "$(cat <<'EOF'
make: the vast destroy verify no longer reads a non-listing or a padded id as gone

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 7: Destroying an instance that is already gone counts as destroyed

**Files:**
- Modify: `scripts/pod_watchdog.py` (`destroy_verified`)
- Test: `scripts/tests/test_batch_pod_watchdog.py`

**Interfaces:**
- Consumes: the `PodControl` protocol (`list_pods`, `destroy`).
- Produces: `destroy_verified(pods_api, pod_id) -> bool` — if `destroy` raises, re-list; not listed → `True` (it is gone, which is what was wanted); still listed → re-raise the original error; if the re-list itself raises, that error propagates (unverifiable). Success path unchanged.

Why: a destroy of an already-gone instance exits non-zero, `destroy_verified` raised, tier 1/2 fell through, the lease was never cleared, `drain_running()` stayed true and the bot refused the next `/confirm` (same shape on RunPod; found in Plan 1's final review, M4).

- [ ] **Step 1: Write the failing tests**

Add to `scripts/tests/test_batch_pod_watchdog.py`, before `class TestOnceExitCode`:

```python
class GonePods:
    """destroy() raises like a CLI told to delete something that no longer exists."""
    def __init__(self, pods):
        self.pods = pods

    def list_pods(self):
        return list(self.pods)

    def destroy(self, pod_id):
        raise RuntimeError(f"no such instance {pod_id}")


class TestAlreadyGoneDestroy(unittest.TestCase):
    def test_a_destroy_error_for_an_instance_no_longer_listed_counts_as_destroyed(self):
        self.assertTrue(pod_watchdog.destroy_verified(GonePods([]), "777"))

    def test_a_destroy_error_for_an_instance_still_listed_is_still_an_error(self):
        with self.assertRaises(RuntimeError) as cm:
            pod_watchdog.destroy_verified(GonePods([PodInfo("777", "motion-transfer")]), "777")
        self.assertIn("no such instance", str(cm.exception))

    def test_when_neither_the_destroy_nor_the_listing_works_it_is_not_verified(self):
        api = GonePods([])

        def broken():
            raise RuntimeError("cannot list")

        api.list_pods = broken
        with self.assertRaises(RuntimeError):
            pod_watchdog.destroy_verified(api, "777")

    def test_an_expired_lease_for_an_already_gone_instance_is_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            lease_path = Path(tmp) / "pod-lease.json"
            write_lease(lease_path, Lease("777", 0.0, "batch/test.yaml", 10, provider="vast"))
            with patch.object(pod_watchdog, "LEASE_PATH", lease_path), \
                 patch.object(pod_watchdog, "MIGRATE_LEASE_PATH", Path(tmp) / "m.json"), \
                 patch.object(pod_watchdog, "log", lambda *_: None):
                pod_watchdog.tick(FakePods(), {}, now=1000.0 * 60.0, dry_run=False,
                                  extra_apis={"vast": GonePods([])})
            self.assertFalse(lease_path.is_file(),
                             "the lease of an instance that is already gone was kept forever")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_pod_watchdog.py' -k TestAlreadyGoneDestroy -v`
Expected: FAIL — `RuntimeError: no such instance 777` escapes `destroy_verified`.

- [ ] **Step 3: Implement**

In `scripts/pod_watchdog.py` replace the body of `destroy_verified` (keep its docstring and add one sentence) with:

```python
    try:
        pods_api.destroy(pod_id)
    except RuntimeError:
        # A destroy of something that is already gone exits non-zero. What matters is whether it
        # is still LISTED: gone means the goal is met; still listed means the error is real.
        # If the listing itself raises, that propagates — an unverifiable destroy is not success.
        if any(p.pod_id == pod_id for p in pods_api.list_pods()):
            raise
        return True
    still_there = any(p.pod_id == pod_id for p in pods_api.list_pods())
    return not still_there
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_pod_watchdog.py' -v`
Expected: PASS (all, including `TestDestroyIsVerified` and the per-orphan failure tests).

- [ ] **Step 5: Commit**

```bash
bash motions-studio/setup/scrub-secrets.sh --check >/dev/null 2>&1; echo scrub=$?
git add scripts/pod_watchdog.py scripts/tests/test_batch_pod_watchdog.py
git commit -m "$(cat <<'EOF'
watchdog: a destroy that errors on an already-gone instance counts as destroyed

Otherwise the lease is never cleared, drain_running stays true and the bot refuses the next confirm.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

### Task 8: Docs and every gate

**Files:**
- Modify: `docs/gpu-pod.md` (extend the `#vast-provider` section; correct the `#vast-search-sampling` "one atomic motion" paragraph only if it now contradicts)
- Modify: `docs/superpowers/plans/2026-09-19-vast-provider-safety-net.md` (tick off what this plan closed in its "Carry-forward" list)

**Interfaces:** none.

- [ ] **Step 1: Document the rent behaviour**

Append to the `#vast-provider` section of `docs/gpu-pod.md`:

```markdown
**How a machine is chosen (2026-09-19).** `pod-provision.sh` hands the Vast branch to
`scripts/vast_rent.py`. It searches (a random ~40-row sample) plus a `machine_id=` query for the
machines it has measured as fast, drops offers that fail the filters (price cap `MAX_DPH`,
`MIN_DISK_BW`, `MIN_CPU_GHZ`, advertised bandwidth `VAST_MIN_INET_MBPS` default 1000, bandwidth
price `VAST_MAX_DOWN_USD_PER_TB` default 20, direct ports, blacklist), and ranks the rest by
`dph × ready_seconds / 3600 + GB × $/TB / 1000`. `ready_seconds` is the machine's own measured
create-to-running time from `batch/vast-machines.json` (git-ignored), or 556 s — the slowest ever
seen — for a machine nobody has measured. `VAST_GB` (default 60) is what the rental will download;
Plan 3 passes the exact figure per batch. Every instance is created with `--label motion-transfer
--cancel-unavail`. If it is not `running` after `VAST_PULL_DEADLINE_S` (default 480) it is
destroyed, the machine is blacklisted for a day, and the next candidate is tried — at most two
retries. If an abandoned instance cannot be destroyed the rent stops and prints `STILL BILLING`
with its id. Defaults that are assumptions, calibrated as data arrives: the 1000 Mbps floor, the
$20/TB ceiling and the 8-minute deadline (both derive from two hosts; the deadline must stay under
the watchdog's 10-minute grace, and a test enforces that).

`pod-wait.sh` uses the direct address from `vastai ssh-url` when it answers (the
`sshX.vast.ai` proxy rejected the registered key on one host), and the proxy otherwise.

Still unverified until the first paid session: the exact `vastai ssh-url` output text (parsed
tolerantly) and the `create --raw` reply keys.
```

- [ ] **Step 2: Update the carry-forward list**

In the Plan 1 file's `## Carry-forward to Plans 2–3` section, append ` — **Done in Plan 2 (2026-09-19):** …` to items 4 (gitignore), 5 (stale id / label / `--cancel-unavail`) and the M4 half of item 6, and add a line noting that Plan 2 also closed the two parked Makefile-verify minors (exit-0 non-listing output; whitespace in the id).

- [ ] **Step 3: Run every gate**

Run: `make batch-test` — Expected: PASS (all modules).
Run: `make check-job-types && make check-batch-params` — Expected: both exit 0.
Run: `make watchdog-dry` — Expected: exit 0.
Run: `bash motions-studio/setup/scrub-secrets.sh --check; echo $?` — Expected: `0`.
Run: `for f in pod-wait pod-provision pod-bootstrap pod-smoke; do bash -n scripts/$f.sh || echo "BAD $f"; done; echo checked` — Expected: only `checked`.

- [ ] **Step 4: Commit**

```bash
git add docs/gpu-pod.md docs/superpowers/plans/2026-09-19-vast-provider-safety-net.md
git commit -m "$(cat <<'EOF'
docs: how a vast machine is chosen and rented, and what Plan 2 closed

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016gEABVkRBLMdLDQMrL78yb
EOF
)"
```

---

## Self-Review

**Spec coverage (§3.2, all five numbered points and "other facts"):**
1. Filter (`inet_down`, `disk_bw`, reliability in the query, `internet_down_cost_per_tb`, direct ports) → Task 2; the "necessary, not sufficient" caveat is in the module docstring and docs.
2. Rank by `dph × ready + bandwidth`, unknown = slowest seen → Tasks 1–2.
3. Pull deadline, blacklist, at most 2 retries, clear failure → Task 3 (`rent`). A "clear failure on Telegram" is Plan 4 (the bot already shows `detail[-500:]` of the drain's stderr for a non-stock-out provision failure, so the messages written here reach it).
4. Model-download probe → **not here** (needs the per-batch model list): Plan 3.
5. Scoreboard, `machine_id` queries, `.gitignore` → Tasks 1, 3.
- `--cancel-unavail`, `--label`, atomic search+create with retry, direct SSH address, `show instances-v1` → Tasks 3–5; `show instances-v1` for the destroy verify was done in Plan 1's fix wave and hardened in Task 6.
- Plan 1 carry-forward closed: gitignore (T1), stale `GPU_INSTANCE_ID` and abandoned-attempt labels (T3), M4 already-gone destroy (T7), the two parked Makefile-verify minors (T6).
- **Deliberately left for later plans:** model probe and per-batch GB (Plan 3); `start_drain` forwarding `PROVIDER=` and the picker (Plan 4); `vastai` on the VPS; the first paid session; M5 (log noise), M6, "tier 3: 0 pod(s) visible" wording.

**Placeholder scan:** none — every code step carries the code; Task 6's Makefile edit is described by exact behaviour and constrained by tests that already exist plus three new ones, because the recipe text is long and edited in place.

**Type consistency:** `Criteria`/`Ranked`/`rank`/`dedupe`/`explain_rejections`/`format_table` (Task 2) are used with those exact names and signatures in Task 3; `Scoreboard`/`MachineRecord`/`load_board`/`save_board`/`WORST_KNOWN_PULL_S` (Task 1) likewise; `RentConfig`, `RentResult`, `rent(...)` keyword names in the tests match the implementation (`confirm`, `now`, `sleep`, `log`, `on_created`, `on_released`, `persist`); `VAST_LABEL`, `DEFAULT_PULL_DEADLINE_S`, `POLL_S`, `MAX_PULL_RETRIES`, `MAX_CREATE_FAILURES` are defined in Task 3 and only referenced by those names. `board.ready_estimate_s(-1)` in `rank` relies on no record ever existing for machine id −1.
