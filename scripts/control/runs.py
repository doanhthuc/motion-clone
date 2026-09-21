"""Runs, read from the on-disk journals — never from the pod.

The journal (`batch/<id>.state.json`) is the same source `progress_text` and
`batch_status` read, so a run stays reportable after `make gpu-destroy`: the
pod is the thing most likely to be gone by the time the phone asks.

Liveness comes from `tgbot.run` through the module, not through imported
names, so it sees this process's own `_RUNNING` / `_PHASE_A` handles — the
reason the API runs inside the bot process (spec §3, approach A).
"""
from __future__ import annotations

import time
from pathlib import Path

from batchlib.manifest import load_state, state_path_for
from control.outputs import final_names
from control.paths import safe_child
import tgbot.run as run_mod


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
            found.append(_summary(manifest, state_file))
    return sorted(found, key=lambda r: r["updated_at"], reverse=True)


def run_detail(batch_dir: Path, out_dir: Path, run_id: str) -> dict | None:
    manifest = safe_child(batch_dir, f"{run_id}.yaml") if run_id else None
    if manifest is None or not manifest.is_file():
        return None
    state_file = state_path_for(manifest)
    if not state_file.is_file():
        return None
    detail = _summary(manifest, state_file)
    jobs = load_state(state_file).get("runs") or {}
    # Whitelisted fields only: the journal also holds absolute `file` paths
    # and the full params_sent payload, neither of which the phone needs.
    detail["jobs"] = [
        {"id": job_id, "status": str(job.get("status")),
         "stages": [{"name": name,
                     "status": str(stage.get("status")),
                     "elapsed_sec": stage.get("elapsed_sec")}
                    for name, stage in (job.get("stages") or {}).items()
                    if isinstance(stage, dict)]}
        for job_id, job in jobs.items() if isinstance(job, dict)
    ]
    lease = run_mod.lease_for(manifest)
    detail["lease"] = None if lease is None else {
        "provider": lease.provider,
        "elapsed_sec": int(time.time() - lease.provisioned_at),
        "abs_max_min": lease.abs_max_min,
    }
    detail["outputs"] = final_names(out_dir, detail["batch"]) if detail["batch"] else []
    return detail
