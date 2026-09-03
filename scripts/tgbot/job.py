"""Assemble one job's files into the manifest the existing batch runner consumes.

Pure: no Telegram types, no subprocess, no I/O beyond writing the manifest
text itself (`write_manifest`). Required slots are read from
`batchlib.pipelines.PIPELINES`/`STAGES` rather than hardcoded here, so a new
pipeline added there cannot silently ask the user for the wrong files — the
mistake would otherwise only surface as a wasted paid render.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from batchlib.pipelines import PIPELINES, STAGES, required_roles

from .ingest import Probe, describe, suggest_preset


@dataclass
class Job:
    slots: dict[str, Path]
    probes: dict[str, Probe]
    pipeline: str


def _driver_stage(pipeline: str) -> str | None:
    """Which stage of `pipeline` actually consumes the `driver` material.

    STAGES has no notion of "video" vs "image" — that typing lives only in
    ingest.Probe. So which *role* a video maps to (driver) is domain
    knowledge, not something derivable from STAGES; but which *stage*
    consumes that role for a given pipeline IS derivable, and deriving it
    avoids hardcoding "motion" when a pipeline instead runs character-swap.
    """
    for stage_name in PIPELINES.get(pipeline, []):
        stage = STAGES[stage_name]
        for source in stage.inputs.values():
            if "material:driver" in source.split("|"):
                return stage_name
    return None


def slot_for(probe: Probe, job: Job) -> str | None:
    """Infer a slot from structural fact alone, never from a filename guess.

    A video can only ever be the `driver`: across every Stage in
    batchlib/pipelines.py, "material:driver" is the sole material role ever
    fed by a video file (motion's `motion` field, character-swap's `video`
    field). An image, by contrast, is genuinely ambiguous among character,
    outfit and background — guessing there would burn a real $1 batch on a
    mislabeled outfit, so we return None and let the caller ask.
    """
    if probe.kind == "video":
        return "driver"
    return None


def missing_slots(job: Job) -> list[str]:
    """Required slots not yet filled, derived from PIPELINES/STAGES.

    Never a hardcoded list: required_roles()/optional_roles() walk the real
    Stage.inputs declarations, so a pipeline added later without a matching
    hardcoded list here can't silently ask for the wrong files.
    """
    needed = required_roles(job.pipeline)
    return sorted(needed - set(job.slots))


def run_id_for(job: Job) -> str:
    """A readable id for one run, built from the material it uses.

    The same shape the user writes by hand — `c1-o4-m1-b1` in
    batch/2026-08-28-lanczos-6cap.yaml — because the id is what names the
    output directory, the `_final/<id>.mp4` file and every row in the journal.
    An opaque `job-3` would make a six-run batch unreadable at exactly the
    moment it matters, which is when one of the six came out wrong.

    Order is character, outfit, background, driver: what varies most often
    comes first, so the ids of a batch differ early rather than in their tails.
    """
    order = ("character", "outfit", "background", "driver")
    parts = []
    for role in order:
        if role not in job.slots:
            continue
        # 12 characters is enough to tell IMG_6781 from IMG_6783 while keeping
        # a four-slot id short enough to read on a phone.
        stem = re.sub(r"[^A-Za-z0-9]+", "", Path(job.slots[role]).stem)[:12]
        if stem:
            parts.append(stem)
    return "-".join(parts) or "job"


def _unique_ids(jobs: list[Job]) -> list[str]:
    """One id per job, none repeated.

    batchlib.manifest refuses a manifest with two runs sharing an id — "output
    sẽ đè lên nhau" — and it is right to: the second run would overwrite the
    first one's finished video. Two jobs CAN legitimately share material and
    differ only by pipeline, so the suffix is not a sign of a mistake.
    """
    seen: dict[str, int] = {}
    ids = []
    for job in jobs:
        base = run_id_for(job)
        seen[base] = seen.get(base, 0) + 1
        ids.append(base if seen[base] == 1 else f"{base}-{seen[base]}")
    return ids


def render_manifest(jobs: list[Job], *, now) -> str:
    """Render the YAML the existing batch runner (`batchlib.manifest`) loads.

    Takes a LIST since 2026-09-01. One pod runs every job in the manifest, and
    provisioning is paid once per drain rather than once per job — the reason
    the user asked for it: "tận dụng tối đa thời gian thuê gpu tránh chờ gpu
    khởi động tốn thời gian chờ và tiền trong lúc chờ nữa". The runner, the
    journal and `final_files` were already multi-run; this writer was the only
    thing pinning the bot to one.

    The header carries the ffprobe numbers as dated comments — what a user
    writes by hand today per docs/batch-runner.md — so the file still explains
    itself six weeks later. Where a job has a driver, the same duration that
    produced the comment also becomes a real `preset:` param: without it the
    worker falls back to its own frame-count default (81 frames for `motion`,
    `drv-5s` for `character-swap` — linux.py:1417, :5261), unrelated to the
    driver's actual measured length, after the pod was already rented.
    """
    ids = _unique_ids(jobs)
    lines = [f"# Generated by the Telegram bot at {now}.",
             f"# {len(jobs)} run(s), one pod."]
    for run_id, job in zip(ids, jobs):
        lines.append(f"# {run_id}: {job.pipeline}")
        for slot in sorted(job.probes):
            probe = job.probes[slot]
            line = f"#   {slot}: {describe(probe)}"
            if probe.kind == "video":
                line += f" -> preset {suggest_preset(probe.duration_s)}"
            lines.append(line)

    lines.append("")
    lines.append("runs:")
    for run_id, job in zip(ids, jobs):
        lines.append(f"  - id: {run_id}")
        lines.append(f"    pipeline: {job.pipeline}")
        lines.append("    inputs:")
        for slot in sorted(job.slots):
            lines.append(f"      {slot}: {job.slots[slot]}")
        driver_stage = _driver_stage(job.pipeline)
        driver_probe = job.probes.get("driver")
        if driver_stage and driver_probe is not None:
            preset = suggest_preset(driver_probe.duration_s)
            lines.append(f"    {driver_stage}: {{ preset: {preset} }}")
        lines.append("")

    return "\n".join(lines)


def write_manifest(jobs: list[Job], path: Path, *, now) -> None:
    """Write the manifest once, before the run. The runner never writes it.

    Byte-identical to what the user approved: this is the only place the
    bot renders text to disk, and it always renders the same job the same
    way given the same `now`, so a re-render before the actual run cannot
    drift from what was shown on the confirmation screen.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_manifest(jobs, now=now), encoding="utf-8")
