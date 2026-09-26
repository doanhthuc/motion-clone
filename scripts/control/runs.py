"""Runs, read from the on-disk journals — never from the pod.

The journal (`batch/<id>.state.json`) is the same source `progress_text` and
`batch_status` read, so a run stays reportable after the pod is destroyed: the
pod is the thing most likely to be gone by the time the phone asks.

Liveness comes from `tgbot.run` through the module, not through imported
names, so it sees this process's own `_RUNNING` / `_PHASE_A` handles — the
reason the API runs inside the bot process (spec §3, approach A).
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from batchlib.manifest import ManifestError, load_manifest, load_state, state_path_for
from control.outputs import final_names
from control.paths import safe_child
import tgbot.run as run_mod


# The flat rate the bot's own /kill text and [Run] fallback quote for RunPod
# (`_gpu_price`'s 0.99 fallback, the 5090's price). A QUOTE, not the invoice:
# CLAUDE.md is explicit that cost claims come from `runpodctl billing pods`,
# and this number is what a phone shows next to "elapsed" as an estimate. It
# is a constant on purpose: a rate looked up live (or an accrued dollar
# amount) would move between polls and break the ETag rule of spec 5.3.
RUNPOD_FLAT_USD_PER_HR = 0.99


def quoted_usd_per_hr(provider: str) -> float | None:
    """The quote for a lease's provider. None for Vast: its lease does not
    carry the rented offer's price, and guessing one would be a made-up number
    next to a real bill."""
    return RUNPOD_FLAT_USD_PER_HR if provider == "runpod" else None


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
            try:
                found.append(_summary(manifest, state_file))
            except FileNotFoundError:
                # /clear or batch-clean can delete a state file after glob()
                # found it but before _summary()'s stat() runs. One vanished
                # run should not 500 the whole list — skip it.
                continue
    return sorted(found, key=lambda r: r["updated_at"], reverse=True)


def _job_setups(manifest: Path) -> dict[str, dict]:
    """job id -> what it was made from, for the phone's batch details.

    Inputs go out as material ids (`<owner>/<name>`, the staging layout
    `control.materials` lists), never the manifest's absolute paths. A
    manifest that no longer loads gives no setups rather than a 500: the
    journal, not the manifest, is what this route exists to report.
    """
    try:
        loaded = load_manifest(manifest)
    except (ManifestError, OSError):
        return {}
    return {run.id: {"pipeline": run.pipeline,
                     "provider": (run.stage_params.get("tryon") or {}).get("provider"),
                     "inputs": {role: f"{path.parent.name}/{path.name}"
                                for role, path in run.inputs.items()}}
            for run in loaded.runs}


def run_detail(batch_dir: Path, out_dir: Path, run_id: str) -> dict | None:
    manifest = safe_child(batch_dir, f"{run_id}.yaml") if run_id else None
    if manifest is None or not manifest.is_file():
        return None
    state_file = state_path_for(manifest)
    if not state_file.is_file():
        return None
    detail = _summary(manifest, state_file)
    jobs = load_state(state_file).get("runs") or {}
    setups = _job_setups(manifest)
    # Whitelisted fields only: the journal also holds absolute `file` paths
    # and the full params_sent payload, neither of which the phone needs.
    detail["jobs"] = [
        {"id": job_id, "status": str(job.get("status")),
         "stages": [{"name": name,
                     "status": str(stage.get("status")),
                     "elapsed_sec": stage.get("elapsed_sec")}
                    for name, stage in (job.get("stages") or {}).items()
                    if isinstance(stage, dict)],
         "setup": setups.get(job_id)}
        for job_id, job in jobs.items() if isinstance(job, dict)
    ]
    lease = run_mod.lease_for(manifest)
    # provisioned_at, not a derived elapsed_sec: elapsed_sec changed every
    # second, so the ETag (a hash of the whole JSON body, server.py
    # _send_json) could never repeat while a pod was live and If-None-Match
    # never got a 304 (review finding 2a, 2026-09-21). provisioned_at is a
    # fixed number straight from the lease; the phone can compute elapsed
    # itself from it.
    detail["lease"] = None if lease is None else {
        "provider": lease.provider,
        "provisioned_at": lease.provisioned_at,
        "abs_max_min": lease.abs_max_min,
        # Constant per provider (see RUNPOD_FLAT_USD_PER_HR), so this adds
        # nothing that changes between two polls of the same lease. The
        # client computes elapsed x rate itself.
        "quoted_usd_per_hr": quoted_usd_per_hr(lease.provider),
    }
    detail["outputs"] = final_names(out_dir, detail["batch"]) if detail["batch"] else []
    return detail


@dataclass(frozen=True)
class Outcome:
    """What a run action did. Truthy when it started or queued something, so
    callers that used to test a bool (`if _do_resume(...)`) keep working."""
    ok: bool
    code: str
    message: str = ""

    def __bool__(self) -> bool:
        return self.ok


OUTCOME_STATUS = {
    "not_found": 404,
    "nothing_to_run": 422, "not_validated": 422, "invalid": 422, "manifest_error": 422,
    "bot_busy": 503,
    # Slice 5: a refusal the caller can fix (a bad field) versus one nothing
    # on this box can fix (runpodctl/vastai unreachable). Both would otherwise
    # fall into the catch-all 409, which tells a phone client to retry the
    # same body forever.
    "upstream_unavailable": 502, "bad_request": 400,
}


def status_for(outcome: Outcome) -> int:
    if outcome.ok:
        return 202
    return OUTCOME_STATUS.get(outcome.code, 409)


# The files in batch/ that make a run a run: the manifest, its journal and a
# drain's handoff note. Everything else named after the manifest's stem
# (`<stem>.draft.json`, `.ledger.json`, logs) belongs to the chat, not to one
# run, and stays — the Telegram draft and message ledger live on after it.
_RUN_FILE_SUFFIXES = (".yaml", ".state.json", ".handoff.json")


def delete_run(batch_dir: Path, out_dir: Path, run_id: str, *, with_videos: bool) -> tuple[Outcome, int]:
    """Forget a run, and optionally everything it made (2026-09-27). Returns
    the outcome and how many videos went with it.

    Always removed: the manifest, journal and handoff note, plus
    `out/<batch>/runs/` (per-stage intermediates, try-on images included) and
    the manifest copy there. `_final/` — the videos in Outputs — goes only
    with `with_videos`, and never while another run's journal still names
    the same batch directory.

    Refused while anything holds the manifest: a drain or Phase A reads it,
    and a lease means a pod is billed against it. The caller holds BOT_LOCK
    so neither can start between this check and the unlink.
    """
    manifest = safe_child(batch_dir, f"{run_id}.yaml") if run_id else None
    if manifest is None or not manifest.is_file() or not state_path_for(manifest).is_file():
        return Outcome(False, "not_found", "no such run"), 0
    if run_mod.busy(manifest) or run_mod.lease_for(manifest) is not None:
        return Outcome(False, "run_busy", "the run is running or has a pod; kill it first"), 0
    batch = load_state(state_path_for(manifest)).get("batch") or None
    shared = batch is not None and any(
        r["batch"] == batch for r in list_runs(batch_dir, out_dir) if r["id"] != manifest.stem)

    for suffix in _RUN_FILE_SUFFIXES:
        manifest.with_name(manifest.stem + suffix).unlink(missing_ok=True)

    videos = 0
    target = safe_child(out_dir, batch) if batch and not shared else None
    if target is not None and not (out_dir / batch.strip()).is_symlink() and target.is_dir():
        shutil.rmtree(target / "runs", ignore_errors=True)
        (target / "manifest.yaml").unlink(missing_ok=True)
        final = target / "_final"
        if with_videos and final.is_dir():
            videos = len(final_names(out_dir, batch))
            shutil.rmtree(final, ignore_errors=True)
        if not any(target.iterdir()):
            target.rmdir()
            # out/latest pointed at the newest batch; left dangling it would
            # point at nothing.
            latest = out_dir / "latest"
            if latest.is_symlink() and not latest.exists():
                latest.unlink()
    return Outcome(True, "deleted"), videos
