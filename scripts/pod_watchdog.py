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
from batchlib_ext.migrate_lease import clear_migrate_lease, read_migrate_lease
from batchlib_ext.podctl import RunpodCtl, VastCtl
from batchlib_ext.watchdog import (DESTROYABLE_NAMES, GRACE_MIN,
                                   MIGRATE_DESTROYABLE_NAMES, decide,
                                   decide_migration, reconcile,
                                   reconcile_migration)

ROOT = Path(__file__).resolve().parents[1]
LEASE_PATH = ROOT / "batch" / "pod-lease.json"
MIGRATE_LEASE_PATH = ROOT / "batch" / "volume-migrate-lease.json"
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

    If destroy raises, the instance may be already gone; re-list to confirm.

    Only RuntimeError is treated as "maybe already gone": VastCtl and RunpodCtl both normalise
    their own OSError/TimeoutExpired (missing binary, hung CLI) into RuntimeError before it
    reaches here, so anything else propagates uncaught — it is not this function's ambiguous
    case to resolve.
    """
    try:
        pods_api.destroy(pod_id)
    except RuntimeError as exc:
        # A destroy of something that is already gone exits non-zero. What matters is whether it
        # is still LISTED: gone means the goal is met; still listed means the error is real.
        # If the listing itself raises, that propagates — an unverifiable destroy is not success.
        if any(p.pod_id == pod_id for p in pods_api.list_pods()):
            raise
        # F5/I3: this used to `return True` with no log line at all — the only branch of this
        # function that decided something without saying so.
        log(f"destroy of {pod_id} errored ({exc}) but it is not listed — treating as already gone")
        return True
    still_there = any(p.pod_id == pod_id for p in pods_api.list_pods())
    return not still_there


def tick(pods_api, first_seen: dict[str, float], *, now: float,
         dry_run: bool, extra_apis: dict | None = None) -> dict[str, float]:
    """`pods_api` is the RunPod control; `extra_apis` maps another provider's name to its
    control. A lease is destroyed through the control its own `provider` names — sending a
    Vast lease to runpodctl would "succeed" against nothing and leave the instance billing."""
    apis = {"runpod": pods_api, **(extra_apis or {})}
    lease = read_lease(LEASE_PATH)

    # The tier-1/2 branch gets its own guard. It used to sit above the try that
    # wraps list_pods, so a single raise here (STAGES lookup, a destroy that
    # exits non-zero) reached main()'s catch-all — which logs "tick failed,
    # continuing" and then does the identical thing forever, with tiers 1, 2 AND
    # 3 all skipped. The outermost net must never be downstream of an inner one.
    try:
        if lease is not None:
            # Resolved before deciding, so a lease naming a provider this process has no
            # control for is reported on every tick (via the except below) instead of only on
            # the tick that finally needs to kill.
            lease_api = apis[lease.provider]
            journal = state_path_for(ROOT / lease.manifest)
            mtime = journal.stat().st_mtime if journal.is_file() else lease.provisioned_at
            verdict = decide(lease=lease, state=load_state(journal),
                             journal_mtime=mtime, now=now)
            if verdict.kill:
                log(f"KILL {lease.pod_id} — {verdict.reason}")
                if not dry_run:
                    if destroy_verified(lease_api, lease.pod_id):
                        clear_lease(LEASE_PATH)
                    else:
                        # Keeping the lease is the whole point: clearing it would
                        # hand a still-billing pod to tier 3, which then needs the
                        # lease-less path plus a 10-minute grace to notice. With
                        # the lease intact the next tick (60s) retries.
                        log(f"DESTROY NOT CONFIRMED for {lease.pod_id} — it is "
                            f"still in the {lease.provider} listing and STILL BILLING. "
                            f"Keeping the lease; retrying next tick. Delete it by "
                            f"hand ({lease.provider}): {lease.pod_id}")
                return first_seen
    except Exception as exc:
        # Fall through to reconciliation on purpose. Tier 3 is the net for
        # everything the inner tiers cannot express, including their own bugs.
        log(f"tier 1/2 failed, falling through to tier 3: {exc!r}")

    # Tier-1/2-equivalent for the two TEMPORARY CPU pods a volume migration
    # rents (scripts/volume_migrate.py, not part of this task). Separate
    # lease file, separate ceiling (MIGRATE_CEILING_MIN, no per-stage journal
    # to dead-man's-switch against) — see batchlib_ext/watchdog.py. Unlike
    # the real GPU pod's branch above, this one does not return on a kill:
    # falling through to tier 3 below is what also catches the case where
    # the destroy above was not confirmed. It gets the SAME try/except guard
    # as the real pod's branch above, for the same reason: a raise in here
    # (decide_migration, or destroy_verified via RunpodCtl.destroy raising
    # RuntimeError on a non-zero exit) must not take tier 3 down with it —
    # "the outermost net must never be downstream of an inner one."
    try:
        migrate_lease = read_migrate_lease(MIGRATE_LEASE_PATH)
        if migrate_lease is not None:
            verdict = decide_migration(lease=migrate_lease, now=now)
            if verdict.kill:
                log(f"KILL migration temp pods {migrate_lease.pod_a_id}, "
                    f"{migrate_lease.pod_b_id} — {verdict.reason}")
                if not dry_run:
                    ok_a = destroy_verified(pods_api, migrate_lease.pod_a_id)
                    ok_b = destroy_verified(pods_api, migrate_lease.pod_b_id)
                    if ok_a and ok_b:
                        clear_migrate_lease(MIGRATE_LEASE_PATH)
                    else:
                        log("DESTROY NOT CONFIRMED for one or both migration temp "
                            "pods — still billing, retrying next tick")
    except Exception as exc:
        # Same fall-through as the real pod's tier 1/2 above: tier 3 is the
        # net for everything the inner tiers cannot express, including their
        # own bugs. migrate_lease may be undefined if read_migrate_lease
        # itself raised (it shouldn't — it catches its own I/O/parse errors —
        # but this guard does not depend on that), so default it to None:
        # reconcile_migration then treats the two temp pods as ordinary
        # orphans, the safe direction to fail in.
        migrate_lease = None
        log(f"migration tier 1/2 failed, falling through to tier 3: {exc!r}")

    # Every provider is listed on its own. One CLI failing must not blind the others: a broken
    # `runpodctl` would otherwise stop the scan for a Vast instance that is billing right now.
    pods: list = []
    owner: dict = {}
    any_failed = False
    for provider_name, api in apis.items():
        try:
            listed = api.list_pods()
        except Exception as exc:
            # Any failure, not only RuntimeError: RunpodCtl.list_pods lets TimeoutExpired and
            # FileNotFoundError through, and RunPod is listed first, so a hung or missing
            # runpodctl would otherwise stop the Vast scan. Skip-only: a provider that could not
            # be listed adds nothing to the kill set.
            # Not seeing is not the same as nothing being there. Skip this provider.
            log(f"cannot list {provider_name} pods, skipping its reconciliation: {exc}")
            any_failed = True
            continue
        for p in listed:
            pods.append(p)
            owner[p.pod_id] = api

    # "Saw nothing" and "saw things and matched nothing" used to print the same
    # silence, and that ambiguity is exactly what let the broken `runpodctl get
    # pod` invocation sit undetected until 2026-08-31 — tier 3 had never once
    # executed. One counted line makes the difference visible.
    log(f"tier 3: {len(pods)} pod(s) visible")

    kill, seen = reconcile(pods=pods, lease=lease, first_seen=first_seen, now=now)
    # Migration temp pods only ever exist on RunPod.
    migrate_kill, _ = reconcile_migration(
        pods=[p for p in pods if owner[p.pod_id] is pods_api], lease=migrate_lease,
        first_seen=first_seen, now=now)
    kill = kill + migrate_kill
    for pod_id in kill:
        log(f"KILL {pod_id} — orphan, no lease claims it")
        if not dry_run:
            # Per-orphan guard: destroy_verified raises RuntimeError on a non-zero destroy exit
            # or a failed re-list, and one stuck orphan (RunPod is killed first) must not keep
            # every later one, on any provider, billing for the rest of the tick.
            try:
                confirmed = destroy_verified(owner[pod_id], pod_id)
            except Exception as exc:
                log(f"DESTROY NOT CONFIRMED for {pod_id} — destroy raised {exc!r}; it is "
                    f"STILL BILLING. Retrying next tick. Delete it by hand at the provider.")
                continue
            if not confirmed:
                log(f"DESTROY NOT CONFIRMED for {pod_id} — still in its provider's "
                    f"listing and STILL BILLING. Retrying next tick. Delete it "
                    f"by hand at the provider.")

    # Say what we deliberately left alone, and WHY — the two reasons are not the
    # same, and reporting the wrong one is worse than reporting nothing. Silent
    # inaction on a $0.99/hour box is the thing this daemon exists to prevent, so
    # naming them costs one log line and makes the boundary auditable.
    #
    # THREE reasons now, not two. The migration lease's two pod ids have to be
    # excluded the same way the real pod's leased id already is: without it, a
    # perfectly normal migration made this loop print "leaving migrate-tmp-a
    # alone — unclaimed but only 62 min old, inside the 10 min grace window"
    # on every 60s tick, in which both halves are false (the pod IS claimed,
    # and 62 is not inside 10). A watchdog log that cries wolf on the healthy
    # case is worth less than no log, which is the same argument the two
    # existing branches were split for.
    migrating = ({migrate_lease.pod_a_id, migrate_lease.pod_b_id}
                 if migrate_lease is not None else set())
    for p in pods:
        if p.pod_id in migrating and p.pod_id not in kill:
            log(f"leaving {p.pod_id} ({p.name!r}) alone — claimed by an active "
                f"migration lease (to {migrate_lease.to_dc})")

    untouched = [p for p in pods
                 if p.pod_id not in kill
                 and (lease is None or p.pod_id != lease.pod_id)
                 and p.pod_id not in migrating]
    for p in untouched:
        if p.name in DESTROYABLE_NAMES or p.name in MIGRATE_DESTROYABLE_NAMES:
            age_min = (now - seen[p.pod_id]) / 60.0
            log(f"leaving {p.pod_id} ({p.name!r}) alone — unclaimed but only "
                f"{age_min:.0f} min old, inside the {GRACE_MIN} min grace window")
        else:
            log(f"leaving {p.pod_id} ({p.name!r}) alone — not a name tier 3 may destroy")
    # When a provider could not be listed, its instances are absent from `seen`; carrying the
    # old entries over keeps their grace clock instead of restarting it next tick.
    return {**first_seen, **seen} if any_failed else seen


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pods_api = RunpodCtl()
    extra_apis = {"vast": VastCtl()}
    first_seen: dict[str, float] = {}
    log(f"started, lease={LEASE_PATH}, dry_run={args.dry_run}")
    while True:
        failed = False
        try:
            first_seen = tick(pods_api, first_seen, now=time.time(),
                              dry_run=args.dry_run, extra_apis=extra_apis)
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
