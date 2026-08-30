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

    # Say what we deliberately left alone. Tier 3 only destroys pods named by
    # DESTROYABLE_NAMES, so an unmanaged pod is invisible to it — and silent
    # inaction on a $0.99/hour box is exactly the thing this daemon exists to
    # prevent. Naming them costs one log line and makes the boundary auditable.
    untouched = [p for p in pods
                 if p.pod_id not in kill and (lease is None or p.pod_id != lease.pod_id)]
    for p in untouched:
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
