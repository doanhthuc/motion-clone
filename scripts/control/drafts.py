"""Composing jobs: the draft logic shared by the Telegram bot and the HTTP API.

Pure helpers moved out of bot.py (spec 2026-09-21 §6, slice 3). The bot keeps
its own in-memory drafts and calls these; the phone's draft lives in
DraftStore below.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path

from batchlib.pipelines import PIPELINES, optional_roles, required_roles
from tgbot.ingest import Probe
from tgbot.job import DEFAULT_PROVIDER, Job, _tryon_stage, missing_slots

# Try-on providers offered from the bot. gemini/qwen-max run directly from
# THIS process (batchlib/runner.py's run_local_phase, invoked by drain.py's
# Phase A) before a pod is ever rented — the self-host default still needs the
# pod, same as every pipeline's motion/character-swap/enhance stage always has.
#
# "qwen-max" here is what the user calls "Qwen Image 3.0 Pro" — the DashScope
# Qwen-Image API (batchlib/local_tryon.py:394-398). Needs DASHSCOPE_API_KEY
# AND QWEN_IMAGE_WORKSPACE (or QWEN_IMAGE_BASE) in the VPS's .env; missing
# either raises a ConfigError from run_local_phase, same as a missing
# GEMINI_API_KEY does for gemini. The model actually called is
# QWEN_IMAGE_MODEL (defaults to "qwen-image-edit-plus", GA) — "qwen-image-3.0-
# pro" specifically was limited preview at local_tryon.py:395-397 and only
# runs once that env var is set to it; the button does not promise 3.0 by name.
PROVIDER_LABELS = {"qwen": "🖥 Self-host (qwen) — needs the GPU pod",
                  "gemini": "☁️ Gemini API — runs here, no pod wait; "
                            "may crop product photos less precisely than "
                            "the pod path",
                  "qwen-max": "☁️ Qwen-Image API (DashScope) — runs here, "
                              "no pod wait; same crop caveat as Gemini"}

# Structural, not a question (job.py's slot_for): a video can only be the driver.
VIDEO_ROLES = frozenset({"driver"})


def copy_job(job: Job) -> Job:
    """A detached copy. The basket must not alias the job still being edited.

    Job holds plain dicts, so appending the live object and carrying on editing
    it would silently rewrite an entry the user already committed to the batch.
    """
    return Job(pipeline=job.pipeline, slots=dict(job.slots),
               probes=dict(job.probes), provider=job.provider)


def signature(job: Job) -> tuple:
    """What makes two runs the same run — pipeline, material, and provider.

    Provider is part of the identity, not just a cosmetic setting: same
    material through gemini vs qwen is two different runs (different API,
    different cost, possibly different output) — collapsing them into "the
    same job" would make _job_digest collide and an edit/drop tap on one
    basket row silently act on the other.
    """
    return (job.pipeline, job.provider,
            tuple(sorted((r, str(p)) for r, p in job.slots.items())))


def job_digest(job: Job) -> str:
    """A short, stable handle for one queued job, for callback_data.

    Keyed on the job's own material rather than its position in the basket. An
    index would be a stale-button hazard: the keyboard on an older panel still
    works — Telegram never expires one — so `bj:d:2` tapped after the batch has
    changed would delete whatever is second NOW. A digest simply fails to match
    and says so, which is the same reasoning as _run_token's staleness guard on
    the money button.
    """
    return hashlib.sha256(repr(signature(job)).encode()).hexdigest()[:10]


def jobs_for(current: Job | None, basket: list[Job]) -> list[Job]:
    """What Run would submit: the batch, plus the job being edited when it is
    complete and not already an exact copy of a batch entry."""
    jobs = list(basket)
    if current is None or missing_slots(current):
        return jobs
    if any(signature(current) == signature(other) for other in jobs):
        return jobs
    return jobs + [current]


def drop_unusable(job: Job, pipeline: str) -> list[str]:
    """Switch `job` to `pipeline`, dropping the slots it cannot use.

    Slots and probes are dropped together: every renderer assumes
    set(job.probes) == set(job.slots). The caller validates the name.
    """
    usable = required_roles(pipeline) | optional_roles(pipeline)
    dropped = sorted(set(job.slots) - usable)
    for role in dropped:
        job.slots.pop(role, None)
        job.probes.pop(role, None)
    job.pipeline = pipeline
    return dropped


def dump_jobs(jobs: list[Job]) -> list[dict]:
    return [{"pipeline": j.pipeline,
             "provider": j.provider,
             "slots": {r: str(v) for r, v in j.slots.items()},
             "probes": {r: asdict(pr) for r, pr in j.probes.items()}}
            for j in jobs]


def load_jobs(payload: list) -> list[Job]:
    return [Job(pipeline=entry["pipeline"],
                # .get, not entry["provider"]: a basket dumped by a previous
                # version of this bot has no such key, and refusing to load an
                # otherwise good job over a missing cosmetic field is exactly
                # the failure _load_draft's own docstring warns against.
                provider=entry.get("provider", DEFAULT_PROVIDER),
                slots={r: Path(v) for r, v in entry["slots"].items()},
                probes={r: Probe(**d) for r, d in entry["probes"].items()})
            for entry in payload]


def role_kind(role: str) -> str:
    return "video" if role in VIDEO_ROLES else "image"


def pipeline_catalog() -> list[dict]:
    """Everything the phone needs to draw a pipeline picker, so it never
    hardcodes one (spec §5.2). Providers only where a try-on stage exists:
    elsewhere the provider is never read (render_manifest, job.py)."""
    catalog = []
    for name in sorted(PIPELINES):
        required, optional = required_roles(name), optional_roles(name)
        catalog.append({
            "id": name,
            "stages": list(PIPELINES[name]),
            "required": sorted(required),
            "optional": sorted(optional),
            "roles": {role: role_kind(role) for role in sorted(required | optional)},
            "providers": ([{"id": pid, "label": label} for pid, label in PROVIDER_LABELS.items()]
                          if _tryon_stage(name) is not None else []),
        })
    return catalog
