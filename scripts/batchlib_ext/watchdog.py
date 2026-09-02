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
from .migrate_lease import MigrateLease
from .podctl import PodInfo

# Longest declared stage timeout (enhance, 90 min as of 2026-08-30). Derived,
# never hardcoded: pipelines.py is the single place stage timeouts are declared.
LONGEST_STAGE_MIN = max(s.timeout_min for s in STAGES.values())

# Tier 3 destroys things, so its authority is scoped to the names this repo's
# own provisioning creates. Anything else in the account — a CPU box someone
# stood up by hand for an unrelated reason — is not ours to kill. The two
# temporary pods a volume migration rents (scripts/volume_migrate.py) have
# their OWN names and their own authority list, MIGRATE_DESTROYABLE_NAMES
# below — kept separate rather than merged into this one, so a lease-shape
# change for one can never silently change the other's scope.
#
# HAND-COPIED LIST. The one place a pod name is created is the `--name` flag at
# scripts/pod-provision.sh:393, which carries the matching pointer back here.
# There is no `make check-*` gate tying the two together yet (CLAUDE.md's "four
# registries" drift class), so changing either one means changing both by hand.
DESTROYABLE_NAMES = frozenset({"motion-transfer"})

# 2026-09-02: this repo used to stand up these two pods BY HAND for the
# EU-CZ-1 failover runbook, which is exactly why the DESTROYABLE_NAMES
# comment above says a CPU box "is not ours to kill" — now that
# scripts/volume_migrate.py automates that runbook, its own two pod names
# need the SAME tier-3 authority the real GPU pod already has, or a crashed
# migration bills forever with nothing watching it.
MIGRATE_DESTROYABLE_NAMES = frozenset({"migrate-tmp-a", "migrate-tmp-b"})

# Spec estimate is 15-25 min end-to-end (docs/superpowers/specs/2026-09-02-
# volume-region-migration-design.md §4) — doubled for margin, same
# reasoning as GRACE_MIN's own margin over the provisioning window it covers.
MIGRATE_CEILING_MIN = 40

# How long a pod may exist unclaimed before tier 3 treats it as an orphan. It has
# to cover the window between `pod create` returning and drain.py writing the
# lease. Since 2026-08-31 drain.py writes the lease immediately after
# pod-provision.sh returns, so that window is a subprocess exit, not the 284s
# bootstrap (docs/gpu-pod.md:81) it used to be — 10 minutes is now generous.
GRACE_MIN = 10


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
    """Tier 2 (absolute ceiling) then tier 1 (dead-man's switch).

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
    # .get, not [ ]: a journal can name a stage pipelines.py does not declare —
    # an older journal replayed after a rename, or a hand-edited state file. A
    # KeyError here would propagate out of the whole tick, and lease.py's
    # docstring states the house rule: a watchdog that dies stops guarding a
    # $0.99/hour pod. Falling back to the longest declared timeout (90 min,
    # enhance, as of 2026-08-30) can only delay a kill, never cause an early one,
    # and tier 2's ceiling still bounds the total.
    declared = STAGES.get(stage) if stage else None
    budget = (declared.timeout_min if declared else LONGEST_STAGE_MIN) + slack_min
    silent_min = (now - journal_mtime) / 60.0
    if silent_min > budget:
        where = stage or "no stage running"
        if stage and declared is None:
            where = f"{stage}, unknown to pipelines.py"
        return Verdict(True, f"journal silent {silent_min:.0f} min "
                             f"> {budget} min budget ({where})")

    return Verdict(False, "")


def reconcile(*, pods: list[PodInfo], lease: Lease | None,
              first_seen: dict[str, float], now: float,
              grace_min: int = GRACE_MIN,
              destroyable_names: frozenset[str] = DESTROYABLE_NAMES) -> tuple[list[str], dict[str, float]]:
    """Tier 3: any pod we can see that no lease claims is an orphan.

    This is the net for the worst case — the lease file was lost, or two
    machines both think they own the pod (see the ownership decision in the
    spec). It runs on daemon start too, which covers a VPS reboot mid-batch.

    Authority is scoped to the pod names this repo's own provisioning creates.
    A pod is only marked for destruction if it is both unclaimed AND has a
    destroyable name. This allows tier 3 to coexist with manual operations
    like the EU-CZ-1 failover runbook, which stands up temporary CPU pods.

    The grace window exists because provisioning writes the lease AFTER the
    pod exists. Without it, a tick landing in that gap kills a pod that is one
    second old. Since 2026-08-31 that gap is only the tail of pod-provision.sh
    (drain.py writes the lease the moment it returns), not the whole bootstrap.
    """
    leased = lease.pod_id if lease else None
    seen = {p.pod_id: first_seen.get(p.pod_id, now) for p in pods}
    kill = [p.pod_id for p in pods
            if p.pod_id != leased and p.name in destroyable_names
            and (now - seen[p.pod_id]) / 60.0 > grace_min]
    return kill, seen


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
