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
from batchlib_ext.handoff import Handoff, claim_mailbox, handoff_path, write_handoff
from batchlib_ext.lease import Lease, clear_lease, read_lease, write_lease

ROOT = Path(__file__).resolve().parents[1]
LEASE_PATH = ROOT / "batch" / "pod-lease.json"
CEILING_SLACK_MIN = 30
DEFAULT_POD_MAX_HOURS = 8   # the fallback pod-provision.sh:28 applies


def abs_max_min(manifest: Manifest) -> int:
    """Tier-2 ceiling: every stage's timeout in this manifest, plus slack."""
    total = sum(STAGES[stage].timeout_min
                for run in manifest.runs
                for stage in PIPELINES[run.pipeline])
    return total + CEILING_SLACK_MIN


def sh(*argv: str) -> None:
    subprocess.run(argv, check=True, cwd=ROOT)


def pod_max_hours(ceiling_min: int, configured: str) -> str:
    """The POD_MAX_HOURS to hand pod-provision.sh: the tier-2 ceiling, CAPPED.

    This can only ever tighten the RunPod-side --stop-after net, never loosen it.
    An uncapped ceil(ceiling/60) loosens it for most real manifests — measured
    2026-08-31 over the 7 manifests in batch/: 11, 6, 11, 9, 3, 18 and 6 hours,
    so 4 of 7 would have RAISED the 8-hour default, and the 6-cap lanczos batch
    would have been granted 18. The design spec says tier 0 stays "still capped
    by POD_MAX_HOURS", and a safety net you are allowed to widen is not one.

    POD_MAX_HOURS=0 means the net is deliberately disabled (pod-provision.sh:70).
    Passing 0 through preserves that; substituting a number would quietly
    re-enable a net the operator switched off.

    A cap below the tier-2 ceiling can stop a long batch mid-run — the lanczos
    6-cap manifest wants 18h and gets 8. That is the same thing `make batch` has
    always done with POD_MAX_HOURS=8, and --stop-after only STOPS the pod (the DB
    survives, `make gpu-up` resumes). Wanting more is a deliberate edit to .env,
    not something a manifest may grant itself.
    """
    value = (configured or "").strip()
    if value == "0":
        return "0"
    if not value.isdigit():
        # Garbage is handed straight through so pod-provision.sh:72 reports it by
        # name and dies before renting. Guessing a number here would hide a typo
        # in .env behind a working-looking rent.
        if value:
            return value
        value = str(DEFAULT_POD_MAX_HOURS)
    return str(max(1, min(-(-ceiling_min // 60), int(value))))   # ceil, capped


def provision(*, ceiling_min: int) -> str:
    """Rent a pod and return its instance id. Does NOT wait or bootstrap.

    Split from the wait so main() can write the lease in between. The pod is
    visible to runpodctl — and therefore to the watchdog's 10-minute tier-3 grace
    window — from the moment this returns, while pod-wait.sh alone defaults to a
    25-minute TIMEOUT (pod-wait.sh:118) and a first, non-prebuilt bootstrap runs
    ~30 min (docs/gpu-pod.md:228). Writing the lease after all that let tier 3
    destroy the pod drain had just legitimately rented.

    POD_MAX_HOURS is tightened per-drain — see pod_max_hours(). pod-provision.sh
    already reads it from the environment.
    """
    hours = pod_max_hours(ceiling_min, env_get(ROOT / ".env", "POD_MAX_HOURS"))
    subprocess.run(f"POD_MAX_HOURS={hours} CONFIRM=yes bash scripts/pod-provision.sh",
                   shell=True, check=True, cwd=ROOT)
    pod_id = env_get(ROOT / ".env", "GPU_INSTANCE_ID")
    if not pod_id:
        # env_get returns "" for every failure mode, including .env being
        # unreadable (config.py:62-66). An empty id in the lease makes every later
        # kill attempt `runpodctl pod delete ""`, which raises — so the watchdog
        # can never clean up the pod that was just rented.
        raise RuntimeError(
            "pod-provision.sh exited 0 but GPU_INSTANCE_ID is empty in .env — "
            "refusing to write a lease that cannot destroy anything. Check that "
            f"{ROOT / '.env'} is readable and has a GPU_INSTANCE_ID line, then "
            "run 'runpodctl pod list -o json' and delete the pod by hand if one "
            "was rented.")
    return pod_id


def wait_and_bootstrap() -> None:
    """Block until the pod answers SSH, then install the backend on it."""
    sh("bash", "scripts/pod-wait.sh")
    sh("bash", "scripts/pod-bootstrap.sh")


def batch_run(*args: str) -> int:
    """Run the existing CLI. Never reimplement what it wires together."""
    return subprocess.run([sys.executable, "scripts/batch_run.py", *args],
                          cwd=ROOT).returncode


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
        try:
            # mkdir belongs INSIDE the try. Outside it, a disk-full or
            # permission error escapes the loop, escapes this function, and
            # aborts the caller's finally block before it can destroy the pod.
            dest.parent.mkdir(parents=True, exist_ok=True)
            # GET /jobs/:id/logs, not pm2: face_crop=vitpose vs the DWPose
            # fallback is written with api_log (linux.py:4605) and never
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


def chain_or_teardown(original_manifest: Path) -> None:
    """Destroy the pod — unless a job is already queued for the same chat,
    in which case run it on this same pod instead of renting a fresh one.

    Checked exactly once per link, right where `teardown()` used to be called
    unconditionally: no polling, no arbitrary grace period. If the mailbox
    (batchlib_ext.handoff.mailbox_path) is empty at this exact instant, the
    pod is destroyed immediately, same as before this existed. If a job IS
    there, it was already validated by bot.py's own /confirm — this only
    needs to run it and report what happened, because a silent handoff that
    nobody heard about is worse than the wait it was meant to save.

    Every exit from this loop funnels through `teardown(current)` — same
    discipline as `teardown()` itself guards for diagnostics: a bug picking
    up the next link must never be able to skip destroying the pod.
    """
    current = original_manifest
    while True:
        try:
            nxt = claim_mailbox(original_manifest)
            if nxt is None:
                teardown(current)
                return
            hpath = handoff_path(original_manifest)
            try:
                nxt_manifest = load_manifest(nxt)
            except Exception as exc:
                write_handoff(hpath, Handoff(status="failed", manifest=str(nxt),
                                             reason=repr(exc)))
                teardown(current)
                return
            write_handoff(hpath, Handoff(status="starting", manifest=str(nxt)))
            try:
                rc = batch_run("--file", str(nxt))
            except Exception as exc:
                rc, reason = 1, repr(exc)
            else:
                reason = None if rc == 0 else f"batch_run exited {rc}"
            if rc != 0:
                write_handoff(hpath, Handoff(status="failed", manifest=str(nxt),
                                             reason=reason))
                teardown(current)
                return
            # Tier 2's ceiling must keep bounding the TOTAL pod lifetime, not
            # reset per link — watchdog.py:65-77. provisioned_at stays put;
            # abs_max_min grows by this link's own ceiling. Tier 1 also needs
            # `manifest` repointed, or it keeps watching a journal that
            # stopped moving the moment this link finished.
            lease = read_lease(LEASE_PATH)
            if lease is not None:
                write_lease(LEASE_PATH, Lease(
                    pod_id=lease.pod_id, provisioned_at=lease.provisioned_at,
                    manifest=str(nxt.resolve()),
                    abs_max_min=lease.abs_max_min + abs_max_min(nxt_manifest)))
            write_handoff(hpath, Handoff(status="running", manifest=str(nxt)))
            current = nxt
        except Exception as exc:
            # Anything unanticipated above must still destroy — the one
            # invariant this function exists to preserve.
            try:
                write_handoff(handoff_path(original_manifest),
                             Handoff(status="failed", manifest=str(current), reason=repr(exc)))
            except Exception:
                pass
            teardown(current)
            return


def teardown(manifest_path: Path) -> None:
    """Collect diagnostics if anything failed, then destroy the pod. Always runs.

    Diagnostics must never be able to stop the destroy. `finally` protects
    against an exception in the TRY block; it does not protect against an
    exception raised inside `finally` itself, which aborts the rest of the
    block — so a disk-full mkdir while fetching a log would skip
    `make gpu-destroy` and leave a $0.99/hour pod running. A failed log fetch
    is an inconvenience; a pod that outlives its batch is a bill.
    """
    state = load_state(state_path_for(manifest_path))
    batch_id = state.get("batch") or ""
    if batch_id and failed_job_ids(state):
        try:
            # load_settings() here rather than at the top of main(): on a fresh
            # VPS, NUXT_MOTION_API_KEY does not exist in motions/.env until
            # gpu-bootstrap has written it, so calling it before provisioning
            # would raise ConfigError on the very first drain.
            collect_diagnostics(load_settings(), state, ROOT / "out" / batch_id)
        except Exception as exc:
            print(f"could not collect diagnostics: {exc!r}", file=sys.stderr)
    sh("make", "gpu-destroy")
    clear_lease(LEASE_PATH)


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

    pod_id = provision(ceiling_min=ceiling)
    # The lease is written HERE, between provisioning and waiting — not after
    # bootstrap. The pod bills from the line above, and tier 3's grace window is
    # 10 minutes while bootstrap is 284s prebuilt (docs/gpu-pod.md:81) and ~30 min
    # on a first run (docs/gpu-pod.md:228). The manifest path is resolved: the
    # watchdog reads it as ROOT / lease.manifest, so a relative path recorded from
    # another cwd would resolve to a different (or missing) journal and tier 1
    # would fire on a live batch at 105 minutes.
    write_lease(LEASE_PATH, Lease(pod_id=pod_id, provisioned_at=time.time(),
                                  manifest=str(manifest_path.resolve()),
                                  abs_max_min=ceiling))
    try:
        # Inside the try, so a wait/bootstrap failure still reaches teardown.
        # Tiers 1 and 2 now cover this phase too, because the lease exists.
        wait_and_bootstrap()
        # --resume, always: phase A already journalled the try-on stages, and
        # resume is what makes them skipped rather than paid for twice.
        rc = batch_run("--file", str(manifest_path), "--resume")
    finally:
        # Best effort only. The watchdog is the guarantee, not this block:
        # `finally` does not run when the process is SIGKILLed or the VPS dies.
        # chain_or_teardown destroys immediately unless a job is already
        # queued for this same manifest's mailbox — see its own docstring.
        chain_or_teardown(manifest_path)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
