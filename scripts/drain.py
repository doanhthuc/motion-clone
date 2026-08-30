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
    ap.add_argument("--resume", action="store_true",
                    help="continue a deferred drain instead of starting a new batch")
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
    #
    # --resume must be forwarded to phase A, not only to the run after
    # provisioning. Without it, resolve_batch_id (batch_run.py:38) mints a NEW
    # batch id on a re-drain, so every local try-on runs again and Gemini is
    # billed a second time — silently destroying the "defer preserves the
    # try-on you already paid for" guarantee in the design spec.
    phase_a = ["--file", str(manifest_path), "--no-start"]
    if args.resume:
        phase_a.append("--resume")
    rc = batch_run(*phase_a)
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
