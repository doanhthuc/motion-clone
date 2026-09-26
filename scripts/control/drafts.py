"""Composing jobs: the draft logic shared by the Telegram bot and the HTTP API.

Pure helpers moved out of bot.py (spec 2026-09-21 §6, slice 3). The bot keeps
its own in-memory drafts and calls these; the phone's draft lives in
DraftStore below.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import control
from batchlib.local_tryon import is_local_provider
from batchlib.pipelines import PIPELINES, optional_roles, required_roles
from control import materials
from control.tryon_library import TryonLibrary
from tgbot import ingest
from tgbot.ingest import Probe
from tgbot.job import DEFAULT_PROVIDER, Job, _tryon_stage, _unique_ids, missing_slots, write_manifest
from tgbot.run import estimate_minutes

# Cloudflare answers 524 when the origin sends nothing for 100 s, so the
# phone's validate must finish (or give up) before that. The bot allows 120 s,
# "~100x the observed runtime"; 90 s is still ~75x.
VALIDATE_TIMEOUT_SEC = 90
# The tail of the validator's output returned to the phone: enough for every
# error line batch_run.py prints, bounded so a runaway log stays small.
VALIDATE_OUTPUT_MAX_CHARS = 4000
# One validate at a time: `make batch-validate` peaks ~111 MB RSS for 0.38s
# (measured 2026-09-21, mostly batch_run.py parsing linux.py with ast). The VPS
# has 1 GB for the bot, cloudflared and a Phase A drain, and a phone retrying
# while the box swaps would otherwise stack one of these per retry. Non-blocking: a second caller gets a fast "busy" instead
# of queueing behind the first for up to VALIDATE_TIMEOUT_SEC.
_VALIDATE_SLOTS = threading.BoundedSemaphore(1)

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
               probes=dict(job.probes), provider=job.provider,
               tryon_seed=job.tryon_seed)


def signature(job: Job) -> tuple:
    """What makes two runs the same run — pipeline, material, provider, seed.

    Provider is part of the identity, not just a cosmetic setting: same
    material through gemini vs qwen is two different runs (different API,
    different cost, possibly different output) — collapsing them into "the
    same job" would make _job_digest collide and an edit/drop tap on one
    basket row silently act on the other.

    The try-on seed (§5.10, slice 6) is part of it for exactly the same
    reason: the same four materials seeded from a saved image and run fresh
    produce different try-ons at different cost.
    """
    return (job.pipeline, job.provider,
            str(job.tryon_seed) if job.tryon_seed else None,
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
             "probes": {r: asdict(pr) for r, pr in j.probes.items()},
             "tryon_seed": str(j.tryon_seed) if j.tryon_seed else None}
            for j in jobs]


def load_jobs(payload: list) -> list[Job]:
    return [Job(pipeline=entry["pipeline"],
                # .get, not entry["provider"]: a basket dumped by a previous
                # version of this bot has no such key, and refusing to load an
                # otherwise good job over a missing cosmetic field is exactly
                # the failure _load_draft's own docstring warns against.
                provider=entry.get("provider", DEFAULT_PROVIDER),
                slots={r: Path(v) for r, v in entry["slots"].items()},
                probes={r: Probe(**d) for r, d in entry["probes"].items()},
                # .get for the same reason as provider above: a draft written
                # before slice 6 has no such key.
                tryon_seed=Path(entry["tryon_seed"]) if entry.get("tryon_seed") else None)
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


class DraftError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class _Draft:
    job: Job
    basket: list[Job] = field(default_factory=list)
    # Tri-state, as the bot's _LAST_VALIDATE: None = never checked since the
    # last change, True/False = the verdict of make batch-validate.
    validated: bool | None = None
    generation: int = 0


_PATCH_KEYS = frozenset({"pipeline", "provider", "slots", "tryon_seed"})
_EDIT_KEYS = _PATCH_KEYS - {"pipeline"}


def _seed_id(seed: Path | None) -> str | None:
    """The try-on library id a job's seed points at, for the draft view.

    TryonLibrary.save names every image `{id}{ext}`, so the stem is the id.
    Reported even when the entry was deleted since — the phone then says the
    saved try-on is gone instead of silently showing an ordinary job.
    """
    return seed.stem if seed else None


class DraftStore:
    """One owner's draft, on disk at batch/<owner>.draft.json.

    Read and written on every call under control.LOCK instead of cached in
    memory: the file is a few KB, and a per-request read removes the bot's
    load-once/save-in-finally envelope along with its check-then-act races.
    Slow work (ffprobe here, make batch-validate in validate()) runs outside
    the lock.
    """

    def __init__(self, batch_dir: Path, staging_root: Path, owner: str, *,
                 default_pipeline: str, default_provider: str,
                 tryon_library: TryonLibrary, probe=ingest.probe, material_roles=None):
        self.batch_dir, self.owner = batch_dir, owner
        # Resolved once here so it matches materials.resolve_material's own
        # resolved paths (control/paths.py's safe_child): on macOS /var is a
        # symlink to /private/var, so an un-resolved staging_root silently
        # failed every path.relative_to() in _material_id below, and every
        # slot in the view came back with material_id: null (measured
        # 2026-09-21, tempfile.mkdtemp() under /var/folders/...).
        self.staging_root = staging_root.resolve()
        self.default_pipeline, self.default_provider = default_pipeline, default_provider
        # Required, no default: a store built without a library silently
        # refuses every tryon_seed with "no such entry" — a bug the test
        # suite should fail on, not paper over.
        self.tryon_library = tryon_library
        self._probe = probe
        # control.material_roles.MaterialRoles, or None (the bot's own store):
        # a material put into a slot is tagged with that role, so the phone's
        # library can group it even after the draft is cleared.
        self.material_roles = material_roles
        self.path = batch_dir / f"{owner}.draft.json"

    # -- persistence -------------------------------------------------------

    def _fresh(self, generation: int = 0) -> _Draft:
        return _Draft(job=Job(slots={}, probes={}, pipeline=self.default_pipeline,
                              provider=self.default_provider), generation=generation)

    def _load(self) -> _Draft:
        if not self.path.exists():
            return self._fresh()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            [current] = load_jobs([payload["job"]])
            if current.pipeline not in PIPELINES:
                raise ValueError(f"unknown pipeline {current.pipeline!r}")
            return _Draft(job=current, basket=load_jobs(payload["basket"]),
                          validated=payload["validated"], generation=int(payload["generation"]))
        except (ValueError, KeyError, TypeError, AttributeError):
            # AttributeError: a hand-edited draft can carry the right keys
            # with the wrong shape (e.g. "slots": [] instead of {}), and
            # load_jobs's own `.items()` call raises that, not KeyError.
            # Moved aside, never deleted: it is the only copy of what the
            # user had composed (same rule as the bot's _load_draft). A
            # unique suffix, not a fixed ".bad", so a second corrupt file
            # never overwrites the first one moved aside.
            self.path.replace(self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.bad"))
            return self._fresh()

    def _save(self, d: _Draft) -> None:
        payload = {"job": dump_jobs([d.job])[0], "basket": dump_jobs(d.basket),
                   "validated": d.validated, "generation": d.generation}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{self.path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def _changed(self, d: _Draft) -> dict:
        d.validated = None
        d.generation += 1
        self._save(d)
        return self._view(d)

    # -- rendering ---------------------------------------------------------

    def _material_id(self, path: Path) -> str | None:
        try:
            rel = path.relative_to(self.staging_root)
        except ValueError:
            return None
        return "/".join(rel.parts) if len(rel.parts) == 2 else None

    def _missing(self, job: Job) -> list[str]:
        present = {r for r, p in job.slots.items() if p.is_file()}
        return sorted(required_roles(job.pipeline) - present)

    def _jobs(self, d: _Draft) -> list[Job]:
        """What Run would submit right now: the basket, plus the job being
        edited only when it is actually complete.

        `jobs_for`'s own completeness check (`missing_slots`) only looks at
        which roles have a dict entry, not whether that entry's file still
        exists on disk — so a vanished file (`_missing` counts
        that as missing, `missing_slots` does not) left the current job
        counted as one of `jobs` while `missing` also listed it. Passing
        `None` here instead of the incomplete job is what `validate()`
        needs too, hence the shared helper.
        """
        current = d.job if not self._missing(d.job) else None
        return jobs_for(current, d.basket)

    def _view(self, d: _Draft) -> dict:
        job = d.job
        jobs = self._jobs(d)
        # _unique_ids, not a per-entry run_id_for: two basket entries can share
        # every slot and differ only by provider (signature() counts that as a
        # different job, so both can sit in the basket at once), and
        # render_manifest suffixes the second one "-2" so the runner doesn't
        # overwrite one output with the other. The view must show the id the
        # manifest will actually give each row, or the app's batch list would
        # print the same run_id twice.
        basket_ids = _unique_ids(d.basket)
        return {
            "owner": self.owner, "pipeline": job.pipeline, "provider": job.provider,
            "generation": d.generation,
            "slots": {role: {"material_id": self._material_id(path), "name": path.name,
                             "exists": path.is_file(), "probe": asdict(job.probes[role]),
                             "warning": ingest.quality_warning(job.probes[role])}
                      for role, path in sorted(job.slots.items())},
            "required": sorted(required_roles(job.pipeline)),
            "optional": sorted(optional_roles(job.pipeline)),
            "missing": self._missing(job),
            "validated": d.validated,
            "tryon_seed": _seed_id(job.tryon_seed),
            "batch": [{"digest": job_digest(b), "run_id": run_id, "pipeline": b.pipeline,
                       "provider": b.provider,
                       "tryon_seed": _seed_id(b.tryon_seed),
                       "slots": {r: self._material_id(p) for r, p in sorted(b.slots.items())}}
                      for b, run_id in zip(d.basket, basket_ids)],
            "jobs": len(jobs),
            "estimate_min": sum(estimate_minutes(j) for j in jobs) if d.validated is True else None,
        }

    def view(self) -> dict:
        with control.LOCK:
            return self._view(self._load())

    def runnable(self) -> tuple[list[Job], bool | None, int]:
        """What Phase A / confirm need from this draft: the jobs `view()` would
        report, the last validate verdict, and the generation it belongs to —
        read together under one lock acquisition so a concurrent edit cannot
        change the jobs after the verdict was read but before the caller acts
        on it."""
        with control.LOCK:
            d = self._load()
            return self._jobs(d), d.validated, d.generation

    # -- mutations ---------------------------------------------------------

    def _resolve(self, material_id) -> Path:
        if not isinstance(material_id, str) or material_id.count("/") != 1:
            raise DraftError("not_found", "no such material")
        owner, name = material_id.split("/")
        path = materials.resolve_material(self.staging_root, owner, name)
        if path is None:
            raise DraftError("not_found", f"no such material: {material_id}")
        return path

    def _prepare(self, body: dict, keys: frozenset) -> tuple:
        """Everything about a patch body that needs no lock: its shape, the
        named pipeline/provider/seed, and each material resolved and probed.
        Shared by the draft's patch and a batch entry's edit."""
        if not isinstance(body, dict) or not body or set(body) - keys:
            raise DraftError("bad_request", "expected an object with " + ", ".join(sorted(keys)))
        pipeline, provider, slots = body.get("pipeline"), body.get("provider"), body.get("slots", {})
        if pipeline is not None and not isinstance(pipeline, str):
            raise DraftError("bad_request", "pipeline must be a string")
        if provider is not None and not isinstance(provider, str):
            raise DraftError("bad_request", "provider must be a string")
        if not isinstance(slots, dict):
            raise DraftError("bad_request", "slots must be an object of role -> material id or null")
        # Resolved outside the lock like the slot probes below, for the same
        # reason: resolve_image takes control.LOCK itself, and control.LOCK is
        # reentrant only for the thread that already holds it — doing this
        # inside the caller's `with` would work, but would also make the lock's
        # "slow work stays outside" rule one exception weaker.
        tryon_seed = body.get("tryon_seed")
        if tryon_seed is not None and not isinstance(tryon_seed, str):
            raise DraftError("bad_request", "tryon_seed must be a string id or null")
        seed_path: Path | None = None
        if tryon_seed:
            seed_path = self.tryon_library.resolve_image(tryon_seed)
            if seed_path is None:
                raise DraftError("seed_not_found", f"no such try-on library entry: {tryon_seed}")
        if pipeline is not None and pipeline not in PIPELINES:
            raise DraftError("unknown_pipeline", f"unknown pipeline {pipeline!r}")
        if provider is not None and provider not in PROVIDER_LABELS:
            raise DraftError("unknown_provider", f"unknown provider {provider!r}")

        # Outside the lock: ffprobe takes up to 60 s per file (ingest.probe).
        filled: dict[str, tuple[Path, Probe]] = {}
        for role, material_id in slots.items():
            if material_id is None:
                continue
            path = self._resolve(material_id)
            try:
                filled[role] = (path, self._probe(path))
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
                # Not str(exc): ffprobe's own error text names the absolute
                # staged path (ROOT/batch/tg-staging/...), and this message
                # goes straight to the phone. path.name is what the user
                # already sees in the slot; nothing else in the exception is
                # theirs to see.
                raise DraftError("unprobeable", f"{path.name} could not be read as media")
        return pipeline, provider, slots, seed_path, filled

    def _apply(self, job: Job, body: dict, prepared: tuple) -> list[str]:
        """Check a prepared patch against `job` and apply it; called under
        control.LOCK. Every check runs before the first write, so a refusal
        leaves `job` as it was. Returns the roles a pipeline switch dropped."""
        pipeline, provider, slots, seed_path, filled = prepared
        target = pipeline or job.pipeline
        usable = required_roles(target) | optional_roles(target)
        for role in slots:
            if role not in usable:
                raise DraftError("unknown_role", f"{target} has no {role!r} slot")
        for role, (path, probed) in filled.items():
            if probed.kind != role_kind(role):
                raise DraftError("wrong_kind",
                                 f"{role} must be {role_kind(role)}, {path.name} is {probed.kind}")
        if provider is not None and _tryon_stage(target) is None:
            raise DraftError("not_applicable", f"{target} has no try-on stage to pick a provider for")
        # Both halves of the post-patch job, not the stored one: setting a
        # seed and a local provider in the SAME patch is a good request.
        #
        # A seed beside a non-local provider is never read: runner.py's
        # _local_tryon_stage only names a stage whose provider passes
        # is_local_provider, so Phase A skips the run and the job goes on to
        # a real, PAID pod try-on while the caller believes they asked for a
        # reuse. Refuse loudly instead — the same call the bot's _regen_tryon
        # already refuses with "not_local" for the same underlying condition.
        resolved_provider = provider if provider is not None else job.provider
        resolved_seed = seed_path if "tryon_seed" in body else job.tryon_seed
        if (resolved_seed is not None and _tryon_stage(target) is not None
                and not is_local_provider(resolved_provider)):
            raise DraftError("not_local",
                             "tryon_seed only applies to a local try-on provider "
                             "(gemini or qwen-max) — switch the provider first")
        # Re-checked under the lock, right before applying: ffprobe ran
        # outside the lock, so a delete could land in the gap between
        # resolving/probing a path and getting here.
        for role, (path, probed) in filled.items():
            if not path.is_file():
                raise DraftError("not_found", f"no such material: {path.name}")
        # Every check passed: apply. Nothing above wrote anything.
        dropped = drop_unusable(job, target) if pipeline is not None else []
        if provider is not None:
            job.provider = provider
        for role, material_id in slots.items():
            if material_id is None:
                job.slots.pop(role, None)
                job.probes.pop(role, None)
            else:
                job.slots[role], job.probes[role] = filled[role]
        if "tryon_seed" in body:
            job.tryon_seed = seed_path
        return dropped

    def _remember_roles(self, slots: dict) -> None:
        if self.material_roles is not None:
            for role, material_id in slots.items():
                if material_id is not None:
                    self.material_roles.remember(material_id, role)

    def patch(self, body: dict) -> dict:
        prepared = self._prepare(body, _PATCH_KEYS)
        with control.LOCK:
            d = self._load()
            dropped = self._apply(d.job, body, prepared)
            view = self._changed(d)
        self._remember_roles(prepared[2])
        view["dropped"] = dropped
        return view

    def edit_batch(self, digest: str, body: dict) -> dict:
        """Change a queued job's material, provider or seed where it sits in
        the batch (2026-09-26). The pipeline stays: switching it drops slots,
        and a queued job that silently lost one is worse than drop and re-add.
        Its digest changes with its signature; the view carries the new one."""
        prepared = self._prepare(body, _EDIT_KEYS)
        with control.LOCK:
            d = self._load()
            index = next((i for i, b in enumerate(d.basket) if job_digest(b) == digest), None)
            if index is None:
                raise DraftError("not_found", "that entry is no longer in the batch")
            # Edited on a copy, so the duplicate check below can still refuse
            # without having touched the entry.
            edited = copy_job(d.basket[index])
            self._apply(edited, body, prepared)
            if self._missing(edited):
                raise DraftError("missing_slots", "a queued job keeps every required slot: "
                                 + ", ".join(self._missing(edited)))
            if any(signature(edited) == signature(other)
                   for i, other in enumerate(d.basket) if i != index):
                raise DraftError("duplicate", "that exact job is already in the batch")
            d.basket[index] = edited
            view = self._changed(d)
        self._remember_roles(prepared[2])
        return view

    def add_to_batch(self) -> dict:
        with control.LOCK:
            d = self._load()
            if self._missing(d.job):
                raise DraftError("missing_slots",
                                 "fill every required slot first: " + ", ".join(self._missing(d.job)))
            if any(signature(d.job) == signature(other) for other in d.basket):
                raise DraftError("duplicate", "that exact job is already in the batch")
            # Two copies: the batch entry and the job still being edited must
            # not share slot dicts (bot's _add_to_batch).
            d.basket.append(copy_job(d.job))
            d.job = copy_job(d.job)
            return self._changed(d)

    def drop_from_batch(self, digest: str) -> dict:
        with control.LOCK:
            d = self._load()
            index = next((i for i, b in enumerate(d.basket) if job_digest(b) == digest), None)
            if index is None:
                raise DraftError("not_found", "that entry is no longer in the batch")
            d.basket.pop(index)
            return self._changed(d)

    def clear(self) -> dict:
        with control.LOCK:
            # The generation keeps counting, so a validate that started before
            # the clear cannot record its verdict on the empty draft.
            d = self._fresh(generation=self._load().generation)
            return self._changed(d)

    def validate(self, *, repo_root: Path, run=None) -> dict:
        """make batch-validate on what Run would submit. Free: no pod.

        The manifest is rendered under the lock and checked outside it; the
        verdict is recorded only if the draft did not change meanwhile.
        """
        # Acquired OUTSIDE control.LOCK and non-blocking, before anything else
        # in this call: a second phone tapping validate while the first is
        # still running must get an immediate "busy", not wait behind it
        # holding (or waiting on) the lock every other mutation needs too.
        if not _VALIDATE_SLOTS.acquire(blocking=False):
            raise DraftError("busy", "a validation is already running — try again in a moment")
        try:
            with control.LOCK:
                d = self._load()
                jobs = self._jobs(d)
                if not jobs:
                    raise DraftError("nothing_to_validate", "no complete job to validate yet")
                generation = d.generation
                # A subdirectory: materials._in_use and runs.list_runs scan
                # batch/*.yaml, and this file is never a run.
                manifest = self.batch_dir / ".validate" / f"{self.owner}-{uuid.uuid4().hex}.yaml"
                try:
                    write_manifest(jobs, manifest, now=time.strftime("%Y-%m-%d %H:%M:%S"))
                except Exception:
                    # write_manifest is a plain path.write_text, not an
                    # atomic rename, so a failure partway (disk full, OSError)
                    # can leave a partial file sitting in batch/.validate/
                    # forever — nothing else ever looks in there to clean it.
                    manifest.unlink(missing_ok=True)
                    raise
            run = run or subprocess.run
            try:
                # Run the validator directly rather than through `make`:
                # subprocess.run(timeout=) kills the process it started
                # (`make`), which orphans the python3 child actually doing the
                # work — the timeout stopped enforcing anything (measured
                # 2026-09-21). This is exactly what the Makefile target runs
                # (root Makefile: `python3 scripts/batch_run.py --file
                # "$(FILE)" --validate-only`), so behaviour is unchanged.
                result = run([sys.executable, "scripts/batch_run.py", "--file", str(manifest),
                             "--validate-only"], cwd=repo_root, capture_output=True, text=True,
                             timeout=VALIDATE_TIMEOUT_SEC)
                ok, output = result.returncode == 0, (result.stdout + result.stderr).strip()
            except subprocess.TimeoutExpired:
                ok, output = False, f"validation did not finish within {VALIDATE_TIMEOUT_SEC}s"
            finally:
                manifest.unlink(missing_ok=True)
            # Staged files are named by absolute path in the validator's errors.
            # DraftStore resolves staging_root (macOS's /var is a symlink to
            # /private/var), so those paths are under repo_root.resolve() even
            # when the caller passed repo_root unresolved — strip both forms.
            output = output.replace(str(repo_root) + "/", "")
            resolved_root = repo_root.resolve()
            if resolved_root != repo_root:
                output = output.replace(str(resolved_root) + "/", "")
            output = output[-VALIDATE_OUTPUT_MAX_CHARS:]

            with control.LOCK:
                d = self._load()
                stale = d.generation != generation
                if not stale:
                    d.validated = ok
                    self._save(d)          # a verdict is not a change: generation stays
                view = self._view(d)
            if stale:
                # The draft this verdict is about no longer exists (edited or
                # cleared mid-run), so a failure here is not the caller's
                # failure to fix — it is stale information, not an error.
                result = {"valid": ok, "stale": True, "draft": view}
                if not ok:
                    result["output"] = output
                return result
            if not ok:
                raise DraftError("invalid", output or "make batch-validate failed")
            return {"valid": True, "stale": False, "draft": view}
        finally:
            _VALIDATE_SLOTS.release()
