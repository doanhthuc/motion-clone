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
from batchlib.runner import ARCHIVED_JOURNAL
from control.outputs import final_names
from control.paths import safe_child
import tgbot.run as run_mod
import runpod_community


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
    next to a real bill. RunPod Community (RUNPOD_CLOUD=COMMUNITY) quotes its
    own list price, still a constant for the same ETag reason."""
    if provider != "runpod":
        return None
    if runpod_community.cloud() == "COMMUNITY":
        return runpod_community.DEFAULT_USD_PER_HR
    return RUNPOD_FLAT_USD_PER_HR


def _status(manifest: Path, jobs: dict) -> str:
    if run_mod.drain_running(manifest):
        return "running"
    if run_mod.phase_a_running(manifest):
        return "phase_a"
    return _journal_status(jobs)


def _journal_status(jobs: dict) -> str:
    statuses = [str(job.get("status")) for job in jobs.values() if isinstance(job, dict)]
    if any(s == "error" for s in statuses):
        return "error"
    if statuses and all(s == "done" for s in statuses):
        return "done"
    return "stopped"


def _summary(manifest: Path, state_file: Path) -> dict:
    state = load_state(state_file)
    jobs = state.get("runs") or {}
    return _summary_of(manifest.stem, state.get("batch") or None, _status(manifest, jobs),
                       state_file.stat().st_mtime, jobs)


def _summary_of(run_id: str, batch: str | None, status: str, updated_at: float, jobs: dict) -> dict:
    return {
        "id": run_id,
        "batch": batch,
        "status": status,
        "updated_at": updated_at,
        "jobs_total": len(jobs),
        # Lets the phone raise a notification from the list poll alone (any tab), without
        # opening every run's detail. Set-once with the warning, so the ETag stays stable.
        "slow_stages": sum(1 for job in jobs.values() if isinstance(job, dict)
                           for st in (job.get("stages") or {}).values()
                           if isinstance(st, dict) and _slow_warning(st) is not None),
        "jobs_done": sum(1 for j in jobs.values()
                         if isinstance(j, dict) and j.get("status") == "done"),
    }


def _live_runs(batch_dir: Path) -> list[dict]:
    """Every journal that still has its manifest next to it."""
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
    return found


def busy_batches(batch_dir: Path) -> set[str]:
    """The `out/` batch names a draining or Phase A run is writing into."""
    return {r["batch"] for r in _live_runs(batch_dir)
            if r.get("batch") and run_mod.busy(batch_dir / f"{r['id']}.yaml")}


def list_runs(batch_dir: Path, out_dir: Path) -> list[dict]:
    """Live journals and past batches together, newest first."""
    live = _live_runs(batch_dir)
    taken = {r["batch"] for r in live} | {r["id"] for r in live}
    past = []
    for batch_out in sorted(out_dir.iterdir()) if out_dir.is_dir() else []:
        if batch_out.name in taken:
            continue
        try:
            summary = _archived_summary(batch_out)
        except FileNotFoundError:
            continue        # removed mid-scan, same race as above
        if summary is not None:
            past.append(summary)
    return sorted(live + past, key=lambda r: r["updated_at"], reverse=True)


# --- Past runs, read back from out/<batch>/ ---------------------------------
#
# The bot keeps one manifest per chat (`batch/tg-<chat>.yaml`), so each new run
# resets the journal the previous one wrote, and the phone could only ever see
# the latest run (2026-10-10). What a past batch leaves in `out/<batch>/` is
# enough to show it read-only: the manifest copy, plus either the journal the
# runner archives there when the next batch takes over (`ARCHIVED_JOURNAL`, from
# 2026-10-10 on) or `_index.tsv`, which every drain that reached its end wrote.
# A directory with neither never ran past validation and is left out.


def _archived_jobs(batch_out: Path) -> tuple[dict, float] | None:
    """journal-shaped `{job id: entry}` for a past batch, and when it last changed."""
    journal = batch_out / ARCHIVED_JOURNAL
    index = batch_out / "_index.tsv"
    if journal.is_file():
        jobs = {k: v for k, v in (load_state(journal).get("runs") or {}).items()
                if isinstance(v, dict)}
        updated_at = journal.stat().st_mtime
    elif index.is_file():
        jobs = _jobs_from_index(index)
        updated_at = index.stat().st_mtime
    else:
        return None
    for job_id in _job_setups(batch_out / "manifest.yaml"):
        jobs.setdefault(job_id, {"status": "pending", "stages": {}})
    return {job_id: _settled(entry) for job_id, entry in jobs.items()}, updated_at


def _jobs_from_index(index: Path) -> dict:
    """`_index.tsv` has one row per stage but only the JOB's status
    (runner.write_index). A stage that produced bytes is done; for a failed job
    its last row is the stage that stopped it (write_index's docstring)."""
    jobs: dict[str, dict] = {}
    for line in index.read_text(encoding="utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 6 or not cols[0]:
            continue
        job_id, status, stage, _job, elapsed, size = cols[:6]
        entry = jobs.setdefault(job_id, {"status": status, "stages": {}})
        entry["stages"][stage] = {"status": "done" if size else "pending",
                                  "elapsed_sec": int(elapsed) if elapsed.isdigit() else None}
    for entry in jobs.values():
        if entry["status"] == "error" and entry["stages"]:
            last = list(entry["stages"].values())[-1]
            if last["status"] != "done":
                last["status"] = "error"
    return jobs


def _settled(entry: dict) -> dict:
    """Nothing in a past batch is still running: a stage the journal left
    "running" was cut off when the next batch took over, so it reads as never
    finished rather than as live work (which would also keep a spinner going)."""
    def calm(status) -> str:
        return "pending" if str(status) == "running" else str(status)
    return {"status": calm(entry.get("status")),
            "stages": {name: {**st, "status": calm(st.get("status"))}
                       for name, st in (entry.get("stages") or {}).items() if isinstance(st, dict)}}


def _archived_dir(out_dir: Path, run_id: str) -> Path | None:
    batch_out = safe_child(out_dir, run_id) if run_id else None
    if (batch_out is None or (out_dir / run_id.strip()).is_symlink()
            or not (batch_out / "manifest.yaml").is_file()):
        return None
    return batch_out


def _archived_summary(batch_out: Path) -> dict | None:
    if batch_out.is_symlink() or not (batch_out / "manifest.yaml").is_file():
        return None
    found = _archived_jobs(batch_out)
    if found is None:
        return None
    jobs, updated_at = found
    return _summary_of(batch_out.name, batch_out.name, _journal_status(jobs), updated_at, jobs)


def _slow_warning(stage: dict) -> dict | None:
    """The runner's "this stage is far past its usual time" note, whitelisted for the phone.
    `at` is a fixed timestamp and the note is written once, so it never breaks the ETag rule."""
    warn = stage.get("slow_warning")
    if not isinstance(warn, dict) or str(stage.get("status")) != "running":
        return None
    return {"at": warn.get("at"), "ceiling_min": warn.get("ceiling_min"),
            "gpu": str(warn.get("gpu") or "unknown"), "detail": str(warn.get("detail") or "")}


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
    if manifest is None or not manifest.is_file() or not state_path_for(manifest).is_file():
        return _archived_detail(batch_dir, out_dir, run_id)
    state_file = state_path_for(manifest)
    detail = _summary(manifest, state_file)
    jobs = load_state(state_file).get("runs") or {}
    detail["jobs"] = _job_details(jobs, _job_setups(manifest))
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


def _job_details(jobs: dict, setups: dict[str, dict]) -> list[dict]:
    # Whitelisted fields only: the journal also holds absolute `file` paths
    # and the full params_sent payload, neither of which the phone needs.
    return [
        {"id": job_id, "status": str(job.get("status")),
         "stages": [{"name": name,
                     "status": str(stage.get("status")),
                     "elapsed_sec": stage.get("elapsed_sec"),
                     # Only while the stage is still running: the runner pops it on done.
                     "slow_warning": _slow_warning(stage)}
                    for name, stage in (job.get("stages") or {}).items()
                    if isinstance(stage, dict)],
         "setup": setups.get(job_id)}
        for job_id, job in jobs.items() if isinstance(job, dict)
    ]


def _archived_detail(batch_dir: Path, out_dir: Path, run_id: str) -> dict | None:
    """A past batch's detail: the same shape, never a lease, never live."""
    batch_out = _archived_dir(out_dir, run_id)
    if batch_out is None or any(r["batch"] == batch_out.name for r in _live_runs(batch_dir)):
        return None
    detail = _archived_summary(batch_out)
    if detail is None:
        return None
    jobs, _ = _archived_jobs(batch_out)
    detail["jobs"] = _job_details(jobs, _job_setups(batch_out / "manifest.yaml"))
    detail["lease"] = None
    detail["outputs"] = final_names(out_dir, batch_out.name)
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
    the manifest copy, `_index.tsv` and archived journal there. `_final/` — the videos in Outputs — goes only
    with `with_videos`, and never while another run's journal still names
    the same batch directory.

    Refused while anything holds the manifest: a drain or Phase A reads it,
    and a lease means a pod is billed against it. The caller holds BOT_LOCK
    so neither can start between this check and the unlink.
    """
    manifest = safe_child(batch_dir, f"{run_id}.yaml") if run_id else None
    if manifest is None or not manifest.is_file() or not state_path_for(manifest).is_file():
        return _delete_archived(batch_dir, out_dir, run_id, with_videos=with_videos)
    if run_mod.busy(manifest) or run_mod.lease_for(manifest) is not None:
        return Outcome(False, "run_busy", "the run is running or has a pod; kill it first"), 0
    batch = load_state(state_path_for(manifest)).get("batch") or None
    shared = batch is not None and any(
        r["batch"] == batch for r in _live_runs(batch_dir) if r["id"] != manifest.stem)

    for suffix in _RUN_FILE_SUFFIXES:
        manifest.with_name(manifest.stem + suffix).unlink(missing_ok=True)

    target = safe_child(out_dir, batch) if batch and not shared else None
    if target is None or (out_dir / batch.strip()).is_symlink() or not target.is_dir():
        return Outcome(True, "deleted"), 0
    return Outcome(True, "deleted"), _remove_batch_outputs(out_dir, target, with_videos=with_videos)


def _delete_archived(batch_dir: Path, out_dir: Path, run_id: str, *,
                     with_videos: bool) -> tuple[Outcome, int]:
    """A past batch has no journal in batch/ and nothing can hold it, so only
    its out/ directory goes. Once its manifest copy is gone it leaves the list;
    the videos, kept by default, stay in Outputs."""
    batch_out = _archived_dir(out_dir, run_id)
    if batch_out is None or any(r["batch"] == batch_out.name for r in _live_runs(batch_dir)):
        return Outcome(False, "not_found", "no such run"), 0
    return Outcome(True, "deleted"), _remove_batch_outputs(out_dir, batch_out, with_videos=with_videos)


def _remove_batch_outputs(out_dir: Path, target: Path, *, with_videos: bool) -> int:
    """Everything a run left in `out/<batch>/` but its videos, and those too
    with `with_videos`. Returns how many videos went."""
    shutil.rmtree(target / "runs", ignore_errors=True)
    for name in ("manifest.yaml", "_index.tsv", ARCHIVED_JOURNAL):
        (target / name).unlink(missing_ok=True)
    videos = 0
    final = target / "_final"
    if with_videos and final.is_dir():
        videos = len(final_names(out_dir, target.name))
        shutil.rmtree(final, ignore_errors=True)
    if not any(target.iterdir()):
        target.rmdir()
        # out/latest pointed at the newest batch; left dangling it would
        # point at nothing.
        latest = out_dir / "latest"
        if latest.is_symlink() and not latest.exists():
            latest.unlink()
    return videos
