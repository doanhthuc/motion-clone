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
from batchlib_ext.watchdog import DESTROYABLE_NAMES, GRACE_MIN, decide, reconcile

ROOT = Path(__file__).resolve().parents[1]
LEASE_PATH = ROOT / "batch" / "pod-lease.json"
TICK_SEC = 60


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} watchdog: {msg}", flush=True)


def destroy_verified(pods_api, pod_id: str) -> bool:
    """Destroy the pod, then re-list and confirm it is actually gone.

    An exit code is not proof. Makefile:139-142 records `make gpu-destroy`
    printing success over an aborted destroy, with the invoice as the only
    evidence — that target re-lists and greps for exactly this reason, and so
    does this. The check goes through the injected PodControl rather than inside
    RunpodCtl.destroy so a fake with a no-op destroy proves the caller keeps its
    lease instead of silently declaring victory.
    """
    pods_api.destroy(pod_id)
    still_there = any(p.pod_id == pod_id for p in pods_api.list_pods())
    return not still_there


def tick(pods_api, first_seen: dict[str, float], *, now: float,
         dry_run: bool) -> dict[str, float]:
    lease = read_lease(LEASE_PATH)

    # The tier-1/2 branch gets its own guard. It used to sit above the try that
    # wraps list_pods, so a single raise here (STAGES lookup, a destroy that
    # exits non-zero) reached main()'s catch-all — which logs "tick failed,
    # continuing" and then does the identical thing forever, with tiers 1, 2 AND
    # 3 all skipped. The outermost net must never be downstream of an inner one.
    try:
        if lease is not None:
            journal = state_path_for(ROOT / lease.manifest)
            mtime = journal.stat().st_mtime if journal.is_file() else lease.provisioned_at
            verdict = decide(lease=lease, state=load_state(journal),
                             journal_mtime=mtime, now=now)
            if verdict.kill:
                log(f"KILL {lease.pod_id} — {verdict.reason}")
                if not dry_run:
                    if destroy_verified(pods_api, lease.pod_id):
                        clear_lease(LEASE_PATH)
                    else:
                        # Keeping the lease is the whole point: clearing it would
                        # hand a still-billing pod to tier 3, which then needs the
                        # lease-less path plus a 10-minute grace to notice. With
                        # the lease intact the next tick (60s) retries.
                        log(f"DESTROY NOT CONFIRMED for {lease.pod_id} — it is "
                            f"still in 'runpodctl pod list' and STILL BILLING. "
                            f"Keeping the lease; retrying next tick. Delete it by "
                            f"hand: runpodctl pod delete {lease.pod_id}")
                return first_seen
    except Exception as exc:
        # Fall through to reconciliation on purpose. Tier 3 is the net for
        # everything the inner tiers cannot express, including their own bugs.
        log(f"tier 1/2 failed, falling through to tier 3: {exc!r}")

    try:
        pods = pods_api.list_pods()
    except RuntimeError as exc:
        # Not seeing is not the same as nothing being there. Skip this tick.
        log(f"cannot list pods, skipping reconciliation: {exc}")
        return first_seen

    # "Saw nothing" and "saw things and matched nothing" used to print the same
    # silence, and that ambiguity is exactly what let the broken `runpodctl get
    # pod` invocation sit undetected until 2026-08-31 — tier 3 had never once
    # executed. One counted line makes the difference visible.
    log(f"tier 3: {len(pods)} pod(s) visible")

    kill, seen = reconcile(pods=pods, lease=lease, first_seen=first_seen, now=now)
    for pod_id in kill:
        log(f"KILL {pod_id} — orphan, no lease claims it")
        if not dry_run:
            if not destroy_verified(pods_api, pod_id):
                log(f"DESTROY NOT CONFIRMED for {pod_id} — still in 'runpodctl "
                    f"pod list' and STILL BILLING. Retrying next tick. Delete it "
                    f"by hand: runpodctl pod delete {pod_id}")

    # Say what we deliberately left alone, and WHY — the two reasons are not the
    # same, and reporting the wrong one is worse than reporting nothing. Silent
    # inaction on a $0.99/hour box is the thing this daemon exists to prevent, so
    # naming them costs one log line and makes the boundary auditable.
    untouched = [p for p in pods
                 if p.pod_id not in kill and (lease is None or p.pod_id != lease.pod_id)]
    for p in untouched:
        if p.name in DESTROYABLE_NAMES:
            age_min = (now - seen[p.pod_id]) / 60.0
            log(f"leaving {p.pod_id} ({p.name!r}) alone — unclaimed but only "
                f"{age_min:.0f} min old, inside the {GRACE_MIN} min grace window")
        else:
            log(f"leaving {p.pod_id} ({p.name!r}) alone — not a name tier 3 may destroy")
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
        failed = False
        try:
            first_seen = tick(pods_api, first_seen, now=time.time(),
                              dry_run=args.dry_run)
        except Exception as exc:            # never let one bad tick end the guard
            log(f"tick failed, continuing: {exc!r}")
            failed = True
        if args.once:
            # --once is a gate, not a daemon: `make watchdog-dry` is acceptance
            # step A1 and its exit code is what that step reads. It exited 0 for
            # the whole life of this branch while tier 3 was completely broken,
            # which is the failure this return value now reports. The daemon loop
            # below still continues on a bad tick — there, dying is the worse bug.
            return 1 if failed else 0
        time.sleep(TICK_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
