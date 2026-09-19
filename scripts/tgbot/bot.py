#!/usr/bin/env python3
"""The bot loop. Thin: dispatch, per-chat state, and wiring — no logic.

    python3 scripts/tgbot/bot.py            # long-poll forever
    python3 scripts/tgbot/bot.py --once     # one getUpdates round, for testing
    python3 scripts/tgbot/bot.py --dry-run  # never invokes drain

Reads TG_BOT_TOKEN, TG_ALLOWED_USER_ID and TG_API_BASE from the root .env.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.config import env_get, env_set
from batchlib.local_tryon import is_local_provider, qwen_max_configured
from batchlib.manifest import (Manifest, ManifestError, load_manifest, load_state,
                               save_state, state_path_for)
from batchlib.pipelines import (PIPELINES, effective_stage_params,
                               optional_roles, required_roles)
from batchlib.vast_models import models_for_manifest
from batchlib.runner import (_local_tryon_stage, has_local_tryon,
                             preserved_local_tryon, stage_dest)
# Not `from batchlib_ext...` or `scripts/batchlib/...` — drain.py itself lives
# at scripts/drain.py, a plain top-level module, same as batch_run.py. scripts/
# is already on sys.path (the insert above), so this is the plan's own "import
# it, do not reimplement" for failed_job_ids rather than re-deriving "did this
# run fail" from state.json by hand a second time.
from drain import failed_job_ids, vast_download_gb
from batch_run import EXIT_NEEDS_POD
import batch_clean
# Absolute, NOT `from .tgclient import ...`. This file runs as
# `python3 scripts/tgbot/bot.py`, i.e. as __main__, where a relative import
# raises ImportError regardless of sys.path. The insert above puts scripts/ on
# the path, which is what makes the absolute form work from either entry point.
from tgbot.tgclient import Tg, TgError
from tgbot import tiktok
from tgbot.ingest import (Probe, describe, probe, quality_warning,
                         quality_warning_html,
                         to_png_if_heic)
from tgbot.job import (DEFAULT_PROVIDER, Job, _tryon_stage, _unique_ids, missing_slots,
                       render_manifest, run_id_for, slot_for, write_manifest)
from tgbot.preview import sheet, slot_preview
from tgbot.vast_panel import (build_view as vast_build_view, parse_enabled, spend_blockers,
                              static_blockers)
# `run as run_mod` alongside the from-imports, for exactly one caller:
# _busy_reason, which has to resolve drain_running through tgbot.run's OWN
# globals so it cannot disagree with the busy() that just returned True. See
# that function's docstring.
from tgbot import run as run_mod
from tgbot.run import (LEASE_PATH, _RUNNING, busy, drain_running,
                       estimate_minutes, final_files, lease_for,
                       phase_a_exit, phase_a_running,
                       progress_text, start_drain, start_phase_a,
                       stop_phase_a, summary_text)
from batchlib_ext.gpu_stock import stock_at, stock_at_cached, volume_datacenter
from batchlib_ext.runpod_account import account_balance
from batchlib_ext.vast_account import account_credit as vast_credit
from batchlib_ext.vast_quote import fetch_quote as vast_fetch_quote, last_quote as vast_last_quote
from batchlib_ext.handoff import handoff_path, mailbox_path, read_handoff
from batchlib_ext.lease import clear_lease, read_lease
from batchlib_ext.migrate_lease import read_migrate_lease
from batchlib_ext.provision_failure import (clear_provision_failure,
                                            provision_failure_path,
                                            read_provision_failure)

ROOT = Path(__file__).resolve().parents[2]

# `make batch-validate` and the Makefile it lives in are only ever at the real
# repo root, never wherever a test points ROOT (path-safety tests above
# reassign `bot.ROOT` to a tempdir). A separate, never-reassigned constant —
# same reasoning as tgbot/run.py's own ROOT for LEASE_PATH.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# How many try-on images /tryon will send before it stops and lists the runs
# instead. A batch can hold six, and six full-size images arriving unasked is
# the wall of clutter the panel exists to prevent.
TRYON_MAX_SENT = 4

# The one pipeline this bot assembles a job for. Plan 2A is the single-job
# slice — a recipe picker (batch/recipes/*.yaml) is explicitly 2B's job (see
# task-7-brief.md "Notes for Plan 2B"). tryon-motion-enhance is the pipeline
# whose required+optional materials are exactly the spec's "four labelled
# slots" (character, outfit, background, driver) — docs/superpowers/specs/
# 2026-08-30-telegram-batch-control-design.md section 1.
JOB_PIPELINE = "tryon-motion-enhance"

# The default above is only a fallback. `main()` replaces this with TG_PIPELINE
# from the root .env when it is set, and /pipeline overrides it per chat.
#
# Added 2026-08-31, on the user's first real run: they assembled a job, read
# the manifest, and wanted character-swap rather than motion — the two features
# this repo actually has. A pipeline nailed to one constant means the phone can
# reach every part of the flow except the choice of what the flow IS, which is
# the one thing they asked about first.
#
# This is the value a NEW job starts on. An existing draft keeps whatever
# /pipeline last set, because _save_draft persists job.pipeline with the slots
# (as of 2026-08-31 — before that a restart reverted the chat here, which is
# the reason this was made configurable rather than left a constant: falling
# back to a value the user set is predictable, falling back to a hard-coded
# one silently switches pipelines under them).
_DEFAULT_PIPELINE = JOB_PIPELINE

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

# The value a NEW job starts on — same role for provider as JOB_PIPELINE/
# _DEFAULT_PIPELINE above has for pipeline, including the TG_PROVIDER env
# override in main() and /provider overriding it per chat thereafter.
#
# Changed from "qwen" to "gemini" 2026-09-12 on the user's own request: the
# whole point of Phase A (drain.py, batchlib/runner.py's run_local_phase) is
# to run try-on on the VPS and catch errors before a pod is ever rented, and
# that only happens when a job's provider is gemini/qwen-max — "qwen" (the
# self-host default) always needs the pod, same as every pipeline's own
# motion/character-swap/enhance stage. Leaving new jobs on "qwen" made the
# whole feature opt-in per job, which is the same "everything works except
# the thing you actually wanted" gap _DEFAULT_PIPELINE was created to close.
#
# NOT job.py's DEFAULT_PROVIDER ("qwen"), which means something different and
# must never change: that constant is the value linux.py:5653 itself falls
# back to when a manifest carries no `provider:` line at all, so
# render_manifest() (job.py) and _fix_buttons/_provider_tag (below) compare
# against it specifically to decide whether "qwen" needs an explicit ☁️/🖥
# marker — not "whichever provider a brand-new job happens to start on".
JOB_PROVIDER = "gemini"
_DEFAULT_PROVIDER = JOB_PROVIDER

# Per-chat state — Plan 2A is one job at a time per chat.
#
# Was memory-only until 2026-08-31. The claim here used to be that "a bot
# restart loses an unsubmitted draft, never a running job", offered as an
# acceptable trade because drain_running() consults the on-disk lease. Three
# restarts in one session showed why it is not: the staged FILES survive on
# disk while their slot LABELS do not, so material the user had already
# answered questions about became unreachable with no message and no way back
# except sending it again. _save_draft/_load_draft now mirror this dict to
# batch/tg-<chat>.draft.json; the lease is still the durable record of a
# RUNNING job, and this is the durable record of an unsubmitted one.
_STATE: dict[int, Job] = {}
# A QUEUE of files parked while their slot is ambiguous (images — a video is
# never ambiguous, see job.slot_for), keyed by chat_id, same as _STATE.
#
# Corrected 2026-08-31 (docs/superpowers/plans/2026-08-31-telegram-bot-thin-
# path.md, commit c5c2a35): this was originally a single `Path`, "the one
# file awaiting an answer". Telegram delivers a multi-file send as
# consecutive updates inside one get_updates() batch, with no opportunity
# for the user to answer between them — so attaching character and outfit
# together, the natural way to do the Goal line's "send four files", made
# the second image silently overwrite the first, with no error and no
# recovery. Only the head of the queue is ever asked about; a valid answer
# pops it and asks about the new head, if any.
_PENDING: dict[int, list[tuple[Path, Probe]]] = {}

# The outcome of the last `make batch-validate` run for a chat's manifest,
# set only by `_maybe_show_manifest`. /confirm consults this cache rather
# than trusting that a downstream safety net (drain.py's own pre-provision
# validate) will catch a manifest the user already saw fail — that file is
# read-only to this bot and its behaviour is not this bot's to depend on.
_LAST_VALIDATE: dict[int, bool] = {}

# Chats that have already been told "you still have unanswered files" by
# /confirm. The second /confirm runs anyway, dropping them — the message says
# so. Cleared by anything that changes the job, so the warning is always about
# the files that are pending right now, not files answered since.
_CONFIRM_WARNED: set[int] = set()

# Where accepted uploads are copied before anything else touches them.
#
# Two problems, one move (findings C2 and I5, 2026-08-31). (1) With
# TELEGRAM_LOCAL=1 the Bot API server returns an absolute path on ITS
# filesystem, and that path embeds the bot token as a directory component:
# /var/lib/telegram-bot-api/<TOKEN>/documents/file_5.mp4. Left alone, that
# string becomes a manifest input line, is echoed back over Telegram by
# _maybe_show_manifest, and lands in the drain log — routing around the
# _scrub() that tgclient.py exists to provide. (2) The staged copy is also the
# only path downstream code ever sees, so the host/container namespace question
# stops mattering for everything after ingest. The names are the user's own
# ("blue-dress.jpg"), not Telegram's opaque "file_5.jpg", so a manifest read on
# a phone is readable.
STAGING_DIR_NAME = "tg-staging"

# tg-staging/ has no cleanup of any other kind (added 2026-09-04): /clear
# only runs when a user explicitly asks (_clear_job), and `make batch-clean`
# only ever touches out/runs/, never this directory (scripts/batch_clean.py).
# Left alone, every upload AND every TikTok download below sits forever on
# the VPS's fixed 40GB disk (scripts/vps/README.md "Box"). 7 days covers the
# normal "send material, confirm, run" session with margin.
STAGING_MAX_AGE_DAYS = 7

# Message kinds with no accept path at all: no width/height/bitrate ffprobe
# can read from a sticker or a voice note, so there is nothing to warn about,
# only refuse.
#
# `photo` and `video` used to be refused here too — see _RECOMPRESSION_COST
# for the 2026-08-31 measurement that justified it, and git history for the
# refusal text. Relaxed 2026-09-03 at the user's explicit request, after being
# shown that same measurement again: TikTok-sourced drivers are re-sent often
# enough that "send it again as a File" was the friction complained about, not
# a one-off mistake. `video` is still handled explicitly rather than falling
# through to the document branch, for the same reason it was ever added to
# this list: it is the iOS Photo/Video tab's default for a driver, and a kind
# neither branch recognises answers with silence, not a refusal.
NON_FILE_MEDIA = ("animation", "audio", "voice", "video_note", "sticker")

# What arriving as `photo` or `video` instead of a File actually costs, so the
# warning below argues from a number instead of from authority. Measured
# 2026-08-31 from the iPhone client, one file sent every available way.
#
# Images come out worse than video, and in three ways at once: Telegram caps
# the long edge at 2560 (the source was 2720, so even "high quality" shrinks
# it), converts PNG to JPEG — lossless to lossy, with chroma subsampling — and
# compresses ~50x. That is also why a recompressed PHOTO gets no OTHER
# warning: quality_warning()/quality_warning_html() only ever measure video
# bitrate (ingest.py's _per_megapixel), so this inline notice is the only
# signal a photo recompression ever produces — try-on consumes the character
# image directly, and this repo's background-chroma measurements assume the
# colour was not subsampled on the way in.
#
# Kinds with no row here get a claim with no number attached, deliberately:
# saying "measured" about something never measured is how the 20-30MB error in
# this spec happened.
_RECOMPRESSION_COST = {
    "photo": ("a 1536x2720 PNG came back a 1445x2560 JPEG with 98% of its "
              "bytes gone, and that was the 'high quality' option — the "
              "default cut it to 722x1280, 0.6% of the original"),
    "video": ("the 1088x1920 frame and all 444 frames survived, but the "
              "bitrate was halved (13,196 -> 6,603 kbps) and 49.8% of the "
              "bytes were gone"),
}

# Filenames are re-spelled into this alphabet before they are written or put
# into a manifest. job.py's render_manifest emits `      <slot>: <path>` as a
# PLAIN (unquoted) YAML scalar, so a space or a ": " anywhere in that line
# would produce a manifest that parses wrong or not at all — and job.py is
# protected by this branch's constraints, so quoting cannot be added there.
#
# What this actually guarantees is narrower than "the emitted line is always
# valid plain YAML" (finding E, 2026-08-31). The line job.py emits is the whole
# staged path — ROOT/batch/tg-staging/<chat_id>/<name> — and only the last
# component passes through here. The rest is the checkout directory, an
# unchecked assumption: clone this repo into "~/My Projects/motion clone" and
# every manifest this bot writes breaks, with nothing in this file to catch it.
# Sanitising only the filename is still the right split — ROOT is
# developer-chosen and inspected once, the filename is user-chosen, arbitrary
# and arrives at $0.99/hour — but the assumption is an assumption, not a proof.
#
# Restricting the alphabet also means a staged name can never contain a path
# separator or "..", the same property _safe_child() checks for.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# `đ` (U+0111) is a distinct letter, not `d` plus a combining mark, so NFD
# leaves it whole and the filter below would delete it. It is the only such
# case in Vietnamese: ă â ê ô ơ ư and every tone mark do decompose, so this
# one pair is the whole table rather than the start of one.
_TRANSLIT = {"đ": "d", "Đ": "D", "ð": "d", "Ð": "D"}


def _fold_diacritics(text: str) -> str:
    """Drop accents while keeping the letter under them.

    Without this, `_SAFE_NAME_RE` turned `áo dài.jpg` into `o_d_i.jpg`
    (measured 2026-08-31) — the accented letters are outside `[A-Za-z0-9._-]`,
    so they were deleted rather than folded. That matters beyond tidiness: the
    manifest is what the user reads on a phone before confirming a $0.99/hour
    render, and a file they cannot recognise is a file they cannot check is the
    right one. Staging exists partly to make those names readable, and this is
    what makes it true for the Vietnamese names this repo's material actually
    uses.

    NFD splits a base letter from its combining marks; dropping the marks keeps
    the letter. `unicodedata` is stdlib, so this adds no dependency.

    **NFD, never NFKD, and that is a security choice rather than a stylistic
    one.** NFKD also applies compatibility mappings, which turn fullwidth forms
    into their ASCII equivalents — measured 2026-08-31: `unicodedata.normalize
    ("NFKD", "／")` is `"/"` and `("NFKD", "．")` is `"."`. Folding with NFKD
    would therefore MANUFACTURE path separators and dots out of input that
    contained none, upstream of `_SAFE_NAME_RE` and of every reason
    `_safe_child()` gives for refusing them. Under NFD those characters are
    left alone and the filter replaces them with "_", which is the whole point.
    Do not "improve" this to NFKD for better folding.
    """
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    return "".join(ch for ch in unicodedata.normalize("NFD", text)
                   if not unicodedata.combining(ch))


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} bot: {msg}", flush=True)


def allowed(update: dict, allowed_user_id: int) -> bool:
    """Allowlist of exactly one user id, in exactly one chat: their own.

    Absence of a sender means refuse. channel_post and service updates carry no
    message.from, and defaulting them to allowed would let anyone who can post
    where this bot can see spend money.

    The chat check is the second half and is not redundant (added 2026-08-31).
    A private chat with a user has chat.id == that user's id; a group does not.
    If the owner adds this bot to a group, every message THEY send there passes
    the sender check, and the bot then replies into the group — with the
    manifest, the file paths and the finished video. Refusing anything but the
    one-to-one chat keeps the reply surface as narrow as the send surface.
    """
    sender, chat = _identify(update)
    return sender == allowed_user_id and chat == allowed_user_id


def _identify(update: dict) -> tuple[int | None, int | None]:
    """(sender id, chat id) from an ordinary message OR a button press.

    Two shapes, one check (added 2026-08-31 with the inline keyboards). A
    callback_query carries its sender at `callback_query.from` and its chat at
    `callback_query.message.chat` — NOT at `message.from`. The message-only
    reader this replaced returned (None, None) for every button press, so
    allowed() refused all of them, silently: buttons would render and do
    nothing at all when tapped.

    Both `None` when neither shape is present, which allowed() then refuses —
    the absence-means-refuse rule has to survive the second shape.
    """
    msg = update.get("message")
    if msg:
        return (msg.get("from") or {}).get("id"), (msg.get("chat") or {}).get("id")
    query = update.get("callback_query")
    if query:
        holder = query.get("message") or {}
        return ((query.get("from") or {}).get("id"),
                (holder.get("chat") or {}).get("id"))
    return None, None


def _safe_child(root: Path, name: str) -> Path | None:
    """Resolve `name` as a single path component directly under `root`, or None.

    The allowlist (`allowed()`) restricts WHO can message the bot, not WHAT
    the one allowed user types or pastes — a mistyped or copy-pasted path is
    enough to turn `/result`/`/tryon` into a probe for arbitrary files this
    process can see. `name` is meant to be a bare filename or a bare batch
    id, never a path, so an absolute argument, a literal ".." anywhere, or
    any path separator at all is refused outright — three independent
    reasons the same mistake would be caught. Then the joined path is
    resolved (symlinks and any remaining "." collapsed) and re-checked with
    `is_relative_to` against the resolved root, a second, independent check
    after the first.
    """
    name = name.strip()
    if not name or Path(name).is_absolute() or ".." in name or "/" in name or "\\" in name:
        return None
    root_resolved = root.resolve()
    candidate = (root_resolved / name).resolve()
    if not candidate.is_relative_to(root_resolved):
        return None
    return candidate


def _safe_name(name: str) -> str:
    """A user-supplied filename, re-spelled into `_SAFE_NAME_RE`'s alphabet.

    Stem and extension are re-spelled SEPARATELY (finding C, 2026-08-31).
    Doing the whole basename in one pass and then stripping "._-" off the ends
    destroyed the extension whenever the stem re-spelled to nothing:
    `_safe_name('写真.heic')` returned `'heic'`, a name with no suffix at all,
    so `to_png_if_heic` never fired and `probe` then rejected the file. This
    user's material comes from a Vietnamese-language workflow, so a non-Latin
    stem is the ordinary case; the extension is the part downstream code
    actually dispatches on, so it is the part that must survive.
    """
    base = _fold_diacritics(Path(name).name)
    stem = _SAFE_NAME_RE.sub("_", Path(base).stem).strip("._-")
    # lstrip(".") first so the separating dot is re-added below rather than
    # stripped away with the rest — ".heic" -> "heic" -> ".heic".
    suffix = _SAFE_NAME_RE.sub("_", Path(base).suffix.lstrip(".")).strip("._-")
    return f"{stem or 'file'}.{suffix}" if suffix else (stem or "file")


def _stage_file(chat_id: int, src: Path, file_name: str | None) -> Path:
    """Copy an accepted upload under `batch/tg-staging/<chat_id>/` and return that path.

    Copy, not reference: see STAGING_DIR_NAME for why the path Telegram hands
    back must never reach a manifest, a message or a log. The copy also means
    the Bot API server's own storage is only ever read — HEIC conversion and
    everything after it writes into the repo's own directory, which the bot
    owns and the container does not.
    """
    dest_dir = ROOT / "batch" / STAGING_DIR_NAME / str(chat_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_name(file_name or src.name)

    # HEIC/HEIF is converted the moment it lands, and ingest.to_png_if_heic
    # writes `path.with_suffix(".png")` with no collision check of its own
    # (ingest.py:145). So an incoming `photo.heic` claims TWO names, and both
    # have to be reserved here (finding A, 2026-08-31). Reserving them in one
    # place rather than giving to_png_if_heic its own counter is deliberate:
    # this function already owns the staging directory and the never-overwrite
    # rule, while ingest.py is written to know nothing about Telegram or
    # staging (its module docstring) and returns a name the caller predicts.
    # Two counters would be two owners of "which names are taken", which is
    # how the hole opened in the first place.
    #
    # The hole it closes: send photo.png, answer "character", then send
    # photo.heic. The .heic staged cleanly under its own name, converted, and
    # overwrote the BYTES of the already-assigned photo.png. Job.slots and the
    # manifest were unchanged and no message was sent, so the render used the
    # wrong image inside a paid job. Created by staging itself — before it,
    # conversion ran against Telegram's unique file_N.heic names.
    derived_suffix = ".png" if Path(stem).suffix.lower() in (".heic", ".heif") else None

    def taken(candidate: Path) -> bool:
        if candidate.exists():
            return True
        return derived_suffix is not None and candidate.with_suffix(derived_suffix).exists()

    dest = dest_dir / stem
    counter = 1
    while taken(dest):
        # Never overwrite: two files can share a name, and silently replacing
        # the first would lose a file the user believes they sent.
        dest = dest_dir / f"{Path(stem).stem}-{counter}{Path(stem).suffix}"
        counter += 1
    try:
        shutil.copyfile(src, dest)
    except OSError as exc:
        # Deliberately does NOT include `src` in the message: that string
        # contains the bot token (see STAGING_DIR_NAME), and this text goes
        # straight back over Telegram. `strerror` alone ("No such file or
        # directory") is the part that tells the operator what happened —
        # normally that the Bot API container's storage is not mounted at the
        # same path on the host (finding C2).
        raise RuntimeError(
            f"could not read the uploaded file for {stem}: {exc.strerror}. "
            f"On the VPS, check that telegram-bot-api.yml mounts "
            f"/var/lib/telegram-bot-api at the identical host path.") from exc
    return dest


def _prune_old_staged_files(now: float | None = None) -> list[Path]:
    """Delete files under batch/tg-staging/*/ older than STAGING_MAX_AGE_DAYS.

    Age-based and blind to which chat or job a file belongs to — the
    directory carries no other record of that once a job is cleared or
    confirmed (job.py's Job only tracks the CURRENT assembly). Returns what
    it removed, so the caller can log it.
    """
    now = time.time() if now is None else now
    cutoff = now - STAGING_MAX_AGE_DAYS * 86400
    staging_root = ROOT / "batch" / STAGING_DIR_NAME
    removed: list[Path] = []
    if not staging_root.is_dir():
        return removed
    for chat_dir in staging_root.iterdir():
        if not chat_dir.is_dir():
            continue
        for path in chat_dir.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
    return removed


# How often main()'s poll loop actually runs the sweep above. Once a day
# rather than every ~50s poll round (2026-09-04): the sweep stats every file
# under tg-staging/, and the whole point of piggybacking on the poll loop
# instead of a systemd timer (see _tick_staging_prune) is to cost nothing
# most of the time.
_STAGING_PRUNE_INTERVAL_SEC = 24 * 60 * 60
_LAST_STAGING_PRUNE = 0.0


def _tick_staging_prune() -> None:
    """Runs `_prune_old_staged_files` at most once per _STAGING_PRUNE_INTERVAL_SEC.

    Called from main()'s poll loop rather than a separate thread or a
    systemd timer: deploy-bot.sh only ever does `git reset --hard` + restart
    (scripts/vps/README.md), so anything needing its own VPS provisioning
    step is a second thing to remember to set up there and keep working
    after every deploy. The poll loop already runs forever, so this
    piggybacks on it instead.
    """
    global _LAST_STAGING_PRUNE
    now = time.time()
    if now - _LAST_STAGING_PRUNE < _STAGING_PRUNE_INTERVAL_SEC:
        return
    _LAST_STAGING_PRUNE = now
    removed = _prune_old_staged_files(now)
    if removed:
        log(f"pruned {len(removed)} staged file(s) older than "
            f"{STAGING_MAX_AGE_DAYS}d: {', '.join(p.name for p in removed)}")


# out/ is the one directory on the VPS that grows without bound (measured
# 2026-09-18: 457MB across 15 batches in ~6 days, ~75MB/day, on a 24GB disk
# with 17GB free). runs/ was 55% of it — 65-100MB of intermediates per batch —
# and only answers "what did try-on produce" for recent batches, so keep the
# newest OUT_RUNS_KEEP and drop the rest. _final/ is the user's finished work
# and is never deleted automatically; the low-disk warning below exists so a
# human decides about it instead.
OUT_RUNS_KEEP = 5
LOW_DISK_WARN_BYTES = 3 * 1024 ** 3
_LAST_OUT_PRUNE = 0.0


def _tick_out_prune(tg: Tg, chat_id: int) -> None:
    """Once a day: prune out/*/runs/ beyond the newest OUT_RUNS_KEEP batches,
    then warn the user if free disk is still under LOW_DISK_WARN_BYTES.

    Skipped entirely while a drain is running or a lease is on disk: RESUME
    re-attaches to an EXISTING out/<batch>/ that need not be among the newest,
    and deleting its runs/ mid-render would lose the stage outputs it resumes
    from. The next day's tick catches up.
    """
    global _LAST_OUT_PRUNE
    now = time.time()
    if now - _LAST_OUT_PRUNE < _STAGING_PRUNE_INTERVAL_SEC:
        return
    if _RUNNING or LEASE_PATH.exists():
        return
    _LAST_OUT_PRUNE = now
    removed = batch_clean.prune(ROOT / "out", OUT_RUNS_KEEP)
    if removed:
        log(f"pruned runs/ of {len(removed)} old batch(es): "
            f"{', '.join(p.parent.name for p in removed)}")
    free = shutil.disk_usage(ROOT).free
    if free < LOW_DISK_WARN_BYTES:
        tg.send_message(
            chat_id,
            f"VPS disk low: {free / 1024 ** 3:.1f} GB free. Intermediates are "
            f"already pruned; what is left is mostly out/*/_final/ (finished "
            f"videos), which the bot never deletes on its own.")


def _fidelity_line(path: Path) -> str:
    """Byte count and sha256 of an accepted file, for acceptance A6.

    Folded into the accepted-file reply rather than a separate /sha command
    (which Task 7 removed when it took over the document path): A6 compares
    what arrived against `out/<batch>/_final/<run>.mp4`, and having the
    arrival digest already in the transcript makes that a comparison instead
    of a second procedure someone has to remember to run.
    """
    digest = hashlib.sha256()
    # Streamed, not path.read_bytes(): a local Bot API server accepts up to
    # 2GB and the VPS this runs on is a 4GB Hetzner CX22 (scripts/vps/README.md
    # "Box"), so reading a whole video into memory to hash it is a real risk.
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    # 12 hex chars, not all 64 (shortened 2026-08-31). The full digest took
    # two lines on a phone and pushed the numbers that actually reveal
    # recompression — resolution, bitrate, size — off the top. 48 bits is far
    # more than enough to notice that what came back is not what went in, and
    # the full digest is recomputable from the staged file whenever A6 wants it.
    return f"{path.stat().st_size} bytes · sha256 {digest.hexdigest()[:12]}"


TAIL_CHARS = 1200


def _tail(path: Path, limit: int = TAIL_CHARS) -> str:
    """The end of a log file, small enough to inline in a message.

    Capped because a Telegram message is 4096 characters and a pod log is
    megabytes: sending the whole thing would make the send fail, which is how
    an error report becomes a second error. Reads only the tail rather than the
    whole file — these logs are large and the box is a 4GB CX22.
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            if size > limit * 4:
                handle.seek(-limit * 4, 2)
            raw = handle.read()
    except OSError as exc:
        return f"(could not read {path.name}: {exc.strerror})"
    text = raw.decode("utf-8", errors="replace").strip()
    return text[-limit:]


def _preserved_tryon(manifest_path: Path) -> tuple[int, int]:
    """(reusable, total) local try-ons for this manifest, or (0, 0) if unknown.

    Delegates to runner.preserved_local_tryon rather than counting here: the
    card and the runner must give the same answer, and the only way to
    guarantee that is one implementation. A manifest that will not load is
    (0, 0) — the card falls back to its generic "your batch is safe" line
    rather than claiming a number it could not check.

    The except below mirrors what load_manifest can actually raise: ManifestError
    for YAML and structural problems, OSError for an unreadable file, and
    UnicodeDecodeError from the read_text that sits inside its own try. It is
    that set and no wider — if load_manifest grows a new raise, this tuple is
    the thing to revisit, because an escape here reaches the poll loop after
    `offset` was already bumped and silently drops the rest of that batch of
    updates along with the card itself.
    """
    try:
        manifest = load_manifest(manifest_path)
    except (ManifestError, OSError, UnicodeDecodeError):
        return 0, 0
    state = load_state(state_path_for(manifest_path))
    if not state.get("batch"):
        return 0, 0
    return preserved_local_tryon(manifest, state, ROOT / "out")


def _deliver_provision_failure(tg: Tg, chat_id: int, manifest_path: Path,
                               failure: "ProvisionFailure") -> None:
    """Report why pod-provision.sh itself never got a pod, in place of the
    generic "nothing to send, check the log" message deliver_result used to
    leave behind for this exact case (a batch whose local phase finished but
    whose GPU rental never started).

    Only a stock-out (pod-provision.sh's own "no instances available"
    classification, drain.py's _STOCK_OUT_MARKER) gets recovery buttons: any
    other reason (bad config, a RunPod API error, ...) is not decidable from
    a fixed menu, so it gets the plain reason plus today's "check the log"
    pointer instead.
    """
    stem = manifest_path.stem
    if not failure.stock_out:
        tg.send_message(
            chat_id,
            f"{ICON_ERROR_CE} <b>Could not rent a pod</b> for {_esc(stem)}.\n"
            f"<blockquote expandable>{_esc(failure.detail[-500:])}</blockquote>\n"
            "Check the drain log on the box for the full error.",
            parse_mode=PARSE_HTML)
        return

    dc = failure.datacenter or "?"
    reusable, total = _preserved_tryon(manifest_path)
    lines = [f"{ICON_ERROR_CE} <b>Could not rent a pod</b> for {_esc(stem)}", "",
             f"No stock for <b>{_esc(failure.gpu)}</b> at {_esc(dc)} — the only "
             "datacenter your Network Volume can mount in.", ""]
    if total:
        lines.append(f"{ICON_OK_CE} <b>Try-on {reusable}/{total} finished and is "
                     "preserved.</b> Retrying will not call Gemini again for those.")
    else:
        lines.append("Your batch is safe — nothing already finished was lost, it's "
                     "just stuck waiting for a pod.")
    lines.append("")

    volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
    home_dc = volume_datacenter(volume_id)
    wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
    try:
        stock = stock_at_cached(wanted) if home_dc else {}
    except RuntimeError:
        stock = {}

    buttons = [[(f"Thử lại — giữ try-on đã chạy", f"{_CB_RECOVER_RETRY}{stem}",
                 _ce_id(ICON_ROCKET_CE))],
               [("Đợi", _CB_RECOVER_WAIT)]]

    # Same-datacenter alternatives — tapping one resumes immediately, unlike
    # _CB_RUN_SWITCH's pre-spend picker, because this batch was already
    # confirmed once; switching GPU here is not a new spend decision.
    for gpu_id in wanted:
        if gpu_id == failure.gpu:
            continue
        short = _GPU_SHORT.get(gpu_id)
        if short is None:
            continue
        entry = next((e for e in (stock.get(gpu_id) or [])
                     if e.datacenter_id == home_dc), None)
        if entry is None or entry.stock_status.lower() == "none":
            continue
        # ICON_ROCKET_CE, not plain — unlike _switch_type_options' own
        # switch buttons (which only update the picker before a later,
        # separate spend confirmation), this one resumes the rental the
        # instant it's tapped, the same action "Yes, spend $/h" performs.
        buttons.append([(f"🖥 Đổi sang {entry.display_name} — {entry.stock_status} · "
                        f"${entry.price_per_hr:.2f}/h",
                        f"{_CB_RECOVER_SWITCH}{short}:{stem}",
                        _ce_id(ICON_ROCKET_CE))])

    # Other datacenters the SAME failed GPU is stocked at — a migration,
    # never a same-datacenter switch (see _migrate_options for why the two
    # are always offered separately).
    for e in stock.get(failure.gpu) or []:
        if e.datacenter_id == home_dc or e.stock_status.lower() == "none":
            continue
        buttons.append([(f"🛫 Migrate → {e.datacenter_id} — {e.stock_status} · "
                        f"${e.price_per_hr:.2f}/h",
                        f"{_CB_RECOVER_MIGRATE}{e.datacenter_id}:{stem}")])

    buttons.append([("☁ Rent on Vast instead", f"{_CB_RECOVER_VAST}{stem}")])

    short = _GPU_SHORT.get(failure.gpu)
    if short is not None:
        # ICON_CRITICAL_CE, same as _offer_gpu_sub_datacenters' own "none"
        # branch — this datacenter is always "none" for the failed GPU here,
        # by construction (that IS the failure being reported).
        buttons.append([(f"🔔 Subscribe {_GPU_DISPLAY_SHORT.get(failure.gpu, failure.gpu)} "
                        f"@ {dc}", f"{_CB_GPUSUB_DC}{short}:{dc}",
                        _ce_id(ICON_CRITICAL_CE))])

    tg.send_message(chat_id, "\n".join(lines), parse_mode=PARSE_HTML, buttons=buttons)


def deliver_result(tg: Tg, chat_id: int, manifest_path: Path) -> None:
    """Send the finished video(s) back, or the failure diagnostics already on disk.

    Reached on request via the `/result` command in `handle()`, not by an
    automatic drain-completion callback: nothing in this bot polls a drain to
    completion and fires a callback when it finishes — `/confirm` (Task 7)
    starts one and returns immediately, same as `make drain` does from a
    terminal. This is the reachable "close the loop" hook until a completion
    poll exists.

    `failed_job_ids` (imported from scripts/drain.py, not reimplemented) is
    the same function `drain.py`'s own `teardown()` uses to decide what to
    fetch, so "which run failed" is answered identically here and there.
    `state.json` lives beside the MANIFEST (batchlib.manifest.state_path_for),
    never under `out/<batch>/`, which is why this takes a manifest path
    rather than a batch id the way `final_files`/`summary_text` do.
    """
    state = load_state(state_path_for(manifest_path))
    batch_id = state.get("batch") or ""
    if not batch_id:
        tg.send_message(chat_id, f"{ICON_ERROR_CE} <b>No batch was ever recorded</b> for "
                                 f"<code>{_esc(manifest_path.name)}</code> — the "
                                 "drain failed before it started. The log is "
                                 f"<code>{_esc(manifest_path.stem)}.drain.log</code> "
                                 "on the box.", parse_mode=PARSE_HTML)
        return
    batch_dir = ROOT / "out" / batch_id
    failures = failed_job_ids(state)
    outputs = final_files(batch_dir)
    # Only this manifest's runs, for the reason _journal_is_resumable gives: a
    # batch that inherited an older job's journal also shares its out/ dir,
    # and would otherwise resend that job's videos as if they were new.
    try:
        current = {run.id for run in load_manifest(manifest_path).runs}
    except (ManifestError, OSError, UnicodeDecodeError):
        current = None
    if current:
        outputs = [p for p in outputs if p.stem in current]
        failures = [(run_id, job_id) for run_id, job_id in failures
                    if run_id in current]

    if outputs:
        tg.send_message(chat_id, f"{ICON_ANNOUNCE_CE} <b>Done</b> · {_esc(batch_id)} · "
                                 f"{len(outputs)} file(s)", parse_mode=PARSE_HTML)
        for path in outputs:
            tg.send_chat_action(chat_id, "upload_document")
            tg.send_document(chat_id, path, caption=path.name)

    for run_id, _job_id in failures:
        # The failing stage, named. "the run failed" sends the user to read a
        # log to learn something the journal already knows.
        stages = ((state.get("runs") or {}).get(run_id) or {}).get("stages") or {}
        broke = [n for n, st in stages.items() if st.get("status") == "error"]
        where = f" at <b>{_esc(broke[0])}</b>" if broke else ""
        tg.send_message(chat_id, f"{ICON_ERROR_CE} <b>{_esc(run_id)} failed</b>{where}",
                        parse_mode=PARSE_HTML)
        # On failure, attach exactly what scripts/drain.py's teardown() already
        # pulled onto local disk BEFORE destroying the pod. Never reach for the
        # pod here — by the time this runs it is normally already gone.
        run_dir = batch_dir / "runs" / run_id
        for name in ("pod-job.log", "run.log"):
            log_path = run_dir / name
            if not log_path.exists():
                continue
            tail = _tail(log_path)
            if tail:
                # Inline as well as attached: opening a .log document on a
                # phone is several taps and an app switch, and the last few
                # lines are almost always the whole answer.
                tg.send_message(chat_id,
                                f"<b>{_esc(name)}</b>, last lines:\n"
                                f"<blockquote expandable>{_esc(tail)}</blockquote>",
                                parse_mode=PARSE_HTML)
            tg.send_document(chat_id, log_path, caption=f"{run_id}/{name}")

    if not outputs and not failures:
        failure = read_provision_failure(provision_failure_path(manifest_path))
        if failure is not None:
            # This IS the whole report for this case — no trailing
            # summary_text blockquote below, which would only say
            # "no _index.tsv yet" (Phase A ran, the pod stage never got to)
            # and bury the actual reason under noise.
            _deliver_provision_failure(tg, chat_id, manifest_path, failure)
            return
        tg.send_message(chat_id, f"{ICON_WARN} <b>Nothing to send</b> for "
                                 f"{_esc(batch_id)} — no output files and no "
                                 "run marked failed. Check the drain log on "
                                 "the box.", parse_mode=PARSE_HTML)

    tg.send_message(chat_id,
                    f"📋 <b>{_esc(batch_id)}</b>\n"
                    f"<blockquote expandable>{_esc(summary_text(batch_dir))}"
                    "</blockquote>", parse_mode=PARSE_HTML)


def _job_for(chat_id: int) -> Job:
    """The chat's draft job, created on first use at the current default.

    One accessor rather than two `_STATE.setdefault(...)` calls: the default
    pipeline is now a variable, and two call sites reading it independently is
    how they drift apart.
    """
    return _STATE.setdefault(
        chat_id, Job(slots={}, probes={}, pipeline=_DEFAULT_PIPELINE,
                    provider=_DEFAULT_PROVIDER))


def _switch_pipeline(chat_id: int, name: str) -> tuple[Job, list[str]]:
    """Point the chat's job at `name`, keeping slots the new pipeline can use.

    Returns the job and the roles that had to be dropped. Both pipelines the
    user moves between here need character/driver/outfit, so in practice
    nothing is dropped — but character-swap-enhance does not take an outfit,
    and silently carrying one into a manifest that has no stage to consume it
    would produce a run that ignores a file the user deliberately labelled.
    """
    job = _job_for(chat_id)
    usable = required_roles(name) | optional_roles(name)
    dropped = sorted(set(job.slots) - usable)
    for role in dropped:
        job.slots.pop(role, None)
        job.probes.pop(role, None)
    job.pipeline = name
    # The cached verdict belongs to the manifest of the OLD pipeline. Leaving
    # it set would let a later /confirm act on a validation that never ran
    # against what is about to be submitted.
    _LAST_VALIDATE.pop(chat_id, None)
    _CONFIRM_WARNED.discard(chat_id)
    return job, dropped


def _switch_provider(chat_id: int, provider: str) -> Job:
    """Point the chat's job at `provider` for whichever stage runs try-on.

    No slots to drop here — provider is a param on an existing stage, not a
    change to which materials the pipeline consumes. Same cache-invalidation
    as _switch_pipeline, for the same reason: the cached verdict is about the
    manifest as it stood before this call.
    """
    job = _job_for(chat_id)
    job.provider = provider
    _LAST_VALIDATE.pop(chat_id, None)
    _CONFIRM_WARNED.discard(chat_id)
    return job


def _job_manifest_path(chat_id: int) -> Path:
    """One deterministic manifest per chat — Plan 2A's "one job at a time".

    Computed from chat_id alone, not a timestamp: the same path is written
    when the job completes and read back by /confirm, so a filename that
    changed between those two moments would make /confirm act on a manifest
    the user never saw. Under `ROOT/"batch"` — the same directory /result
    already resolves bare filenames against — so a finished job stays
    reachable by `/result tg-<chat_id>.yaml` if the in-memory state is lost.
    """
    return ROOT / "batch" / f"tg-{chat_id}.yaml"


def _active_manifest_path(chat_id: int) -> Path:
    """Where the job being assembled right now actually belongs.

    The live per-chat path while nothing is draining; the mailbox
    (batchlib_ext.handoff.mailbox_path) while one is — so the panel, the
    validate call, and the eventual /confirm never disagree about which file
    this job is going into (2026-09-02, the queue-while-draining feature).
    """
    live_path = _job_manifest_path(chat_id)
    return mailbox_path(live_path) if drain_running(live_path) else live_path


_SPIN_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPIN_INTERVAL_SEC = 0.5


@contextlib.contextmanager
def _spinner(tg: Tg, chat_id: int, label: str):
    """A real animated "working" message for the duration of the `with` block,
    deleted the instant it ends.

    The bot is otherwise single-threaded and fully sequential (main()'s poll
    loop only calls `handle()` and `tick_progress` back to back) — nothing
    can redraw a message while the getFile/probe/ffmpeg chain below runs on
    the main thread, so an animation needs a thread of its own. This is the
    only one in the file. It never touches per-chat state (`_STATE`,
    `_PENDING`, ...) — only this one Telegram message — so it cannot race
    the main thread's own bookkeeping; the two are joined again (`thread.
    join()`) before the caller does anything else with `tg`.

    A throttled edit just skips a frame (2026-09-02): the spin is cosmetic,
    and this thread has no way to report a raised exception to anyone.
    """
    message_id = tg.send_message(chat_id, f"{_SPIN_FRAMES[0]} {label}")
    stop = threading.Event()

    def spin() -> None:
        i = 0
        while not stop.wait(_SPIN_INTERVAL_SEC):
            i += 1
            frame = _SPIN_FRAMES[i % len(_SPIN_FRAMES)]
            try:
                tg.edit_message(chat_id, message_id, f"{frame} {label}")
            except TgError as exc:
                log(f"spinner edit throttled, skipping a frame: {exc}")

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=_SPIN_INTERVAL_SEC + 1)
        tg.delete_message(chat_id, message_id)


def _askable_roles(pipeline: str) -> list[str]:
    """Slot names a user can be asked to name — every material role except
    `driver`, which is structural (job.slot_for never asks about a video)."""
    return sorted((required_roles(pipeline) | optional_roles(pipeline)) - {"driver"})


def _ask_about(tg: Tg, chat_id: int, p: Probe, pipeline: str,
               path: Path | None = None) -> None:
    """Ask which slot a parked, ambiguous file belongs to.

    One button per askable role (2026-08-31). Typing "character" on a phone is
    the friction the buttons remove; the typed reply still works, and
    _answer_slot is the single body both paths run.
    """
    roles = _askable_roles(pipeline)
    job = _STATE.get(chat_id)
    filled = set(job.slots) if job else set()
    # Two per row and marked, for the same reason _fix_buttons is (2026-09-01):
    # three or four buttons on one row have their labels truncated on a phone,
    # and an unmarked role gives no warning that tapping it overwrites a file
    # already placed — _fill_slot names the replacement, but only afterwards.
    labels = [(f"{ROLE_ICON.get(r, '')} {r}", _CB_SLOT + r, _ce_id(ICON_OK_CE))
              if r in filled else (f"{ROLE_ICON.get(r, '')} {r}", _CB_SLOT + r)
              for r in roles]
    rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    question = f"{describe(p)}\nWhich slot is this?"
    if filled & set(roles):
        # The animated checkmark on a role's button says it's filled, but not
        # what tapping it does — a user wanting to swap that file out has no
        # other button to reach for, so the answer has to live right next to
        # the question (2026-09-02, reported as "didn't tell me how to change
        # outfit"). ICON_OK_CE here, not a plain "✅", so it reads as the same
        # checkmark the button itself carries via icon_custom_emoji_id
        # (2026-09-12, reported live: the two didn't match).
        question += f"\n({ICON_OK_CE} = already set — tap it again to replace)"

    # With the picture, when there is one (2026-09-01). This question used to
    # be asked about an image the user could not see — the File rule that keeps
    # the bytes intact also means nothing is ever shown back, so the whole
    # prompt was "image 1536x2720, 4.9 MB / Which slot is this?" and the only
    # way to answer was to remember the upload order.
    shot = (slot_preview(path, is_video=False, into=_preview_dir(chat_id))
            if path else None)
    if shot is not None:
        try:
            tg.send_photo(chat_id, shot, caption=question, buttons=rows,
                          parse_mode=PARSE_HTML)
            return
        except TgError as exc:
            # Fall through to text. A preview is a courtesy and must never be
            # able to swallow the question itself.
            log(f"preview upload failed, asking without it: {exc}")
    tg.send_message(chat_id, question, buttons=rows, parse_mode=PARSE_HTML)


def _fill_slot(tg: Tg, chat_id: int, job: Job, role: str, path: Path,
               p: Probe) -> None:
    """Put a file in a slot and say so — including when it displaces one.

    The one acknowledgement path for every fill (findings I6/I7,
    2026-08-31). Both ways into a slot could previously overwrite a file the
    user had already placed and reply as if nothing had been lost: resending a
    video silently replaced `driver`, and answering a role that was already
    filled both popped the queue head and overwrote the slot, so the original
    was unrecoverable in the same breath. Overwriting is still allowed — it is
    how you correct a mistake — but it is now named.

    Since 2026-09-01 this writes the acknowledgement into `_PANEL_NOTE` instead
    of sending it, and renders nothing itself — every caller reaches
    `_maybe_show_manifest`, which is now the single place the panel is drawn.
    The naming survived the move, in the past tense the new position calls for
    ("replaced the previous outfit" rather than "replacing"): a note sits under
    a panel that already shows the result, so the present tense would be
    describing something that has finished happening.
    """
    replacing = role in job.slots
    job.slots[role] = path
    job.probes[role] = p
    verb = f"replaced the previous {role}" if replacing else f"added {role}"
    _PANEL_NOTE[chat_id] = f"{verb} — {path.name}"


def _render_and_validate(tg: Tg, chat_id: int) -> bool:
    """Write this chat's manifest and run the free `make batch-validate` on it.

    Returns whether the manifest is safe to run, and records that in
    `_LAST_VALIDATE` — `/confirm` consults the cache rather than re-deriving
    "was the last render valid" from scratch, since a user who ignores the
    failure message and types /confirm anyway must still be refused (Task 7
    fix round 1, Finding 2).

    Split out of `_maybe_show_manifest` for finding B (2026-08-31): /confirm
    needs the render-and-validate half without the "reply /confirm to spend
    money" prompt that follows it. Every path that returns False has already
    sent its own specific message — callers must not add a second, vaguer one
    on top, because burying the real reason is the bug finding B is about.

    One asymmetry is load-bearing: the two write-guard branches below (a live
    Phase A, or a mailbox already occupied) leave `_LAST_VALIDATE` UNSET rather
    than setting it False. Unset means "never attempted", which is what
    /confirm keys off to retry later; False means "attempted and the manifest
    is bad", which /confirm must not retry.
    """
    live_path = _job_manifest_path(chat_id)
    # Two guards, and they are not the same shape. This one is unconditional:
    # --phase-a-only is drain.py too, so a live Phase A is a child re-reading
    # the very file below is about to rewrite, and there is nowhere to put the
    # write instead — a mailbox belongs to a running drain's chain_or_teardown,
    # which a Phase A does not have. Refuse outright, never queue.
    #
    # phase_a_running, not busy(): busy() also catches a drain, and a drain is
    # the OTHER guard's case — it redirects into the mailbox rather than
    # blocking. Added 2026-09-17: until then neither guard fired for the common
    # case (Phase A live, no drain, empty mailbox) and the rewrite went through.
    if phase_a_running(live_path):
        tg.send_message(chat_id,
                        "the try-on phase is running for this job — wait for "
                        "it to finish, or /kill to stop it, then try again")
        return False
    # The drain-specific guard. _manifest_write_ok is only half of it: a live
    # drain alone is fine (the write is redirected below), what is refused is a
    # mailbox ALREADY holding a job.
    if not _manifest_write_ok(chat_id) and mailbox_path(live_path).exists():
        # Queue depth is "current plus at most one next" (2026-09-02) — a
        # second job can be assembled and validated live while the first
        # drains, but a third has nowhere safe to land until drain.py's own
        # chain_or_teardown claims the one already queued, which frees this
        # same mailbox file again.
        tg.send_message(chat_id,
                        "a job is already queued next for this chat — wait for "
                        "it to start before queuing another. Your files are "
                        "kept; send /status for progress.")
        return False
    # While a drain is running, write into its mailbox instead of the live
    # manifest (2026-09-02) — scripts/drain.py:220-248 runs batch_run.py as a
    # separate process TWICE, each time re-reading the live manifest from
    # disk, so overwriting THAT file mid-drain would corrupt input the runner
    # is actively reading. The mailbox is a different file: safe to write,
    # validate, and show a live panel for, exactly like an ordinary job.
    manifest_path = _active_manifest_path(chat_id)
    if manifest_path != live_path:
        # Writing the mailbox IS queueing: drain.py claims whatever sits there when the current
        # job ends, with no further tap. So a job bound for a Vast pod is checked BEFORE the write
        # (leaving _LAST_VALIDATE unset, like the two guards above: never attempted).
        refusal = _vast_queue_refusal(chat_id, live_path)
        if refusal is not None:
            tg.send_message(chat_id, refusal, parse_mode=PARSE_HTML)
            return False
    write_manifest(_jobs_for(chat_id), manifest_path,
                   now=time.strftime("%Y-%m-%d %H:%M:%S"))

    tg.send_chat_action(chat_id)
    try:
        result = subprocess.run(
            ["make", "batch-validate", f"FILE={manifest_path}"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        # This runs inside the single synchronous poll loop: without a timeout
        # a hung validate blocks every further update, including /status, for
        # as long as it hangs. 120s is ~100x the observed runtime (validate
        # loads YAML and stats files; it never touches a pod).
        _LAST_VALIDATE[chat_id] = False
        tg.send_message(chat_id, "make batch-validate did not finish within 120s "
                                 "— nothing will run. Check the VPS.")
        return False
    if result.returncode != 0:
        _LAST_VALIDATE[chat_id] = False
        tg.send_message(chat_id,
                        "manifest failed validation — nothing will run:\n"
                        f"{(result.stdout + result.stderr).strip()}")
        return False
    _LAST_VALIDATE[chat_id] = True
    return True


def _maybe_show_manifest(tg: Tg, chat_id: int, job: Job, *,
                         bump: bool = False, note: str = "") -> None:
    """Redraw the panel, validating for free first if the job is complete.

    This is the plan's "[Run]" step (docs/superpowers/plans/2026-08-31-…
    Task 5: "renders the manifest, runs make batch-validate (free), and shows
    the result with an estimate") — triggered automatically by the last slot
    fill rather than a separate command, since there is no button to tap for
    it. It rents nothing: `make batch-validate` never touches the pod.

    It now draws on EVERY call, not only on a complete job (2026-09-01). The
    panel is the one thing the user reads, so a fill that leaves the job
    incomplete has to move it too — otherwise two of the three slot fills in an
    ordinary job would change nothing on screen.

    The validate verdict is not consulted here: `_render_and_validate` records
    it in `_LAST_VALIDATE` and sends its own specific message on failure, and
    `_panel_buttons` reads it to decide whether Run may be offered at all. One
    reader, one writer.
    """
    if _jobs_for(chat_id):
        _render_and_validate(tg, chat_id)
    _show_panel(tg, chat_id, bump=bump, note=note)


_DRAFT_SUFFIX = ".draft.json"

# Chats whose draft has already been read off disk this process. Without it,
# every update would re-read and clobber the live state with the saved copy.
_LOADED: set[int] = set()


def _draft_path(chat_id: int) -> Path:
    """Beside the chat's manifest, and gitignored by the same `batch/` rules."""
    return ROOT / "batch" / f"tg-{chat_id}{_DRAFT_SUFFIX}"


def _save_draft(chat_id: int) -> None:
    """Write the chat's unsubmitted draft — slots, probes, pipeline, queue.

    Added 2026-08-31, after this bit three times in one session. `_STATE` and
    `_PENDING` were memory-only, so an ordinary restart discarded every slot
    LABEL while leaving the staged FILES on disk: material the user had
    already answered questions about became unreachable, with no message and
    no way to re-attach it except sending it again. motion-bot.service is
    `Restart=always`, so on the VPS nobody has to restart it by hand for this
    to happen.

    Not persisted, on purpose: `_LAST_VALIDATE`. /confirm already treats a
    missing verdict as "never attempted" and re-runs the free validate,
    re-sending the manifest before anything spends — which is the behaviour
    you want after a restart anyway. A cached pass carried across a restart
    would be a verdict about a process that no longer exists.

    Atomic via tmp+replace, same as batchlib_ext/lease.py: a draft truncated
    by a kill mid-write is the exact failure this function exists to prevent.
    """
    path = _draft_path(chat_id)
    job = _STATE.get(chat_id)
    pending = _PENDING.get(chat_id) or []
    if job is None and not pending and not _BASKET.get(chat_id):
        # /confirm clears the state after submitting; the draft must go with
        # it, or the next restart would resurrect a job already running.
        path.unlink(missing_ok=True)
        return
    payload = {
        "pipeline": job.pipeline if job else _DEFAULT_PIPELINE,
        # _DEFAULT_PROVIDER (bot.py's "what a new job starts on"), not job.py's
        # DEFAULT_PROVIDER — same "pipeline" if job else _DEFAULT_PIPELINE
        # reasoning two lines up: there is no job yet, so this placeholder
        # should read as what /provider would show for one, not "qwen" (which
        # would just be job.py's unrelated worker-fallback fact leaking in).
        "provider": job.provider if job else _DEFAULT_PROVIDER,
        "slots": {r: str(p) for r, p in (job.slots if job else {}).items()},
        "probes": {r: asdict(pr) for r, pr in (job.probes if job else {}).items()},
        "pending": [[str(p), asdict(pr)] for p, pr in pending],
        # The panel has to survive a restart for the same reason the slots do:
        # motion-bot.service is Restart=always, and a bot that came back
        # without the message id would send a SECOND panel while the first sat
        # above it with live buttons — two keyboards for one job, which is how
        # a tap lands on a job that no longer exists.
        "panel": _PANEL.get(chat_id),
        "panel_is_photo": chat_id in _PANEL_IS_PHOTO,
        "note": _PANEL_NOTE.get(chat_id),
        "last_seen": _LAST_SEEN.get(chat_id),
        # Acceptance A6 evidence. Recomputable from the staged file, but only
        # while it is still there — and the digest is what proves the file on
        # disk IS the one that arrived, which a later re-hash cannot.
        "fidelity": _FIDELITY.get(chat_id),
        "basket": _dump_jobs(_BASKET.get(chat_id) or []),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_draft(chat_id: int) -> str | None:
    """Rehydrate one chat's draft. Returns the name of a file it had to set aside.

    A corrupt draft is MOVED, never left in place (fixed 2026-08-31). It used
    to be logged and skipped, which looked harmless and was not: skipping
    leaves `_STATE` empty, and `handle`'s `finally: _save_draft` then sees no
    state and unlinks the file — so the one record of the job was destroyed by
    the line after the one that failed to read it, with nothing said to the
    user. Reconstructing a draft by hand from the staged files is possible (it
    was done once, earlier the same day) but only while the file still exists.
    """
    path = _draft_path(chat_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pipeline = payload["pipeline"]
        if pipeline not in PIPELINES:
            raise ValueError(f"unknown pipeline {pipeline!r}")
        slots = {r: Path(v) for r, v in payload["slots"].items()}
        probes = {r: Probe(**d) for r, d in payload["probes"].items()}
        pending = [(Path(v), Probe(**d)) for v, d in payload["pending"]]
    except (ValueError, KeyError, TypeError) as exc:
        # Moved aside, not left to be unlinked by the save that follows. An
        # existing .bad from a previous failure is overwritten: the most recent
        # one matches the state on disk, and this must not grow without bound.
        bad = path.with_suffix(path.suffix + ".bad")
        path.replace(bad)
        log(f"draft for chat {chat_id} is unreadable, moved to {bad.name}: {exc!r}")
        return bad.name
    # .get, not payload["provider"]: same "missing cosmetic field must not
    # condemn an otherwise good draft" reasoning as "panel" below — a draft
    # written before /provider existed simply has none. job.py's
    # DEFAULT_PROVIDER ("qwen"), not bot.py's _DEFAULT_PROVIDER: a draft this
    # old was assembled back when self-host was the only behaviour that
    # existed, and this fallback should say what it actually was, not
    # retroactively become whatever a brand-new job defaults to today.
    provider = payload.get("provider", DEFAULT_PROVIDER)
    _STATE[chat_id] = Job(slots=slots, probes=probes, pipeline=pipeline,
                          provider=provider)
    if pending:
        _PENDING[chat_id] = pending
    # Read with .get, unlike the fields above: a draft written by the previous
    # version of this bot has no "panel" key, and refusing to load an otherwise
    # perfectly good job over a missing cosmetic field would set it aside as
    # corrupt — the one outcome _load_draft exists to avoid.
    panel = payload.get("panel")
    if panel is not None:
        _PANEL[chat_id] = int(panel)
    if payload.get("panel_is_photo"):
        # Which edit call to use on it. A restart that forgot this would send
        # editMessageText at a photo, get "message can't be edited", treat the
        # panel as deleted and post a second one below the first.
        _PANEL_IS_PHOTO.add(chat_id)
    note = payload.get("note")
    if note:
        _PANEL_NOTE[chat_id] = str(note)
    last_seen = payload.get("last_seen")
    if last_seen is not None:
        _LAST_SEEN[chat_id] = int(last_seen)
    fidelity = payload.get("fidelity")
    if fidelity:
        _FIDELITY[chat_id] = {str(k): str(v) for k, v in fidelity.items()}
    basket = payload.get("basket")
    if basket:
        _BASKET[chat_id] = _load_jobs(basket)
    return None


_LEDGER: dict[int, list[int]] = {}
_LEDGER_LOADED: set[int] = set()


def _ledger_path(chat_id: int) -> Path:
    """Its own file, not folded into the draft (2026-09-02): _save_draft
    deletes the draft the moment a job finishes or is cleared, which is
    exactly when a user is likely to want /wipe — the record of what to
    delete must outlive the job it has nothing to do with.
    """
    return ROOT / "batch" / f"tg-{chat_id}.ledger.json"


def _ledger_for(chat_id: int) -> list[int]:
    """Every message_id /wipe is allowed to ask Telegram to delete.

    Telegram exposes no "list this chat's history" call, so this is built up
    one send/receive at a time as messages happen — anything from before this
    existed, or from a message this bot never saw pass through `handle` or
    `_track_sends`, is simply unreachable. Loaded once per process per chat,
    same as `_LOADED` for drafts.
    """
    if chat_id not in _LEDGER_LOADED:
        _LEDGER_LOADED.add(chat_id)
        path = _ledger_path(chat_id)
        try:
            _LEDGER[chat_id] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as exc:
            log(f"ledger for chat {chat_id} is unreadable, starting over: {exc!r}")
    return _LEDGER.setdefault(chat_id, [])


def _record_message(chat_id: int | None, message_id: int | None) -> None:
    """Note one message_id as belonging to this chat, for /wipe.

    Idempotent by construction (`in` before `append`) rather than because
    duplicates would be harmful — deleteMessage on an already-deleted id just
    fails quietly — but a ledger that only grows is easier to reason about
    than one that might not.
    """
    if chat_id is None or message_id is None:
        return
    ids = _ledger_for(chat_id)
    if message_id in ids:
        return
    ids.append(message_id)
    path = _ledger_path(chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(ids), encoding="utf-8")
    tmp.replace(path)


def _track_sends(tg: Tg) -> Tg:
    """Patch every id-producing send method on THIS instance so /wipe's
    ledger sees each one before the caller does.

    The alternative was recording at each of the dozens of call sites across
    this file that send something — one more added later without the same
    care would be a message /wipe quietly cannot reach. Patched once, on the
    single Tg main() constructs (2026-09-02) — not per `handle()` call, or
    each update would wrap the previous wrapper again and record every id
    once per layer.

    send_document and send_media_group are here too, covering a delivered
    result and a preview album — the two message shapes that are not text
    or a single photo, so a wipe used to leave exactly those behind.
    """
    real_send_message = tg.send_message
    real_send_photo = tg.send_photo
    real_send_document = tg.send_document
    real_send_media_group = tg.send_media_group

    def send_message(chat_id, *a, **kw):
        message_id = real_send_message(chat_id, *a, **kw)
        _record_message(chat_id, message_id)
        return message_id

    def send_photo(chat_id, *a, **kw):
        message_id = real_send_photo(chat_id, *a, **kw)
        _record_message(chat_id, message_id)
        return message_id

    def send_document(chat_id, *a, **kw):
        message_id = real_send_document(chat_id, *a, **kw)
        _record_message(chat_id, message_id)
        return message_id

    def send_media_group(chat_id, *a, **kw):
        message_ids = real_send_media_group(chat_id, *a, **kw)
        for message_id in message_ids:
            _record_message(chat_id, message_id)
        return message_ids

    tg.send_message = send_message
    tg.send_photo = send_photo
    tg.send_document = send_document
    tg.send_media_group = send_media_group
    return tg


def handle(tg: Tg, update: dict, *, allowed_user_id: int,
           dry_run: bool = False) -> None:
    """Load the draft, handle the update, save the draft.

    One save site rather than one per mutation: `_fill_slot`, the `_PENDING`
    queue, `_switch_pipeline` and /confirm's clear all change the draft, and
    four independent save calls is how one of them ends up missing. `finally`,
    so a handler that raises still persists what it managed to change — the
    poll loop in main() logs and continues, and the next update must not see
    a stale draft.
    """
    if not allowed(update, allowed_user_id):
        return                              # silent: do not confirm the bot exists
    # From _identify, not update["message"], so a button press does not KeyError
    # here before its own branch in _handle ever runs.
    _, chat_id = _identify(update)
    # The other half of the ledger _track_sends builds for the bot's own
    # messages — a callback_query carries no new message of its own, so
    # there is nothing to record for that case.
    incoming = update.get("message")
    if incoming is not None:
        _record_message(chat_id, incoming.get("message_id"))
    if chat_id not in _LOADED:
        _LOADED.add(chat_id)
        salvaged = _load_draft(chat_id)
        if salvaged:
            # Told, not just logged. Otherwise /job answers "nothing assembled
            # yet" for a job the user knows they built, and the only trace is a
            # log line on a box they are not looking at.
            tg.send_message(
                chat_id,
                f"{ICON_WARN} <b>The job I was holding could not be read.</b>\n"
                f"Saved as <code>{_esc(salvaged)}</code> and set aside; the "
                "staged files are still on disk. Send the files again, or "
                "forward them from earlier in this chat.",
                parse_mode=PARSE_HTML)
    try:
        _handle(tg, update, allowed_user_id=allowed_user_id, dry_run=dry_run)
    finally:
        _save_draft(chat_id)


# callback_data prefixes. Kept this short because the Bot API caps
# callback_data at 64 bytes and an over-long button makes the whole
# sendMessage fail — i.e. the user sees nothing (Tg.keyboard asserts it).
_CB_SLOT = "slot:"
_CB_PIPE = "pipe:"
_CB_RUN_ASK = "run:ask"
_CB_RUN_GO = "run:go:"      # + the manifest's mtime_ns, see _run_token
_CB_RUN_NO = "run:no"
_CB_RUN_SWITCH = "run:sw:"  # + a key from _GPU_SHORT
# Open the switch-type / migrate submenus off the Choose GPU screen
# (2026-09-12). Exact-match, no trailing colon — same shape as _CB_RUN_ASK —
# and deliberately NOT "run:sw" + suffix: _CB_PIPE_ASK's own comment below
# is the standing lesson that a shorter key must never be a prefix of a
# longer one when dispatch matches by startswith.
_CB_RUN_SWITCH_MENU = "run:swmenu"
_CB_RUN_MIGRATE_MENU = "run:mgmenu"
# ◀ Back, distinct from _CB_RUN_ASK even though both re-render the main
# Choose GPU screen: _CB_RUN_ASK also lives on the job panel (a DIFFERENT
# message), and only Back should ever pass its own message_id in to be
# edited — passing the panel's id there would overwrite the manifest.
_CB_RUN_BACK = "run:back"
# The two tabs of the Choose GPU screen (spec §3.5). Exact matches, no trailing colon, and neither
# is a prefix of another key here. Not _CB_RUN_BACK for RunPod: Back insists on a drafted job in
# memory (_STATE), which the rent panel drawn after Phase A may no longer have while the bot is
# still up (the draft is persisted across a restart, _STATE is what gets cleared on submit), while
# these two accept the panel's own run token instead — so the tabs behave the same as each other
# wherever they are shown.
_CB_RUN_RUNPOD = "run:rp"
_CB_RUN_VAST = "run:vast"
# The provider a spend button was minted for rides IN its callback data, after the run token:
# "run:go:<token>:vast" or "run:go:<token>:runpod". Not in .env (a bot that dies mid-run would
# leave it behind) and not in bot state (lost on restart, and shared between two panels).
#
# The RunPod suffix was added after the RunPod-labelled screen shipped with none at all
# (2026-09-19 review finding: a button that names RunPod but carries no provider still asks
# start_drain to fall back to .env's GPU_PROVIDER, so a host misconfigured with
# GPU_PROVIDER=vast would rent Vast from a tap that said "RunPod"). Every NEWLY minted RunPod
# button now names its provider explicitly, same as Vast always has; a button already sitting in
# a chat from before this fix has no suffix and keeps meaning exactly what it meant — .env — which
# is the one case a suffix cannot retroactively fix.
_VAST_SUFFIX = ":vast"
_RUNPOD_SUFFIX = ":runpod"
# + "m"/"s"/"g" — which screen to redraw (main / switch menu / migrate menu).
_CB_RUN_REFRESH = "run:refresh:"
# The /gpu report's own Refresh button — separate from _CB_RUN_REFRESH
# because /gpu has no submenus to disambiguate between.
_CB_GPU_REFRESH = "gpu:refresh"
_CB_BALANCE_REFRESH = "bal:refresh"
# /subscribe's two-step chooser (pick a GPU, then pick a datacenter for it)
# and /unsubscribe's per-row remove button. Short "gs:" (GPU Subscribe) so
# none of the three is a prefix of another — same constraint _CB_PIPE_ASK's
# comment names for this whole scheme.
_CB_GPUSUB_PICK = "gs:pick:"  # + a key from _GPU_SHORT
_CB_GPUSUB_DC = "gs:dc:"      # + "<gpu short>:<datacenter_id>"
_CB_GPUSUB_RM = "gs:rm:"      # + "<gpu short>:<datacenter_id>"
_CB_REDO = "redo:"
_CB_CLEAR_ASK = "clr:ask"
_CB_CLEAR_GO = "clr:go"
_CB_CLEAR_NO = "clr:no"
_CB_WIPE_GO = "wipe:go"
_CB_WIPE_NO = "wipe:no"
_CB_KILL_ASK = "kill:ask"
_CB_KILL_GO = "kill:go"
_CB_KILL_NO = "kill:no"
# Both carry ONLY the destination datacenter. A migration moves the Network
# Volume; it has nothing to say about which GPU is rented afterwards, and the
# `<gpu_short>,` prefix this used to carry was never read by _ask_migrate —
# it was decoded back into a gpu_id that the function's body ignored. Worse,
# minting the button needed a _GPU_SHORT entry to build that prefix, so a GPU
# missing from that table silently got no migrate button at all.
_CB_MIGRATE_ASK = "mig:ask:"    # + "<to_dc>"
_CB_MIGRATE_GO = "mig:go:"      # + "<to_dc>"
_CB_MIGRATE_NO = "mig:no"

# The recovery buttons _deliver_provision_failure offers when pod-
# provision.sh itself failed with "no instances available" for an already-
# confirmed batch (drain.py wrote a ProvisionFailure next to its manifest).
# Distinct from _CB_RUN_SWITCH/_CB_MIGRATE_ASK: those act on the job still
# being drafted in _STATE, these act on a manifest that already ran its
# local phase — see _do_resume. Subscribing reuses _CB_GPUSUB_DC as-is,
# no new constant needed for that one.
_CB_RECOVER_WAIT = "rec:wait"
_CB_RECOVER_SWITCH = "rec:sw:"     # + "<gpu short>:<manifest stem>"
_CB_RECOVER_MIGRATE = "rec:mig:"   # + "<to_dc>:<manifest stem>"
# Same-GPU retry. Before this existed the card's four other buttons each did
# something else — switch GPU type, migrate datacenter, subscribe to a stock
# alert, or dismiss (Đợi) — and none of them resumed on the GPU the batch had
# just failed on. A user whose card had scrolled away had no way to resume
# without /confirm, which minted a new batch id and re-ran every try-on.
_CB_RECOVER_RETRY = "rec:retry:"   # + "<manifest stem>"
# Opens the Vast tab of the rent panel for the batch whose RunPod rental just failed (spec §3.5).
# Carries the stem like the other recovery buttons, and is only honoured for the manifest THIS chat
# is on: it opens a panel, it never spends — the spend button on that panel carries its own token.
_CB_RECOVER_VAST = "rec:vast:"     # + "<manifest stem>"

# The reuse-or-rerun chooser _do_confirm sends when the journal already holds
# a matching try-on. Both carry _run_token for the same reason the spend
# button does: Telegram keyboards stay tappable forever, and a chooser minted
# for one manifest must not be answerable after it was rewritten.
_CB_PHASE_A_REUSE = "pa:reuse:"   # + _run_token
_CB_PHASE_A_RERUN = "pa:rerun:"   # + _run_token

# The post-Phase-A spend button. Routes to _do_resume, never _do_confirm:
# _do_confirm starts from _STATE and clears it before returning, so by the
# time Phase A finishes minutes later the draft job is gone and a panel wired
# to _CB_RUN_GO would answer "no complete job yet" for a batch whose try-on
# images are sitting on disk. _do_resume needs no _STATE — it loads the
# manifest and requires only that the journal has a batch id, which Phase A
# writes before its first Gemini call.
#
# Carries _run_token, like _CB_PHASE_A_REUSE and _CB_PHASE_A_RERUN. The
# manifest stem looks sufficient — "the manifest is not rewritten between the
# panel and the tap" — but that is not an invariant: _job_manifest_path(chat_id)
# IS the file the stem names, the same file _run_token stamps with mtime_ns and
# _maybe_show_manifest rewrites, and once Phase A has exited busy() is False so
# nothing blocks that rewrite while the panel sits unanswered. A stem-carrying
# button cannot detect it and would then rent $0.99/hour against inputs the user
# never reviewed, the exact harm _run_token's own docstring names. The handler
# derives the path from chat_id, so the token is only ever proof of which
# manifest was seen, never a path.
_CB_PHASE_A_SPEND = "pa:spend:"   # + _run_token

# The Regenerate button under one try-on preview (2026-09-18). Carries the
# run's INDEX in the manifest, not its id: run ids are up to ~51 characters
# (four 12-character stems, see job.run_id_for) and the _run_token after them
# is 19 digits, which together overflow the 64-byte cap. The token is what
# makes the index safe — a rewritten manifest invalidates it, the same way it
# invalidates the spend button.
_CB_TRYON_REGEN = "rg:"           # + "<run index>:<_run_token>"
# Retry a FAILED try-on with a chosen provider (2026-09-18). Same index+token
# shape as _CB_TRYON_REGEN; "qwen-max" is the longest provider, so the worst
# case is "rt:" + 2 + 9 + 19 digits, well under 64 bytes.
_CB_TRYON_RETRY = "rt:"           # + "<run index>:<provider>:<_run_token>"

# ONE number, everywhere a migration's duration is quoted: the /gpu listing,
# the [Run] picker's "Other regions" note, the destructive confirm, and the
# "started" reply. They said "~25-30 min" in two of those places and "15-25
# minutes" in the other two, for the same operation on the same screen.
#
# 15-25 min is the number docs/gpu-pod.md#volume-migrate actually stands
# behind: the older ~25-30 was extrapolated from a single-thread ~57MB/s
# measurement and was explicitly retired there on 30/08/2026, after the
# 29/08/2026 measurement table put the sync itself at ~3 min and located the
# rest of the time in booting two temp pods, the checksum verify, and
# provisioning the real GPU pod afterwards.
MIGRATE_DURATION_PLAIN = "15-25 minutes"
MIGRATE_DURATION_SHORT = "~15-25 min"
MIGRATE_DURATION_TEXT = f"Estimated {MIGRATE_DURATION_PLAIN}"
_CB_ADD = "add"
_CB_JOB_EDIT = "bj:e:"   # + _job_digest
_CB_JOB_DROP = "bj:d:"   # + _job_digest
_CB_JOB_OPEN = "bj:open" # the square of the job already on screen
_CB_JOB_HERE = "bj:here" # drop the job currently on screen
# "pipe-ask", not "pipe:ask": _CB_PIPE is the prefix "pipe:", and the dispatch
# matches it with startswith, so "pipe:ask" was swallowed by that branch and
# read as a request to switch to a pipeline named "ask". Caught by a test on
# its first run — a prefix scheme needs the shorter key to not be a prefix of
# the longer one, which is easy to lose sight of when adding a key months later.
_CB_PIPE_ASK = "pipe-ask"
# Same shape, same "-ask" not ":ask" reasoning as _CB_PIPE_ASK above.
_CB_PROVIDER = "prov:"
_CB_PROVIDER_ASK = "prov-ask"


def _split_provider(rest: str) -> tuple[str, str | None] | None:
    """`<token>` -> (token, None); `<token>:vast` -> (token, "vast"); `<token>:runpod` ->
    (token, "runpod"); anything else -> None.

    `(token, None)` also means "not decided yet" for the Phase A try-on-first confirm, which mints
    no suffix at all — that screen rents nothing, so there is no provider to name. A bare token
    reaching a screen that DOES rent (the RunPod stock screen, since the runpod suffix above was
    added) can only be a button minted before that fix; it still falls back to .env, unchanged.

    A trailing suffix this version does not know (a button from a newer or older bot) returns
    None outright — the callers treat that like a stale token: refuse, spend nothing."""
    token, sep, suffix = rest.partition(":")
    if not sep:
        return token, None
    if suffix == _VAST_SUFFIX[1:]:
        return token, "vast"
    if suffix == _RUNPOD_SUFFIX[1:]:
        return token, "runpod"
    return None


def _run_token(chat_id: int) -> str:
    """A stamp identifying the exact manifest a Run button was offered for.

    Telegram keeps old inline keyboards tappable forever — there is no expiry
    and no way to make one single-use. Without this, a Run button from a
    manifest the user has since changed (or already submitted) stays live, and
    tapping it would spend $0.99/hour on inputs they never reviewed.

    mtime_ns of the manifest, because _maybe_show_manifest rewrites that file
    every time it renders: any change to the job invalidates every button
    minted before it, with no extra state to keep in sync and nothing to lose
    across a restart.
    """
    try:
        return str(_job_manifest_path(chat_id).stat().st_mtime_ns)
    except OSError:
        return "0"


def _handle_callback(tg: Tg, chat_id: int, query: dict, *, dry_run: bool) -> None:
    """Dispatch a button press.

    answerCallbackQuery in a `finally`: until it is called the client spins on
    the button and eventually reports the bot as unresponsive, even when the
    work succeeded. That must happen whether the branch replied, refused, or
    raised.
    """
    data = query.get("data") or ""
    try:
        if data.startswith(_CB_SLOT):
            role = data[len(_CB_SLOT):]
            job = _job_for(chat_id)
            queue = _PENDING.get(chat_id) or []
            if role not in _askable_roles(job.pipeline):
                # Reachable from a keyboard minted before a /pipeline switch.
                # The file is still waiting — re-ask with the buttons that
                # match the pipeline now in effect instead of dead-ending on
                # an error the user has no button to recover from.
                if queue:
                    path, p = queue[0]
                    tg.send_message(chat_id,
                                    f"{role} is not a slot for {job.pipeline} "
                                    f"— asking again:")
                    _ask_about(tg, chat_id, p, job.pipeline, path=path)
                else:
                    tg.send_message(chat_id,
                                    f"{role} is not a slot for {job.pipeline}")
            elif not queue:
                tg.send_message(chat_id, "no file is waiting for a slot — that "
                                         "button is from an earlier question")
            else:
                _answer_slot(tg, chat_id, role)

        elif data.startswith(_CB_PIPE):
            msg_id = (query.get("message") or {}).get("message_id")
            _switch_pipeline_and_report(tg, chat_id, data[len(_CB_PIPE):],
                                        chooser_message_id=msg_id)

        elif data.startswith(_CB_PROVIDER):
            msg_id = (query.get("message") or {}).get("message_id")
            _switch_provider_and_report(tg, chat_id, data[len(_CB_PROVIDER):],
                                        chooser_message_id=msg_id)

        elif data == _CB_RUN_ASK:
            job = _STATE.get(chat_id)
            if job is None or missing_slots(job):
                tg.send_message(chat_id, "no complete job yet — send the "
                                         "required files first")
            else:
                # The second step. Deliberately a separate tap: the first one
                # is next to the manifest and easy to hit by accident.
                _offer_run_for_chat(tg, chat_id)

        elif data.startswith(_CB_RUN_SWITCH):
            gpu_id = _GPU_BY_SHORT.get(data[len(_CB_RUN_SWITCH):])
            if gpu_id is None:
                tg.send_message(chat_id, "that button is from an older "
                                         "version of the bot; tap Run again")
            else:
                env_set(ROOT / ".env", "GPU", gpu_id)
                # Edits the submenu message straight into the refreshed main
                # screen (2026-09-12) — its own "Current:" line already says
                # what changed, so a separate "switched — GPU is now X" text
                # message would just be one more message saying the same thing.
                msg_id = (query.get("message") or {}).get("message_id")
                _offer_run_for_chat(tg, chat_id, message_id=msg_id)

        elif data == _CB_RUN_SWITCH_MENU:
            msg_id = (query.get("message") or {}).get("message_id")
            _offer_run_switch_menu(tg, chat_id, message_id=msg_id)

        elif data == _CB_RUN_MIGRATE_MENU:
            msg_id = (query.get("message") or {}).get("message_id")
            _offer_run_migrate_menu(tg, chat_id, message_id=msg_id)

        elif data == _CB_RUN_VAST or data == _CB_RUN_RUNPOD:
            msg_id = (query.get("message") or {}).get("message_id")
            job = _STATE.get(chat_id)
            # Back's guard, plus one more way in: the panel drawn after Phase A may have no draft
            # job left in memory, only the manifest its run token names.
            if (job is None or missing_slots(job)) \
                    and _PHASE_A_OFFERED.get(chat_id) != _run_token(chat_id):
                tg.send_message(chat_id, "no complete job yet — send the "
                                         "required files first")
            else:
                on_vast = data == _CB_RUN_VAST
                if on_vast and msg_id is not None:
                    tg.edit_message(chat_id, msg_id, "🔄 Asking Vast for offers…")
                _offer_run_for_chat(tg, chat_id, message_id=msg_id,
                                    gpu_provider="vast" if on_vast else None)

        elif data == _CB_RUN_BACK:
            msg_id = (query.get("message") or {}).get("message_id")
            job = _STATE.get(chat_id)
            if job is None or missing_slots(job):
                tg.send_message(chat_id, "no complete job yet — send the "
                                         "required files first")
            else:
                _offer_run_for_chat(tg, chat_id, message_id=msg_id)

        elif data.startswith(_CB_RUN_REFRESH):
            view = data[len(_CB_RUN_REFRESH):]
            msg_id = (query.get("message") or {}).get("message_id")
            # A brief interstitial (2026-09-12): the real recheck below skips
            # stock_at_cached's TTL and hits runpodctl live, which can take a
            # couple of seconds — an unchanged screen for that long reads as a
            # dead button, not a working one.
            if msg_id is not None:
                tg.edit_message(chat_id, msg_id, "🔄 Refreshing stock…")
            if view == "s":
                _offer_run_switch_menu(tg, chat_id, message_id=msg_id, force=True)
            elif view == "g":
                _offer_run_migrate_menu(tg, chat_id, message_id=msg_id, force=True)
            elif view == "v":
                _offer_run_for_chat(tg, chat_id, message_id=msg_id, force=True,
                                    gpu_provider="vast")
            else:
                _offer_run_for_chat(tg, chat_id, message_id=msg_id, force=True)

        elif data == _CB_GPU_REFRESH:
            msg_id = (query.get("message") or {}).get("message_id")
            if msg_id is not None:
                tg.edit_message(chat_id, msg_id, "🔄 Refreshing stock…")
            _report_gpu_stock(tg, chat_id, message_id=msg_id, force=True)

        elif data == _CB_BALANCE_REFRESH:
            msg_id = (query.get("message") or {}).get("message_id")
            if msg_id is not None:
                tg.edit_message(chat_id, msg_id, "🔄 Refreshing balance…")
            _report_balance(tg, chat_id, message_id=msg_id)

        elif data.startswith(_CB_GPUSUB_PICK):
            msg_id = (query.get("message") or {}).get("message_id")
            if msg_id is not None:
                _offer_gpu_sub_datacenters(tg, chat_id, msg_id,
                                           data[len(_CB_GPUSUB_PICK):])

        elif data.startswith(_CB_GPUSUB_DC):
            msg_id = (query.get("message") or {}).get("message_id")
            short, _, dc = data[len(_CB_GPUSUB_DC):].partition(":")
            if msg_id is not None and dc:
                _add_gpu_sub(tg, chat_id, msg_id, short, dc)

        elif data.startswith(_CB_GPUSUB_RM):
            msg_id = (query.get("message") or {}).get("message_id")
            short, _, dc = data[len(_CB_GPUSUB_RM):].partition(":")
            if msg_id is not None and dc:
                _remove_gpu_sub(tg, chat_id, msg_id, short, dc)

        elif data.startswith(_CB_RUN_GO):
            parsed = _split_provider(data[len(_CB_RUN_GO):])
            if parsed is None or parsed[0] != _run_token(chat_id):
                tg.send_message(chat_id,
                                "the job changed since that button was sent, so "
                                "nothing ran. Check the manifest above and "
                                "confirm again.")
            elif _job_has_local_tryon(chat_id):
                # Two-step flow: Phase A first, rent afterwards. _do_confirm is
                # NOT called here — it clears _STATE, and the panel
                # tick_phase_a renders minutes later needs the manifest on disk
                # and the journal to have a batch id, both of which Phase A
                # produces. Nothing has been spent yet at this point.
                _do_phase_a(tg, chat_id, dry_run=dry_run)
            else:
                _do_confirm(tg, chat_id, dry_run=dry_run, gpu_provider=parsed[1])

        elif data.startswith(_CB_PHASE_A_REUSE) or data.startswith(_CB_PHASE_A_RERUN):
            reuse = data.startswith(_CB_PHASE_A_REUSE)
            prefix = _CB_PHASE_A_REUSE if reuse else _CB_PHASE_A_RERUN
            parsed = _split_provider(data[len(prefix):])
            if parsed is None or parsed[0] != _run_token(chat_id):
                tg.send_message(chat_id,
                                "the job changed since that button was sent, so "
                                "nothing ran. Check the manifest above and "
                                "confirm again.")
            else:
                _do_confirm(tg, chat_id, dry_run=dry_run,
                            phase_a_choice="reuse" if reuse else "rerun",
                            gpu_provider=parsed[1])

        elif data.startswith(_CB_PHASE_A_SPEND):
            parsed = _split_provider(data[len(_CB_PHASE_A_SPEND):])
            if parsed is None or parsed[0] != _run_token(chat_id):
                tg.send_message(chat_id,
                                "the job changed since that button was sent, so "
                                "nothing ran. Check the manifest above and "
                                "confirm again.")
            else:
                # _do_resume, not _do_confirm — see _CB_PHASE_A_SPEND's own
                # comment for why _STATE cannot be relied on here. The path
                # comes from chat_id, the way _CB_RUN_GO's branch reaches
                # _do_confirm; the token proved which manifest was reviewed.
                #
                # The latch is dropped only once the rental really started: a refusal
                # (a Vast spend the gate turned down, a migration in flight) leaves the
                # panel it came from re-renderable instead of falling back to the
                # "run try-on first" screen.
                if _do_resume(tg, chat_id, _job_manifest_path(chat_id),
                              dry_run=dry_run, gpu_provider=parsed[1]):
                    _PHASE_A_OFFERED.pop(chat_id, None)

        elif data.startswith(_CB_TRYON_REGEN):
            index, _, token = data[len(_CB_TRYON_REGEN):].partition(":")
            _regen_tryon(tg, chat_id, index, token, dry_run=dry_run)

        elif data.startswith(_CB_TRYON_RETRY):
            index, _, rest = data[len(_CB_TRYON_RETRY):].partition(":")
            provider, _, token = rest.partition(":")
            _retry_tryon(tg, chat_id, index, provider, token, dry_run=dry_run)

        elif data == _CB_RUN_NO:
            tg.send_message(chat_id, "cancelled — nothing was spent")

        elif data.startswith(_CB_REDO):
            _redo_slot(tg, chat_id, data[len(_CB_REDO):])

        elif data == _CB_ADD:
            _add_to_batch(tg, chat_id)

        elif data.startswith(_CB_JOB_EDIT):
            _edit_from_batch(tg, chat_id, data[len(_CB_JOB_EDIT):])

        elif data == _CB_PIPE_ASK:
            _offer_pipelines(tg, chat_id)

        elif data == _CB_PROVIDER_ASK:
            _offer_providers(tg, chat_id)

        elif data == _CB_JOB_OPEN:
            tg.answer_callback_query(query.get("id") or "",
                                     "this one is already open")
            return

        elif data == _CB_JOB_HERE:
            _drop_current(tg, chat_id)

        elif data.startswith(_CB_JOB_DROP):
            # Still handled although nothing offers it any more: Telegram never
            # expires an inline keyboard, so panels minted before the layout
            # changed are still sitting in the chat with these buttons on them.
            _drop_from_batch(tg, chat_id, data[len(_CB_JOB_DROP):])

        elif data == _CB_CLEAR_ASK:
            _ask_to_clear(tg, chat_id)

        elif data == _CB_CLEAR_GO:
            _clear_job(tg, chat_id)

        elif data == _CB_CLEAR_NO:
            tg.send_message(chat_id, "kept — nothing deleted")

        elif data == _CB_WIPE_GO:
            _wipe_chat(tg, chat_id)

        elif data == _CB_WIPE_NO:
            tg.send_message(chat_id, "kept — nothing deleted")

        elif data == _CB_KILL_ASK:
            _ask_kill(tg, chat_id)

        elif data == _CB_KILL_GO:
            _do_kill(tg, chat_id)

        elif data == _CB_KILL_NO:
            tg.send_message(chat_id, "left running — nothing killed")

        elif data.startswith(_CB_MIGRATE_ASK):
            _ask_migrate(tg, chat_id, data[len(_CB_MIGRATE_ASK):])

        elif data.startswith(_CB_MIGRATE_GO):
            _start_migration(tg, chat_id, data[len(_CB_MIGRATE_GO):])

        elif data == _CB_MIGRATE_NO:
            tg.send_message(chat_id, "kept — nothing migrated")
            # Drops a resume request minted by the recovery Migrate button
            # (see _CB_RECOVER_MIGRATE below) — cancelling here means there is
            # nothing left to resume when some LATER, unrelated migration
            # finishes.
            _migrate_resume_marker().unlink(missing_ok=True)

        elif data == _CB_RECOVER_WAIT:
            # Count-agnostic on purpose: this handler gets a bare rec:wait with
            # no manifest stem, so it cannot know whether the card above it just
            # claimed "0/2 preserved", or whether the batch had any try-on
            # images at all. It matches the card's generic fallback line, which
            # is why it stays true in all three states.
            tg.send_message(chat_id, "OK — parked. Nothing already finished is "
                                     "lost. Tap <b>Thử lại</b> above when you "
                                     "want to rent again.",
                            parse_mode=PARSE_HTML)

        elif data.startswith(_CB_RECOVER_RETRY):
            stem = data[len(_CB_RECOVER_RETRY):]
            if not stem:
                tg.send_message(chat_id, "that button is from an older "
                                         "version of the bot; check /status")
            else:
                # RunPod-only recovery card (_deliver_provision_failure): explicit, same
                # reasoning as _RUNPOD_SUFFIX — never leave a RunPod-labelled tap to .env.
                _do_resume(tg, chat_id, ROOT / "batch" / f"{stem}.yaml",
                           dry_run=dry_run, gpu_provider="runpod")

        elif data.startswith(_CB_RECOVER_VAST):
            stem = data[len(_CB_RECOVER_VAST):]
            live_manifest = _job_manifest_path(chat_id)
            # This chat's batch, AND the failure that card reported is still outstanding, AND no new
            # job is being assembled: the stem is the same for every batch of a chat, so an old
            # card would otherwise re-open the rent panel for whatever is on disk now.
            still_failed = read_provision_failure(provision_failure_path(live_manifest)) is not None
            if not stem or stem != live_manifest.stem or not still_failed or chat_id in _STATE:
                tg.send_message(chat_id, "that button is from an earlier batch; "
                                         "check /status")
            else:
                # The same latch tick_phase_a sets, so every re-render of this panel (Refresh,
                # the [RunPod] tab, Back) stays on the rent panel and its spend button resumes
                # into a rental instead of dropping to the "run try-on first" screen.
                _PHASE_A_OFFERED[chat_id] = _run_token(chat_id)
                # The interstitial becomes the panel: the search takes a few seconds and an
                # unchanged chat for that long reads as a dead button.
                wait_id = tg.send_message(chat_id, "🔄 Asking Vast for offers…")
                _offer_run_for_chat(tg, chat_id, message_id=wait_id, gpu_provider="vast")

        elif data.startswith(_CB_RECOVER_SWITCH):
            short, _, stem = data[len(_CB_RECOVER_SWITCH):].partition(":")
            gpu_id = _GPU_BY_SHORT.get(short)
            if gpu_id is None or not stem:
                tg.send_message(chat_id, "that button is from an older "
                                         "version of the bot; check /status")
            else:
                env_set(ROOT / ".env", "GPU", gpu_id)
                # Same RunPod-only recovery card, a different GPU type — still explicit.
                _do_resume(tg, chat_id, ROOT / "batch" / f"{stem}.yaml",
                          dry_run=dry_run, gpu_provider="runpod")

        elif data.startswith(_CB_RECOVER_MIGRATE):
            to_dc, _, stem = data[len(_CB_RECOVER_MIGRATE):].partition(":")
            if not to_dc or not stem:
                tg.send_message(chat_id, "that button is from an older "
                                         "version of the bot; check /status")
            else:
                _migrate_resume_marker().write_text(
                    json.dumps({"stem": stem}), encoding="utf-8")
                _ask_migrate(tg, chat_id, to_dc)

        else:
            tg.send_message(chat_id, "that button is from an older version of "
                                     "the bot; send /start for the commands")
    finally:
        tg.answer_callback_query(query.get("id") or "")


def _offer_pipelines(tg: Tg, chat_id: int) -> None:
    """Pick the flow for the job on screen — buttons only, no wall of text.

    The first version printed all five pipelines with their stages and then
    repeated the same five as buttons underneath, which is the same list twice
    and neither copy readable. The button now carries the stages, because the
    stages ARE the choice; the canonical name is what `/pipeline <name>` takes
    and is not what anyone reads when deciding.

    Shared by `/pipeline` and the ⚙️ button (2026-09-01). The choice is per
    job: open a queued job with its coloured square, tap ⚙️, pick. Everything
    it changes lands on `_STATE`, which is whichever job is open, so one flow
    serves the whole batch.
    """
    job = _job_for(chat_id)
    basket = _BASKET.get(chat_id) or []
    # Which row of the sheet is being changed, when there is more than one.
    where = f" {_row_mark(len(basket))}" if _jobs_for(chat_id)[1:] else ""
    tg.send_message(
        chat_id,
        f"{ICON_ASK_CE} <b>Flow for{where}</b>\n"
        f"now: {_flow(job.pipeline)}  <i>({_esc(job.pipeline)})</i>",
        buttons=[[("🎬 " + " → ".join(PIPELINES[name]), _CB_PIPE + name)]
                 for name in sorted(PIPELINES) if name != job.pipeline],
        parse_mode=PARSE_HTML)


def _switch_pipeline_and_report(tg: Tg, chat_id: int, name: str, *,
                                chooser_message_id: int | None = None) -> None:
    """The /pipeline body, shared by the typed command and the buttons."""
    job = _job_for(chat_id)
    if name not in PIPELINES:
        tg.send_message(chat_id, "no pipeline called that. send /pipeline to "
                                 "list them.")
        return
    if name == job.pipeline:
        tg.send_message(chat_id, f"already on {name}")
        return
    job, dropped = _switch_pipeline(chat_id, name)
    # A note, not a message: the panel's first line IS the pipeline and its
    # slot list is redrawn below, so a separate message would restate what the
    # reader is already looking at. What a redraw cannot show is the dropped
    # material, because the evidence of it is exactly what just disappeared.
    _PANEL_NOTE[chat_id] = (
        f"switched to {name}"
        + (f" · dropped {', '.join(dropped)} — no stage in {name} uses it"
           if dropped else ""))
    # Re-render against the new pipeline rather than leaving the manifest the
    # user last saw on screen: that file is what /confirm submits. bump=True
    # for the same reason /job uses it: this is an explicit user action, and
    # a silent edit somewhere above the chooser would look like the switch
    # dropped the panel entirely (2026-09-02).
    _maybe_show_manifest(tg, chat_id, job, bump=True)
    if chooser_message_id is not None:
        # The chooser message otherwise sits untouched after the tap — its
        # buttons still say "now: <old pipeline>", so the switch looks like
        # it did nothing unless the reader also notices the panel changed
        # further down the chat (2026-09-02, reported as "no feedback").
        # Editing it in place, with no buttons, closes the loop right where
        # the tap happened and retires that keyboard so it can't be reused.
        tg.edit_message(
            chat_id, chooser_message_id,
            f"{ICON_EDIT_CE} <b>Flow switched</b>\n"
            f"now: {_flow(job.pipeline)}  <i>({_esc(job.pipeline)})</i>",
            parse_mode=PARSE_HTML)


def _offer_providers(tg: Tg, chat_id: int) -> None:
    """Pick who runs try-on for the job on screen — same shape as _offer_pipelines.

    Only reachable from a pipeline that actually has a try-on stage
    (_fix_buttons hides the button otherwise); /provider itself still reports
    plainly rather than showing an empty chooser, since it can be typed
    regardless of what pipeline happens to be open.
    """
    job = _job_for(chat_id)
    if _tryon_stage(job.pipeline) is None:
        tg.send_message(chat_id, f"{_esc(job.pipeline)} has no try-on stage — "
                                 "nothing here for a provider to run.")
        return
    tg.send_message(
        chat_id,
        f"{ICON_ASK_CE} <b>Try-on provider</b>\n"
        f"now: {_esc(PROVIDER_LABELS.get(job.provider, job.provider))}",
        buttons=[[(label, _CB_PROVIDER + key)]
                 for key, label in PROVIDER_LABELS.items() if key != job.provider],
        parse_mode=PARSE_HTML)


def _switch_provider_and_report(tg: Tg, chat_id: int, provider: str, *,
                                chooser_message_id: int | None = None) -> None:
    """The /provider body, shared by the typed command and the buttons."""
    job = _job_for(chat_id)
    if provider not in PROVIDER_LABELS:
        tg.send_message(chat_id, "no provider called that. send /provider to "
                                 "list them.")
        return
    if _tryon_stage(job.pipeline) is None:
        tg.send_message(chat_id, f"{_esc(job.pipeline)} has no try-on stage — "
                                 "nothing here for a provider to run.")
        return
    if provider == job.provider:
        tg.send_message(chat_id, f"already on {provider}")
        return
    job = _switch_provider(chat_id, provider)
    _PANEL_NOTE[chat_id] = f"try-on provider switched to {provider}"
    _maybe_show_manifest(tg, chat_id, job, bump=True)
    if chooser_message_id is not None:
        tg.edit_message(
            chat_id, chooser_message_id,
            f"{ICON_EDIT_CE} <b>Provider switched</b>\n"
            f"now: {_esc(PROVIDER_LABELS[job.provider])}",
            parse_mode=PARSE_HTML)


def _answer_slot(tg: Tg, chat_id: int, role: str) -> None:
    """Assign the head of the queue to `role` — the one path, two callers.

    A typed reply and a tapped button must do the SAME thing: pop the head,
    fill the slot, re-render the manifest, and ask about whatever is still
    queued. Buttons arrived on 2026-08-31 and duplicating this sequence for
    them is how the two drift — finding I7 lives inside it (see _fill_slot).
    """
    queue = _PENDING.get(chat_id) or []
    if not queue:
        return
    job = _job_for(chat_id)
    path, p = queue.pop(0)
    if not queue:
        _PENDING.pop(chat_id, None)
    _CONFIRM_WARNED.discard(chat_id)
    # _fill_slot, not a bare assignment: answering a role that is already
    # filled pops the queue head AND overwrites the slot, so the displaced
    # file is gone in the same step (finding I7).
    _fill_slot(tg, chat_id, job, role, path, p)
    _maybe_show_manifest(tg, chat_id, job)
    if queue:
        # The next file was already queued (it arrived before this answer) —
        # ask about it now rather than waiting for another document.
        next_path, next_p = queue[0]
        _ask_about(tg, chat_id, next_p, job.pipeline, path=next_path)




# Measured 2026-09-04, after enabling Telegram Premium on this bot's owner
# account: a `<tg-emoji emoji-id="...">` entity now survives — sendMessage's
# own response carries the entity back instead of stripping it, and it
# renders as a real animated icon on the phone. The earlier note here only
# knew of one path to that entitlement (a Fragment-bought username, ~5000
# TON); the owning account's Premium subscription is a second, far cheaper
# one. Every `_CE` glyph below degrades to its own plain fallback character
# if that subscription ever lapses — Telegram strips the entity silently,
# same as before — so lapsing costs the animation, never a broken render.
#
# This changes where motion inside a message comes from. It used to be ONLY
# re-editing (run._SPIN's hand-cycled braille, run._HOURGLASS's flip) because
# nothing else could move without a fresh HTTP call. An animated custom
# emoji moves on its own, client-side, for as long as the message exists —
# which is nicer to look at but means it can no longer prove the process
# behind it is still alive (it keeps spinning after a drain dies mid-stage,
# same as before it died). run.py's `_elapsed()` carries that proof now: a
# real, still-ticking mm:ss is the one thing in run.progress_text that still
# has to change through an edit, not the icons.
def _ce(emoji_id: str, glyph: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{glyph}</tg-emoji>'


# Buttons can't carry the `_ce()` HTML tag (Telegram renders button text
# literally, no parsing) but Bot API 9.4 added a field just for this,
# icon_custom_emoji_id, that Tg.keyboard() accepts as an optional 3rd tuple
# element — same eligibility as `_ce()` (owner's Telegram Premium). This pulls
# the id back out of an already-built `_ce()` string instead of hardcoding it
# a second time at the button call site, so the two never drift apart.
def _ce_id(ce: str) -> str:
    return re.search(r'emoji-id="(\d+)"', ce).group(1)


# Role icons are shown in two kinds of place: button labels (_ask_about's
# rows, the redo button) and HTML message text (_role_line, _sheet_caption).
# Buttons cannot carry entities — Telegram renders their text literally, no
# parsing — so ROLE_ICON stays plain for those, and ROLE_ICON_CE (HTML text
# only) carries the animated version. Only "driver" differs from its plain
# counterpart: the driver slot is always a TikTok download now (tgbot/tiktok.py),
# so its message icon is that app's real logo, not a generic clapper.
ROLE_ICON = {"character": "👤", "outfit": "👗", "driver": "🎬", "background": "🖼"}
ROLE_ICON_CE = {**ROLE_ICON, "driver": _ce("5327982530702359565", "📱")}

# One square per job, in the same order as preview.ROW_ACCENTS and the same
# colours. Nothing can be written onto the contact sheet — `drawtext` is not
# compiled into the ffmpeg this runs against — so colour is the only legend
# that reads in the picture and in the text at once: the bar down the left of
# row 2 is the square printed beside entry 2 here.
ROW_MARK = ["🟦", "🟧", "🟩", "🟪", "🟥", "🟨"]


def _row_mark(index: int) -> str:
    return ROW_MARK[index % len(ROW_MARK)]


ICON_OK_CE = _ce("5980930633298350051", "✅")
ICON_WARN = _ce("5420323339723881652", "⚠️")
ICON_EMPTY = _ce("5884089033558070257", "⬜️")

# One HTML-only animated icon per message-text moment that used to carry a
# plain glyph (or none). Named for the moment, not the picture, because the
# picture is exactly what would make a future swap look like a typo.
ICON_ERROR_CE = _ce("5210952531676504517", "❌")
ICON_TRASH_CE = _ce("5445267414562389170", "🗑")
ICON_CLIP_CE = _ce("5305265301917549162", "📎")
ICON_ASK_CE = _ce("5341715473882955310", "⚙️")
ICON_EDIT_CE = _ce("5395444784611480792", "✏️")
ICON_ROCKET_CE = _ce("5188481279963715781", "🚀")
ICON_DEPART_CE = _ce("5201691993775818138", "🛫")
ICON_MONEY_CE = _ce("5201873447554145566", "💵")
# FinanceEmoji#57 (money bag) — distinct from ICON_MONEY_CE above so the
# "you're about to spend" warning in /start doesn't reuse the same glyph as
# the per-hour price display it sits near (2026-09-15).
ICON_SPEND_CE = _ce("5287231198098117669", "💰")
ICON_ANNOUNCE_CE = _ce("5424818078833715060", "📣")
ICON_ALERT_CE = _ce("5440660757194744323", "‼️")
ICON_FLAG_CE = _ce("5460755126761312667", "🚩")
ICON_EYES_CE = _ce("5210956306952758910", "👀")
ICON_CRITICAL_CE = _ce("5395695537687123235", "🚨")
ICON_SPEAK_CE = _ce("5460795800101594035", "🗣️")
# The Nvidia logo (EmojiTechPack#158) — every GPU this repo has ever rented
# is Nvidia (RTX 5090/4090, RTX PRO 4500), so it stands in for "GPU" wherever
# 🖥 used to, in message text. Fallback glyph is 💻 (the pack's own fallback
# tag, not what it depicts — same mismatch as ROLE_ICON_CE's TikTok logo).
ICON_NVIDIA_CE = _ce("4994617077676376661", "💻")
# A generic GPU/hardware icon (nedonews#39), distinct from the Nvidia brand
# logo above — not wired to any message yet; kept alongside ICON_NVIDIA_CE
# since both came out of the same round of icon picks (2026-09-04).
ICON_GPU_CE = _ce("5269375507220165755", "👊")
# Button-only icons (no HTML text use, unlike the ones above) — user picked
# the pack for each by hand (2026-09-12): Run from cwdinfo_aemoji, Add from
# NewsEmoji (the same pack most of the icons above already came from).
ICON_RUN_CE = _ce("5422837510499739688", "▶️")
ICON_ADD_CE = _ce("5397916757333654639", "➕")
# Button-only, same as Run/Add above — picked from the Interface_Icons pack
# (2026-09-12), the two-arrow loop glyph closest to the plain "🔄" it replaces.
ICON_REFRESH_CE = _ce("5465680951738637726", "🔄")

PARSE_HTML = "HTML"

# Attached once, to /start's reply. Labels are the exact literal command text
# — tapping a reply-keyboard button sends its label back as a plain message,
# so an emoji-decorated label (e.g. "🎨 /pipeline") would stop matching the
# `text.startswith("/pipeline")` dispatch below and silently do nothing.
# /pipeline and /status work with no argument, so those two taps just work;
# /result and /tryon still need an argument for anything but this chat's own
# job, so a bare tap falls through to their usage message instead of erroring
# silently (2026-09-01, see the /start UI rework).
START_KEYBOARD = [["/pipeline", "/status"],
                  ["/result", "/tryon"]]


def _esc(value: object) -> str:
    """Escape a dynamic value for parse_mode=HTML.

    Applied to everything interpolated, not only to what looks dangerous. One
    stray `<` makes Telegram reject the WHOLE message, so the user sees nothing
    — the same silence class as the NON_FILE_MEDIA bug. Staged filenames are
    already reduced to [A-Za-z0-9._-] by _safe_name, so nothing can carry a
    bracket today; this stops that from being load-bearing.
    """
    return html.escape(str(value), quote=False)


def _compact(p: Probe) -> str:
    """The numbers worth a glance: resolution, duration for a video, size.

    Bitrate stays in the expandable block — it is how a re-compressed driver is
    caught, but it is diagnostic, and five figures per line is what made the
    first version of this screen a wall of text the user could not read.

    Duration came back out of that block on 2026-09-01, at the user's request
    and correctly: it is not diagnostic, it is an INPUT to the decision. It
    picks the preset (`ingest.suggest_preset`), the preset sets how much work
    the pod does, and that is what the $0.99/hour buys. A 30-second driver
    where a 15-second one was intended is the difference the estimate on the
    same screen is computed from, and it was one tap away.
    """
    length = f" · {p.duration_s:.1f}s" if p.kind == "video" else ""
    return f"{p.width}×{p.height}{length} · {p.size_bytes / 1_000_000:.1f} MB"


def _role_line(role: str, job: Job) -> str:
    """One line per slot: state, role icon, name, the two headline numbers."""
    icon_role = ROLE_ICON_CE.get(role, "")
    if role not in job.slots:
        tail = "" if role in required_roles(job.pipeline) else " — optional"
        return f"{ICON_EMPTY} {icon_role} {_esc(role)}{tail}"
    pr = job.probes.get(role)
    state = ICON_WARN if (pr and quality_warning(pr)) else ICON_OK_CE
    detail = f" · {_compact(pr)}" if pr else ""
    return f"{state} {icon_role} {_esc(role)}{detail}"


def _details_block(chat_id: int, job: Job) -> str:
    """The expandable blockquote: full measurements and any warnings.

    Collapsed by default so the screen stays scannable, but one tap away.
    Measuring on arrival is the point of spec section 4.3, and the bitrate
    figure must not become unreachable in the name of tidiness.
    """
    lines = [_esc(" → ".join(PIPELINES[job.pipeline]))]
    for role in sorted(job.slots):
        pr = job.probes.get(role)
        if pr is None:
            continue
        lines += ["", f"<b>{_esc(role)}</b> · {_esc(job.slots[role].name)}",
                  _esc(describe(pr))]
        # Acceptance A6 compares what arrived against the delivered file, so
        # the arrival digest has to be in the transcript at the moment money is
        # committed. It used to be its own message per upload — three loose
        # lines of hex in the middle of the flow, and the first thing the user
        # called "một loạt text khó hiểu" (2026-09-01). Here it is one tap away
        # and, unlike a message, it is inside what _freeze_panel preserves.
        fidelity = (_FIDELITY.get(chat_id) or {}).get(str(job.slots[role]))
        if fidelity:
            lines.append(_esc(fidelity))
        # The PLAIN form inside the block, while the panel shows the laid-out
        # one outside it. Not a technical limit — measured 2026-09-01 that
        # <pre> nests inside <blockquote expandable> without complaint — but
        # the same table twice on one screen is noise, and this copy exists so
        # the reason survives when the collapsed block is read on its own.
        warning = quality_warning(pr)
        if warning:
            lines.append(f"{ICON_WARN} {_esc(warning)}")
    lines += ["", f"manifest: {_esc(_active_manifest_path(chat_id).name)}"]
    return "<blockquote expandable>" + "\n".join(lines) + "</blockquote>"


def _fix_buttons(job: Job) -> list[list[tuple[str, str]]]:
    """Replace one slot of the job on screen — ONE row, icon only.

    Icon rather than name (2026-09-01): the panel's keyboard has to stay a
    fixed height as the batch grows, and four named buttons two per row cost
    two of the four rows available. The icons are the ones already printed
    beside each role name a few lines above, so the mapping is on screen.
    """
    # Text can't be empty (Bot API rejects it), and icon_custom_emoji_id is a
    # PREFIX to text, not a replacement — a literal "⚙️" here duplicated the
    # gear (⚙️⚙️ on screen) once the icon field was wired in (2026-09-12).
    labels = [(" ", _CB_PIPE_ASK, _ce_id(ICON_ASK_CE))]
    # Only offered when the open pipeline actually runs a try-on stage —
    # motion-enhance/character-swap(-enhance) have nothing for a provider to
    # change, and a button that opens an empty chooser is worse than no button.
    if _tryon_stage(job.pipeline) is not None:
        labels.append(("☁️" if job.provider != DEFAULT_PROVIDER else "🖥",
                       _CB_PROVIDER_ASK))
    for role in sorted(job.slots):
        # driver is the one role whose CE differs from its plain glyph (a
        # TikTok logo, not a generic clapper — see ROLE_ICON_CE above), so
        # its redo button gets that icon via icon_custom_emoji_id instead of
        # spelling both glyphs out in plain text. The others have no CE id,
        # so their buttons are unchanged.
        if role == "driver":
            labels.append(("🔁", _CB_REDO + role, _ce_id(ROLE_ICON_CE["driver"])))
        else:
            labels.append((f"🔁{ROLE_ICON.get(role, '')}", _CB_REDO + role))
    return [labels]


# ----------------------------------------------------------------- the panel
#
# ONE message per chat holds the whole state of the job being assembled, and
# every change re-edits it instead of sending a new message (2026-09-01, on the
# user's instruction after a screenshot of the old behaviour: "UI hiện tại vẫn
# đang quá xấu, không được trực quan"). Before this, each step — a file
# accepted, a slot answered, a pipeline switched, the manifest re-shown — was
# its own message, so assembling a three-file job left eight fragments and the
# only way to see the current state was to scroll or type /job.
#
# The rule that makes it safe: the panel is frozen, never deleted, at the
# moment money is committed. The invariant this file works to is that nothing
# may spend $0.99/hour without the exact inputs it spent on being in the
# transcript, and an edited message keeps only its latest version — so
# _freeze_panel stops editing it and strips its keyboard, leaving the submitted
# job permanently in the chat above the progress message.

_PANEL: dict[int, int] = {}          # chat_id -> message_id of the live panel
_PANEL_NOTE: dict[int, str] = {}     # chat_id -> one line about the last change
_LAST_SEEN: dict[int, int] = {}      # chat_id -> id of the newest message seen

# How many messages may sit between the panel and the bottom of the chat before
# it is moved rather than edited. In a private chat message ids increment by
# one per message, so `newest - panel` IS the drift in messages — no guessing.
# Editing is preferred (it is silent and keeps one message), but a panel that
# has scrolled off the screen is a panel the user cannot see, which is the
# problem this whole thing exists to fix.
_PANEL_DRIFT_MAX = 3


def _panel_next_line(chat_id: int, job: Job) -> str:
    """The one line that says what to do now — the bar plus its caption."""
    required = sorted(required_roles(job.pipeline))
    # Required roles only: an unfilled OPTIONAL slot must not make the bar look
    # unfinished, because nothing is waiting on it.
    filled = sum(1 for r in required if r in job.slots)
    bar = "▰" * filled + "▱" * (len(required) - filled)
    head = f"{bar} {filled}/{len(required)}"
    missing = sorted(missing_slots(job))
    if missing:
        return f"{head} · send the <b>{_esc(missing[0])}</b> as a File"
    verdict = _LAST_VALIDATE.get(chat_id)
    if verdict is True:
        queued = _jobs_for(chat_id)
        minutes = sum(estimate_minutes(j) for j in queued)
        head = f"{head} · {len(queued)} job(s)" if len(queued) > 1 else head
        # The caveat travels with the number, because estimate_minutes' own
        # contract says it must: "The caller must put this next to a caveat
        # (measured once, on one batch) — this function only computes the
        # number". Carried here verbatim when _manifest_summary was folded into
        # the panel (2026-09-01) rather than dropped as clutter.
        return (f"{head} · ready · ⏱ ~{minutes} min · {_panel_cost_str()}"
                "\n<i>estimate measured once on one batch — not a promise</i>")
    if verdict is False:
        return f"{head} · {ICON_WARN} did not pass <code>batch-validate</code>"
    return f"{head} · not checked yet — see the message above"


def _flow(pipeline: str) -> str:
    """A pipeline as its stages — what the run does, rather than its name.

    `tryon → character-swap → enhance` instead of
    `tryon-character-swap-enhance`: the same length, but the arrows say it is a
    sequence, which is the thing actually being chosen.
    """
    return _esc(" → ".join(PIPELINES[pipeline]))


def _provider_tag(job: Job) -> str:
    """" · ☁️ gemini" when the try-on stage is running off the default —
    empty for the common case, so a panel with nothing switched stays exactly
    as it read before /provider existed."""
    if job.provider == DEFAULT_PROVIDER or _tryon_stage(job.pipeline) is None:
        return ""
    return f" · ☁️ {_esc(job.provider)}"


def _panel_text(chat_id: int, job: Job, *, with_pictures: bool = True) -> str:
    """The panel body. Same vocabulary as the old review screen, one message."""
    basket = _BASKET.get(chat_id) or []
    queued = _jobs_for(chat_id)
    lines: list[str] = []

    if basket:
        # The batch first, because it is what the money is mostly for: one pod,
        # N videos.
        #
        # FILENAMES are deliberately not here (2026-09-01). Everything arrives
        # from a phone, so they are all IMG_6781, IMG_6783 — the line cost a
        # whole row and said nothing. The picture above identifies the
        # material; what a picture cannot show is the flow, so that is what the
        # text carries, one line per job. Stems come back only when there is no
        # picture to identify anything by.
        lines.append(f"🎬 <b>{len(queued)} job(s)</b> · one pod")
        for index, other in enumerate(basket):
            line = f"{_row_mark(index)} {_flow(other.pipeline)}{_provider_tag(other)}"
            if not with_pictures:
                line += "  <i>" + _esc(", ".join(Path(other.slots[role]).stem
                                                 for role in sorted(other.slots))) + "</i>"
            lines.append(line)
        if len(queued) > len(basket):
            # The job being edited is the LAST row of the sheet, so it takes
            # the next square. Without this the picture has one more coloured
            # bar than the list has squares and the mapping silently breaks.
            lines.append(f"▸{_row_mark(len(basket))} {_flow(job.pipeline)}"
                         f"{_provider_tag(job)}  <i>← open</i>")
        lines.append("")
    else:
        lines += [f"🎬 <b>{_esc(job.pipeline)}</b>{_provider_tag(job)}", ""]

    for role in sorted(required_roles(job.pipeline) | optional_roles(job.pipeline)):
        lines.append(_role_line(role, job))
    lines += ["", _panel_next_line(chat_id, job)]

    if basket and _STATE.get(chat_id) is not None and not missing_slots(job) \
            and any(_signature(job) == _signature(o) for o in basket):
        # Named, not silently dropped. After "add another" every slot is kept
        # so one can be swapped, which means this job IS entry N until the user
        # changes something — and running it would pay twice for one video.
        lines.append("<i>same as an entry above — change something, or just Run</i>")

    pending = _PENDING.get(chat_id) or []
    if pending:
        lines += ["", f"⏳ waiting for a label: "
                      f"{_esc(', '.join(q[0].name for q in pending))}"]
    # Repeated OUTSIDE the collapsed block, as the review screen already did:
    # this is the last thing read before $0.99/hour is committed, and anything
    # that needs a tap to reveal is something that gets skipped.
    for role in sorted(job.slots):
        pr = job.probes.get(role)
        # _html, and NOT passed through _esc: it returns markup on purpose.
        warning = quality_warning_html(pr) if pr else ""
        if warning:
            lines += ["", f"{ICON_WARN} <b>{_esc(role)}</b> — {warning}"]

    note = _PANEL_NOTE.get(chat_id)
    if note:
        # The acknowledgement that used to be its own message. Kept in the
        # panel so "replacing the previous outfit" is still named — losing that
        # was the risk of collapsing the per-step messages (finding I6/I7).
        lines += ["", f"<i>{_esc(note)}</i>"]
    if job.slots:
        lines += ["", _details_block(chat_id, job)]
    return "\n".join(lines)


def _panel_buttons(chat_id: int, job: Job) -> list[list[tuple[str, str]]]:
    """Run only when there is genuinely something safe to run.

    Gated on `_LAST_VALIDATE is True` as well as completeness — stricter than
    the old review screen, which showed Run next to a manifest that had just
    failed validation and relied on _do_confirm refusing the tap afterwards.
    A button that cannot work should not be offered.
    """
    rows: list[list[tuple[str, str]]] = []
    queued = _jobs_for(chat_id)
    basket = _BASKET.get(chat_id) or []
    if queued and not (_PENDING.get(chat_id) or []) and _LAST_VALIDATE.get(chat_id) is True:
        price = _panel_price()
        # No leading ▶️/➕ in the text: icon_custom_emoji_id already prefixes
        # the same glyph, and keeping both duplicated it on screen (▶️▶️ Run,
        # ➕➕ Add) — same bug as the settings gear above (2026-09-12).
        run = [(f"Run {len(queued)} · ${price:.2f}/h" if len(queued) > 1
                else f"Run · ${price:.2f}/h", _CB_RUN_ASK, _ce_id(ICON_RUN_CE))]
        # Offered beside Run, not instead of it: one pod runs everything in the
        # manifest, so adding another job costs nothing but the render itself.
        if not missing_slots(job):
            run.append(("Add", _CB_ADD, _ce_id(ICON_ADD_CE)))
        rows.append(run)

    # ONE row of coloured squares, whatever the batch size. The first version
    # gave every job its own row of two buttons, which the user measured as
    # nine rows and seventeen buttons for six jobs: "menu dài hơn nữa à, làm
    # vậy không được quá dài". Tapping a square opens that job, and the slot
    # and delete buttons below then act on whatever is open — so the keyboard
    # is four rows for one job and four rows for eight.
    if len(queued) > 1:
        squares = [(_row_mark(index), _CB_JOB_EDIT + _job_digest(other))
                   for index, other in enumerate(basket)]
        if len(queued) > len(basket):
            # The job being edited is always the last row of the sheet. Marked
            # rather than omitted, so the squares in the keyboard and the bars
            # in the picture stay one-to-one.
            squares.append((f"▸{_row_mark(len(basket))}", _CB_JOB_OPEN))
        rows.append(squares)

    rows += _fix_buttons(job)

    trash_icon = _ce_id(ICON_TRASH_CE)
    trash: list[tuple[str, str, str]] = []
    if len(queued) > 1:
        trash.append(("this job", _CB_JOB_HERE, trash_icon))
    if job.slots or basket:
        trash.append(("all", _CB_CLEAR_ASK, trash_icon))
    if trash:
        rows.append(trash)
    return rows


def _preview_dir(chat_id: int) -> Path:
    """Throwaway JPEGs, deliberately NOT under the staging dir.

    `_clear_job` counts `staged.rglob("*")` to tell the user how many files it
    deleted, and previews living there would inflate a number the user checks
    against what they sent. Both directories are removed on /clear.
    """
    return ROOT / "batch" / "tg-preview" / str(chat_id)


# Byte count and sha256 of each accepted file, keyed by chat then by staged
# path. Recorded when the file ARRIVES rather than when it lands in a slot,
# because an ambiguous image is queued first and answered later — keying it on
# the role would lose the digest of anything that waited.
_FIDELITY: dict[int, dict[str, str]] = {}


# Jobs already assembled and waiting for the next pod. The whole point of
# renting by the hour: provisioning and bootstrap are paid once per DRAIN, not
# once per job, so a batch of four amortises them four ways. The user's words
# (2026-09-01): "tận dụng tối đa thời gian thuê gpu tránh chờ gpu khởi động tốn
# thời gian chờ và tiền trong lúc chờ nữa".
#
# This is spec section 5's basket. The runner, the journal and final_files were
# already multi-run — the bot's manifest writer was the only thing pinning it
# to one, and their own hand-written manifests have had 2-6 runs all along.
_BASKET: dict[int, list[Job]] = {}


def _copy_job(job: Job) -> Job:
    """A detached copy. The basket must not alias the job still being edited.

    Job holds plain dicts, so appending the live object and carrying on editing
    it would silently rewrite an entry the user already committed to the batch.
    """
    return Job(pipeline=job.pipeline, slots=dict(job.slots),
               probes=dict(job.probes), provider=job.provider)


def _signature(job: Job) -> tuple:
    """What makes two runs the same run — pipeline, material, and provider.

    Provider is part of the identity, not just a cosmetic setting: same
    material through gemini vs qwen is two different runs (different API,
    different cost, possibly different output) — collapsing them into "the
    same job" would make _job_digest collide and an edit/drop tap on one
    basket row silently act on the other.
    """
    return (job.pipeline, job.provider,
            tuple(sorted((r, str(p)) for r, p in job.slots.items())))


def _job_digest(job: Job) -> str:
    """A short, stable handle for one queued job, for callback_data.

    Keyed on the job's own material rather than its position in the basket. An
    index would be a stale-button hazard: the keyboard on an older panel still
    works — Telegram never expires one — so `bj:d:2` tapped after the batch has
    changed would delete whatever is second NOW. A digest simply fails to match
    and says so, which is the same reasoning as _run_token's staleness guard on
    the money button.
    """
    return hashlib.sha256(repr(_signature(job)).encode()).hexdigest()[:10]


def _find_in_batch(chat_id: int, digest: str) -> int | None:
    basket = _BASKET.get(chat_id) or []
    return next((i for i, job in enumerate(basket)
                 if _job_digest(job) == digest), None)


def _edit_from_batch(tg: Tg, chat_id: int, digest: str) -> None:
    """Pull a committed job back out for editing. It stays queued throughout.

    No "add it back" step, because `_jobs_for` is the basket PLUS the job being
    edited — so the moment it leaves the basket it is already counted again.
    What changes is only which row of the sheet it occupies: the edited job is
    always the last one, so its colour moves. That is visible rather than
    hidden, which is the right way round.
    """
    index = _find_in_batch(chat_id, digest)
    if index is None:
        tg.send_message(chat_id, "that entry is no longer in the batch — the "
                                 "button was from an older version of this panel")
        return
    current = _STATE.get(chat_id)
    if current is not None and missing_slots(current):
        # Refuse rather than discard: a half-built job is work already done and
        # nothing else would recover it.
        tg.send_message(chat_id, "finish or /clear the job you are building "
                                 "first — otherwise it would be lost")
        return
    basket = _BASKET[chat_id]
    picked = basket.pop(index)
    if current is not None and not any(_signature(current) == _signature(other)
                                       for other in basket):
        basket.append(_copy_job(current))
    _STATE[chat_id] = picked
    _LAST_VALIDATE.pop(chat_id, None)
    _render_and_validate(tg, chat_id)
    _show_panel(tg, chat_id, note="editing this one — it is still in the batch")


def _drop_current(tg: Tg, chat_id: int) -> None:
    """Remove the job on screen from the batch, and open the next one.

    Something has to stay on screen afterwards or the panel would show an empty
    job while the batch still has entries, which reads as "everything is gone".
    So the last committed job is pulled back out to take its place.
    """
    job = _STATE.get(chat_id)
    basket = _BASKET.get(chat_id) or []
    if job is None and not basket:
        tg.send_message(chat_id, "nothing to remove")
        return
    name = run_id_for(job) if job is not None else "that job"
    _STATE.pop(chat_id, None)
    _PENDING.pop(chat_id, None)
    if basket:
        _STATE[chat_id] = basket.pop()
        if not basket:
            _BASKET.pop(chat_id, None)
    _LAST_VALIDATE.pop(chat_id, None)
    if _jobs_for(chat_id):
        _render_and_validate(tg, chat_id)
    # Files are NOT deleted here, for the same reason as _drop_from_batch: the
    # material is shared with every other run in the batch.
    _show_panel(tg, chat_id, note=f"removed {_esc(name)} from the batch")


def _drop_from_batch(tg: Tg, chat_id: int, digest: str) -> None:
    """Remove one job from the batch. The staged files stay — other jobs use them."""
    index = _find_in_batch(chat_id, digest)
    if index is None:
        tg.send_message(chat_id, "that entry is no longer in the batch — the "
                                 "button was from an older version of this panel")
        return
    dropped = _BASKET[chat_id].pop(index)
    if not _BASKET[chat_id]:
        _BASKET.pop(chat_id, None)
    _LAST_VALIDATE.pop(chat_id, None)
    if _jobs_for(chat_id):
        _render_and_validate(tg, chat_id)
    # Deliberately does NOT delete the staged files: material is shared across
    # a batch by design, so removing one run must not take the character every
    # other run points at. /clear is the thing that deletes files.
    _show_panel(tg, chat_id,
                note=f"removed {_esc(run_id_for(dropped))} from the batch")


def _jobs_for(chat_id: int) -> list[Job]:
    """Everything Run would submit, in order.

    The job still being assembled joins the basket only when it is complete AND
    differs from every entry already there. After "add another" the panel keeps
    every slot — the user asked for that, so they can replace just the one thing
    they want changed — which means it is briefly an exact duplicate. Running it
    would pay twice for one video.
    """
    jobs = list(_BASKET.get(chat_id) or [])
    current = _STATE.get(chat_id)
    if current is None or missing_slots(current):
        return jobs
    if any(_signature(current) == _signature(other) for other in jobs):
        return jobs
    return jobs + [current]


# Panels that are a PHOTO rather than a text message. A ready job becomes one
# message — the strip of material with the whole panel as its caption — and
# editMessageText cannot touch a photo while editMessageCaption cannot touch
# text, so the bot has to know which kind it is holding.
_PANEL_IS_PHOTO: set[int] = set()

# The built strip, kept per material set so a redraw does not re-run ffmpeg.
_STRIP: dict[int, tuple] = {}

# Telegram caps a caption at 1024 characters — of the PARSED text, not the raw
# HTML. Measured 2026-09-01, and the distinction decided the design: counting
# the markup gave 1,060 for a three-slot panel and the wrong conclusion that a
# merge was impossible, when the visible text is 930 with FOUR slots and
# Telegram accepts it. Names come from the user's own files, so the margin is
# not guaranteed — _caption_fits is checked every time rather than assumed.
CAPTION_LIMIT = 1024


def _visible_length(markup: str) -> int:
    """Characters Telegram will count once the HTML has been parsed away."""
    text = re.sub(r"<[^>]+>", "", markup)
    for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&")):
        text = text.replace(entity, char)
    return len(text)


def _caption_fits(markup: str) -> bool:
    return _visible_length(markup) <= CAPTION_LIMIT


# The material an album was last sent for. Keyed on the slot->path mapping, so
# replacing one file sends a fresh album and re-uploading the same job does not.
_ALBUM_KEY: dict[int, tuple] = {}


def _material_key(job: Job) -> tuple:
    return tuple(sorted((r, str(pth)) for r, pth in job.slots.items()))


def _sheet_key(chat_id: int) -> tuple:
    """Every queued job's material, so the sheet rebuilds when ANY of them moves."""
    return tuple(_material_key(job) for job in _jobs_for(chat_id))


def _sheet_columns(jobs: list[Job]) -> list[str]:
    """One column per role any queued job uses, in the panel's own order.

    Sorted, and the UNION rather than one job's slots: a batch where job 1 has
    a background and job 2 does not still needs four columns, or job 2's cells
    would shift left and the sheet would stop being readable down a column.
    """
    return sorted({role for job in jobs for role in job.slots})


def _sheet_for(chat_id: int) -> Path | None:
    """The contact sheet of everything Run would submit — one row per job.

    Memoised on the material of every queued job: `_show_panel` runs on each
    change and a six-job sheet is twenty-four ffmpeg invocations. That is under
    a second, but not worth repeating for a redraw that changed a word.
    """
    jobs = _jobs_for(chat_id)
    if not jobs or _LAST_VALIDATE.get(chat_id) is not True:
        return None
    key = _sheet_key(chat_id)
    cached = _STRIP.get(chat_id)
    if cached is not None and cached[0] == key and cached[1].exists():
        return cached[1]
    columns = _sheet_columns(jobs)
    rows = [[(job.slots[role],
              bool(job.probes.get(role) and job.probes[role].kind == "video"))
             if role in job.slots else None
             for role in columns]
            for job in jobs]
    shot = sheet(rows, into=_preview_dir(chat_id))
    if shot is None:
        return None
    _STRIP[chat_id] = (key, shot)
    return shot


def _sheet_caption(chat_id: int) -> str:
    """The legend for the sheet when it cannot carry the whole panel.

    Names the columns, because nothing is drawn onto the image — `drawtext` is
    not compiled into the ffmpeg this runs against — using the same role icons
    the panel does, which the reader already knows.
    """
    jobs = _jobs_for(chat_id)
    columns = " · ".join(f"{ROLE_ICON_CE.get(r, '')} {_esc(r)}"
                         for r in _sheet_columns(jobs))
    return f"{len(jobs)} job(s) · {columns}" if len(jobs) > 1 else columns


def _show_panel(tg: Tg, chat_id: int, *, note: str = "",
                bump: bool = False) -> None:
    """Render the panel: edit it in place, or move it back to the bottom.

    Once the job is ready to run the panel becomes ONE message — the strip of
    material carrying the whole panel as its caption, Run button and all —
    rather than a picture followed by a separate panel. The user asked for the
    merge after seeing the two ("1 lần xác nhận là gửi 2 tin như này luôn à"),
    and they were also visibly redundant: the caption repeated the role list
    and the warning that the panel below restated.

    A merge had been ruled out earlier on a bad measurement. The 1024-character
    caption cap applies to the PARSED text, not the raw markup: counting the
    HTML gave 1,060 for three slots and the wrong answer, while the visible
    text is 930 with four. It is still checked per render rather than assumed —
    filenames come from the user's own files and nothing bounds them — and a
    panel that does not fit falls back to the two-message shape instead of
    being truncated.

    `bump` forces a move — used by /job, where the user has explicitly asked to
    see the thing now and a silent edit somewhere above would look like the
    command did nothing at all.
    """
    job = _STATE.get(chat_id)
    if job is None:
        _drop_panel(tg, chat_id)
        return
    if note:
        _PANEL_NOTE[chat_id] = note
    message_id = _PANEL.get(chat_id)
    was_photo = chat_id in _PANEL_IS_PHOTO

    # The sheet is built BEFORE the text, because the text says less when there
    # is a picture: filenames only come back when nothing else identifies a job.
    shot = _sheet_for(chat_id)
    text = _panel_text(chat_id, job, with_pictures=shot is not None)
    # One render, then gone: left in place, a note about THIS change (e.g.
    # "took X out of outfit") would keep resurfacing on every later redraw
    # that has nothing to do with it — reported 2026-09-02 as a confusing
    # note reappearing next to an unrelated slot question.
    _PANEL_NOTE.pop(chat_id, None)
    buttons = _panel_buttons(chat_id, job)
    key = _sheet_key(chat_id) if shot is not None else None
    merged = shot is not None and _caption_fits(text)

    log(f"panel chat={chat_id} pipeline={job.pipeline} slots={sorted(job.slots)} "
        f"id={message_id} photo={was_photo} strip={shot is not None} "
        f"merged={merged} caption={_visible_length(text)}")

    if merged:
        # Same picture, same message: only the words changed.
        if (was_photo and message_id is not None and not bump
                and _ALBUM_KEY.get(chat_id) == key):
            if tg.edit_message_caption(chat_id, message_id, text, buttons=buttons,
                                       parse_mode=PARSE_HTML):
                return
        # New material (or no photo panel yet) needs a new message, because a
        # photo cannot be swapped into a text message and its image cannot be
        # replaced by an edit.
        if message_id is not None:
            tg.delete_message(chat_id, message_id)
        try:
            _PANEL[chat_id] = tg.send_photo(chat_id, shot, caption=text,
                                            buttons=buttons, parse_mode=PARSE_HTML)
        except TgError as exc:
            # A rejected upload must not cost the user the panel itself.
            log(f"merged panel failed, falling back to text: {exc}")
            _PANEL_IS_PHOTO.discard(chat_id)
            _PANEL[chat_id] = tg.send_message(chat_id, text, buttons=buttons,
                                              parse_mode=PARSE_HTML)
            return
        _PANEL_IS_PHOTO.add(chat_id)
        _ALBUM_KEY[chat_id] = key
        return

    # Not ready, or too long to be a caption: the picture (if any) goes above a
    # text panel, once per set of material.
    sent_strip = False
    if shot is not None and _ALBUM_KEY.get(chat_id) != key:
        try:
            tg.send_photo(chat_id, shot, caption=_sheet_caption(chat_id),
                          parse_mode=PARSE_HTML)
            _ALBUM_KEY[chat_id] = key
            sent_strip = True
        except TgError as exc:
            log(f"preview strip failed, continuing without it: {exc}")

    drifted = bump or sent_strip or was_photo or (
        message_id is not None
        and _LAST_SEEN.get(chat_id, 0) - message_id > _PANEL_DRIFT_MAX)
    if message_id is not None and not drifted:
        if tg.edit_message(chat_id, message_id, text, buttons=buttons,
                           parse_mode=PARSE_HTML):
            return
        # False means the user deleted it. Fall through and build a new one
        # rather than leaving the chat with no panel at all.
    elif message_id is not None:
        tg.delete_message(chat_id, message_id)
    _PANEL_IS_PHOTO.discard(chat_id)
    _PANEL[chat_id] = tg.send_message(chat_id, text, buttons=buttons,
                                      parse_mode=PARSE_HTML)


def _drop_panel(tg: Tg, chat_id: int) -> None:
    """Remove the panel entirely — only for /clear, where the job is gone."""
    message_id = _PANEL.pop(chat_id, None)
    _PANEL_NOTE.pop(chat_id, None)
    _PANEL_IS_PHOTO.discard(chat_id)
    _STRIP.pop(chat_id, None)
    if message_id is not None:
        tg.delete_message(chat_id, message_id)


def _freeze_panel(tg: Tg, chat_id: int, stamp: str) -> None:
    """Stop editing the panel and strip its keyboard. Called only from _do_confirm.

    Two jobs at once. It preserves the transcript invariant — the panel stops
    changing at the instant money is committed, so what it shows is what was
    submitted — and it removes the Run button from a job that has already been
    handed to a drain, which is a second line of defence behind _run_token's
    staleness check rather than a replacement for it.

    Must run BEFORE `_STATE.pop`: the text is rendered from the job it is
    freezing.
    """
    message_id = _PANEL.pop(chat_id, None)
    _PANEL_NOTE.pop(chat_id, None)
    was_photo = chat_id in _PANEL_IS_PHOTO
    _PANEL_IS_PHOTO.discard(chat_id)
    _STRIP.pop(chat_id, None)
    job = _STATE.get(chat_id)
    if message_id is None or job is None:
        return
    frozen = _panel_text(chat_id, job) + f"\n\n{ICON_ROCKET_CE} <b>{_esc(stamp)}</b>"
    # A merged panel is a photo, and editMessageText cannot touch one. Getting
    # this wrong would leave the Run button live on a job already handed to a
    # drain — the exact thing the freeze exists to prevent.
    if was_photo:
        tg.edit_message_caption(chat_id, message_id, frozen, parse_mode=PARSE_HTML)
    else:
        tg.edit_message(chat_id, message_id, frozen, parse_mode=PARSE_HTML)


_PROGRESS_SUFFIX = ".progress.json"

def _migrate_progress_path() -> Path:
    """The migration counterpart to _progress_path below, but system-wide
    rather than per-chat: volume_migrate.py's write_progress() writes ONE file
    no matter who is watching, because only one migration can run at a time
    (see migration_running) and this bot only ever serves one allowed user.
    Deliberately a separate file from the migration LEASE: the lease marks "a
    migration is in flight" for the /run picker to refuse a second one, while
    this is volume_migrate.py's phase-by-phase progress, read by
    tick_migration_progress below.

    A function, not a module constant bound at import time (fixed 2026-09-02).
    Its sibling `_migrate_progress_message_path` always resolved ROOT at call
    time, so the two diverged the moment a test reassigned `bot.ROOT` to a
    tempdir: tick_migration_progress then read and DELETED the real repo's
    batch/volume-migrate.progress.json while writing the message id into the
    tempdir. Tests that touch the live repo's migration state are the one
    thing a migration test must not do.
    """
    return ROOT / "batch" / "volume-migrate.progress.json"


def _migrate_progress_message_path(chat_id: int) -> Path:
    """Which message this bot process is keeping edited for a migration in
    progress — mirrors _progress_path's own reasoning (a migration can
    outlive a bot restart) but is its OWN file: a migration is a single
    system-wide operation, not one per chat, and must never be confused with
    a per-chat batch drain's progress file."""
    return ROOT / "batch" / f"tg-{chat_id}.migrate-progress.json"

# The progress message is re-edited on every poll — roughly every 50s, since
# that is what get_updates long-polls for.
#
# A 5-minute throttle was added and then removed on the user's instruction
# (2026-08-31): "nếu là sửa tin nhắn thì cứ để 50s lại đi". An edit sends no
# notification and adds no message to the chat, so there is nothing to be
# spammed by; the throttle was solving a problem that does not exist, and a
# knob that never fires is worse than no knob. The elapsed-minutes line changes
# every minute, so these edits are real rather than the "message is not
# modified" no-ops that edit_message swallows.


_ANIM_PAUSE: dict[int, float] = {}      # chat_id -> time.time() to resume at
# chat_id -> the _run_token tick_phase_a rendered the rent panel for. In memory
# on purpose: after a restart the fallback is the "run try-on first" screen,
# and a resumed Phase A skips every try-on already on disk.
_PHASE_A_OFFERED: dict[int, str] = {}

# While a drain runs the poll drops from a 50s long-poll to 2s, and each spin
# redraws one frame. Measured against the real API on 2026-09-01: 0.48 edits/s
# sustained with zero rejections (also clean at 0.91/s and 2.02/s in shorter
# bursts). 2s rather than 1s because the animation reads the same either way
# and half the calls is half the exposure to a flood limit that is not
# published and can change without notice.
_POLL_IDLE_SEC = 50
_POLL_ANIMATED_SEC = 2


def _progress_path(chat_id: int) -> Path:
    """Which message to keep editing while a drain runs, and with what stages.

    On disk rather than in memory because a drain outlives a bot restart by
    design: `Restart=always` plus a 68-minute job means the process that sent
    the progress message is often not the one that finishes it. Losing the
    message_id would leave a progress message frozen forever at whatever it
    last said, with the real job invisible.
    """
    return ROOT / "batch" / f"tg-{chat_id}{_PROGRESS_SUFFIX}"


def _start_progress(tg: Tg, chat_id: int, manifest_path: Path,
                    stages: list[str], *, phase: str | None = None,
                    sent_tryon: list[str] | None = None,
                    regen: dict | None = None,
                    gpu_provider: str | None = None) -> None:
    """Send the first progress message and record it for later edits.

    `phase` names which tick owns the resulting message: "local" while Phase A
    runs, absent otherwise. Both ticks read this same file and tick_progress
    runs first in the poll loop, so without an owner the tick after Phase A
    exits would find drain_running() False (Phase A writes no lease and
    registers no _RUNNING entry), take tick_progress's "Finished" branch,
    unlink the file and deliver_result — reporting a half-finished batch as
    done and never showing the rent panel.

    `sent_tryon` pre-seeds the previews already in the chat, and `regen`
    records a single-image regeneration — both only from _regen_tryon: a
    regeneration re-runs Phase A on the same manifest, and without the seed
    every OTHER image would be sent a second time.

    `gpu_provider` is "vast" for a Vast rental and records, in the same file, the provider and the
    hourly rate QUOTED on the panel the user tapped (vast_last_quote — an estimate: the offer
    actually rented can differ). tick_progress and /status read both back, so every later render
    prices the run at its own rate instead of RunPod's flat $0.99. Absent for RunPod, so that
    file and its message are exactly what they were.
    """
    billing: dict = {}
    if gpu_provider == "vast":
        quote = vast_last_quote()
        billing = {"gpu_provider": "vast",
                   **({"usd_per_hr": quote.dph} if quote is not None else {})}
    text = progress_text(manifest_path, lease=lease_for(manifest_path),
                         stages=stages, phase=phase, **_billing_kwargs(billing))
    message_id = tg.send_message(chat_id, text, parse_mode=PARSE_HTML)
    _progress_path(chat_id).write_text(json.dumps({
        "manifest": str(manifest_path), "message_id": message_id,
        "stages": stages, "sent_tryon": sorted(sent_tryon or []),
        **({"phase": phase} if phase else {}),
        **({"regen": regen} if regen else {}),
        **billing}, indent=2), encoding="utf-8")


def _billing_kwargs(payload: dict | None) -> dict:
    """progress_text's provider/usd_per_hr, read back from a progress file's payload. Empty for a
    RunPod run (and for a file written before Vast existed), which keeps its old rendering."""
    payload = payload or {}
    out: dict = {}
    if payload.get("gpu_provider"):
        out["provider"] = str(payload["gpu_provider"])
    rate = payload.get("usd_per_hr")
    if isinstance(rate, (int, float)) and not isinstance(rate, bool):
        out["usd_per_hr"] = float(rate)
    return out


def _deliver_tryon_previews(tg: Tg, chat_id: int, manifest_path: Path,
                            payload: dict) -> None:
    """Send a run's try-on/camera-tryon image the moment it finishes — only
    for gemini/qwen-max, on the user's own request (2026-09-12).

    Only those two providers finish this early: batchlib/runner.py's
    run_local_phase runs them from THIS process, before drain.py rents a pod
    at all, so there is a real wait (Phase A's API call, then provision +
    bootstrap) between "try-on is done" and "the rest of the pipeline even
    started" worth showing something for. Self-host (qwen) runs the same
    stage moments before motion, already on the pod paid for — no comparable
    gap to fill, and the final video follows soon after anyway.

    Sent at most once per run: `payload["sent_tryon"]` lives in the same
    on-disk progress file tick_progress already rewrites every tick, so this
    survives both a poll finding nothing new and a bot restart mid-render —
    same reasoning as _progress_path's own docstring.
    """
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError:
        return
    state = load_state(state_path_for(manifest_path))
    runs = state.get("runs") or {}
    sent = set(payload.get("sent_tryon") or [])
    changed = False
    # Regenerate is only offered while the image is still a draft: Phase A's
    # progress message (nothing rented yet) on the chat's own manifest, which
    # is the only one _regen_tryon acts on. Once a drain owns the message the
    # pod is about to read this file, and a button there could only refuse.
    regen_ok = (payload.get("phase") == "local"
                and manifest_path.resolve() == _job_manifest_path(chat_id).resolve())
    token = _run_token(chat_id) if regen_ok else ""
    for index, run in enumerate(manifest.runs):
        if run.id in sent:
            continue
        stage_name = _tryon_stage(run.pipeline)
        if stage_name is None:
            continue
        params = effective_stage_params(stage_name, run.stage_params.get(stage_name))
        provider = str(params.get("provider") or "").strip()
        if not is_local_provider(provider):
            continue
        stage = ((runs.get(run.id) or {}).get("stages") or {}).get(stage_name) or {}
        if stage.get("status") != "done":
            continue
        image = Path(stage.get("file") or "")
        # is_file(), not just "was it recorded done": _local_tryon_stage's own
        # `dest.is_file()` check (runner.py) is the same defence against a
        # journal that says done while batch-clean (or anything else) removed
        # the file it points to.
        if not image.is_file():
            continue
        tg.send_chat_action(chat_id, "upload_document")
        version = len(_tryon_versions(image)) + 1
        label = f"{run.id} · v{version}" if version > 1 else run.id
        # caption is plain text — send_document has no parse_mode (unlike
        # send_message/edit_message), so no HTML here.
        if regen_ok:
            tg.send_document(
                chat_id, image,
                caption=f"🖼 try-on ({provider}) · {label} — not right? "
                        "Regenerate it before renting the GPU",
                buttons=[[("🔄 Regenerate this image",
                           f"{_CB_TRYON_REGEN}{index}:{token}")]])
        else:
            tg.send_document(
                chat_id, image,
                caption=f"🖼 try-on ({provider}) · {label} — "
                        "pipeline continues on the pod")
        sent.add(run.id)
        changed = True
    if changed:
        payload["sent_tryon"] = sorted(sent)
        _progress_path(chat_id).write_text(json.dumps(payload, indent=2),
                                           encoding="utf-8")


def _tryon_versions(image: Path) -> list[Path]:
    """Earlier versions of one try-on image, oldest first.

    `<stem>.v<N><ext>` beside the live file, kept by _regen_tryon so that a
    regeneration never destroys the image it replaces: each one is a paid API
    call, and the older one is sometimes the better one. The live name is
    untouched, so stage_dest, the journal and /tryon's `*/01-tryon.png` glob
    never see these.
    """
    found = []
    for path in image.parent.glob(f"{image.stem}.v*{image.suffix}"):
        number = path.name[len(image.stem) + 2:len(path.name) - len(image.suffix)]
        if number.isdigit():
            found.append((int(number), path))
    return [path for _, path in sorted(found)]


def _regen_tryon(tg: Tg, chat_id: int, index: str, token: str, *,
                 dry_run: bool) -> None:
    """Redo ONE run's try-on image, leaving every other run's untouched.

    Reached from the 🔄 button under a Phase A preview. The mechanism is the
    one Phase A already trusts for "skip what is done": drop this run's stage
    from the journal and start Phase A again with resume=True, so
    run_local_phase's own local_tryon_reusable check skips every other image
    and only this one is sent to the provider. No second code path that calls
    Gemini, and nothing that could disagree with the runner about which
    images a later resume will reuse.

    The previous image is renamed to `<stem>.v<N><ext>`, not deleted, and is
    put back by _settle_regen if the new call fails — a 429 on a regeneration
    must not leave the batch worse off than before the tap.

    Only before a pod has touched the run. Once a drain is running (or has
    already recorded a later stage), the pod reads or has read this file, and
    a new image would silently not be used.
    """
    manifest_path = _job_manifest_path(chat_id)
    if token != _run_token(chat_id):
        tg.send_message(chat_id, "the job changed since that image was sent, so "
                                 "nothing was regenerated.")
        return
    if dry_run:
        tg.send_message(chat_id, "dry run — regenerating would spend API quota, "
                                 "so nothing ran")
        return
    if drain_running(manifest_path):
        tg.send_message(chat_id, "too late to regenerate — the GPU run has "
                                 "started and uses this image. /status shows it.")
        return
    if phase_a_running(manifest_path):
        tg.send_message(chat_id, "the try-on phase is still running — wait for "
                                 "the \"rent a GPU?\" panel, then tap Regenerate "
                                 "again.")
        return
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        tg.send_message(chat_id, f"could not regenerate — {exc}")
        return
    if not index.isdigit() or int(index) >= len(manifest.runs):
        tg.send_message(chat_id, "that button is from an older version of the "
                                 "bot; send /start for the commands")
        return
    run = manifest.runs[int(index)]
    stage_name = _local_tryon_stage(run)
    if stage_name is None:
        tg.send_message(chat_id, f"{run.id}'s try-on no longer runs over the API "
                                 "(its provider changed), so there is nothing to "
                                 "regenerate here.")
        return

    state_file = state_path_for(manifest_path)
    state = load_state(state_file)
    batch_id = str(state.get("batch") or "")
    entry = (state.get("runs") or {}).get(run.id) or {}
    stages = entry.get("stages") or {}
    recorded = stages.get(stage_name) or {}
    pipeline = PIPELINES[run.pipeline]
    if any(stages.get(later) for later in pipeline[pipeline.index(stage_name) + 1:]):
        tg.send_message(chat_id, f"too late to regenerate — {run.id} has already "
                                 "gone past try-on on the pod, so a new image "
                                 "would not be used.")
        return
    if not batch_id or recorded.get("status") not in ("done", "error"):
        tg.send_message(chat_id, f"{run.id}'s try-on has not run yet — nothing "
                                 "to regenerate.")
        return

    dest = stage_dest(run, ROOT / "out" / batch_id / "runs" / run.id, stage_name)
    backup = None
    if recorded.get("status") == "done" and dest.is_file():
        backup = dest.with_name(
            f"{dest.stem}.v{len(_tryon_versions(dest)) + 1}{dest.suffix}")
        dest.replace(backup)
    regen = {"run": run.id, "stage": stage_name, "dest": str(dest),
             "backup": str(backup) if backup else None,
             "entry": recorded, "run_status": entry.get("status"),
             "run_error": entry.get("error")}
    stages.pop(stage_name, None)
    save_state(state_file, state)

    # Seeded with every OTHER image already previewed, so the re-run sends
    # only the new one. A run whose try-on failed earlier is left out on
    # purpose: resume retries it too, and if it succeeds now it deserves its
    # first preview.
    runs_state = state.get("runs") or {}
    seed = []
    for other in manifest.runs:
        other_stage = _local_tryon_stage(other)
        if other.id == run.id or other_stage is None:
            continue
        other_rec = ((runs_state.get(other.id) or {}).get("stages") or {}) \
            .get(other_stage) or {}
        if other_rec.get("status") == "done":
            seed.append(other.id)

    start_phase_a(manifest_path, resume=True)
    kept = (f" The previous version stays on the box as "
            f"<code>{_esc(backup.name)}</code>." if backup else "")
    tg.send_message(
        chat_id,
        f"🔄 <b>Regenerating the try-on for {_esc(run.id)}</b> — API quota "
        "only, no GPU is rented. The other images are kept as they are."
        f"{kept} I will send the new image, then ask about the GPU again.",
        parse_mode=PARSE_HTML)
    all_stages: list[str] = []
    for other in manifest.runs:
        for stage in PIPELINES[other.pipeline]:
            if stage not in all_stages:
                all_stages.append(stage)
    _start_progress(tg, chat_id, manifest_path, all_stages, phase="local",
                    sent_tryon=seed, regen=regen)


_RETRY_PROVIDERS = {"gemini": "Gemini", "qwen-max": "Qwen"}
_QWEN_MISSING = ("Qwen is not set up on this box: it needs DASHSCOPE_API_KEY "
                 "and QWEN_IMAGE_WORKSPACE in the VPS's .env.")


def _tryon_failure_reason(error: str) -> str:
    """The journal's error string, as something a person can act on (HTML).

    Only the one case seen in practice gets a translation: Gemini's
    IMAGE_SAFETY finishReason (2026-09-16, batch 2026-09-16-1706) arrives as
    300 characters of raw JSON. Everything else is shown as-is: a 429 or a
    missing key already says what it is. "Can pass on a retry" is measured:
    the run blocked on 2026-09-16 went through unchanged on 2026-09-18.
    """
    if "IMAGE_SAFETY" in error or "PROHIBITED_CONTENT" in error:
        return ("Gemini's safety filter blocked the generated image. It judges "
                "each output, so the same inputs can pass on a retry; Qwen uses "
                "a different filter.")
    return f"<code>{_esc(error[:300])}</code>"


def _report_failed_tryons(tg: Tg, chat_id: int, manifest_path: Path) -> None:
    """One message per run whose Phase A try-on failed: why, plus retry buttons.

    Until this existed the only sign in the chat was a ❌ on the progress bar,
    and the reason sat in run.log on the VPS.
    """
    manifest = load_manifest(manifest_path)
    runs = load_state(state_path_for(manifest_path)).get("runs") or {}
    token = _run_token(chat_id)
    qwen_ok = qwen_max_configured(ROOT)
    for index, run in enumerate(manifest.runs):
        stage_name = _local_tryon_stage(run)
        if stage_name is None:
            continue
        entry = runs.get(run.id) or {}
        if (((entry.get("stages") or {}).get(stage_name) or {})
                .get("status") != "error"):
            continue
        provider = effective_stage_params(
            stage_name, run.stage_params.get(stage_name)).get("provider")
        buttons = [(f"🔄 Retry with {label}",
                    f"{_CB_TRYON_RETRY}{index}:{key}:{token}")
                   for key, label in _RETRY_PROVIDERS.items()
                   if key != "qwen-max" or qwen_ok]
        hint = "" if qwen_ok else f"\n{_esc(_QWEN_MISSING)}"
        tg.send_message(
            chat_id,
            f"{ICON_ERROR_CE} <b>Try-on failed</b> for <code>{_esc(run.id)}</code> "
            f"({_esc(str(provider))})\n"
            f"{_tryon_failure_reason(str(entry.get('error') or 'no reason recorded'))}"
            f"{hint}\nRetrying costs API quota only; no GPU is rented.",
            parse_mode=PARSE_HTML, buttons=[buttons])


def _retry_tryon(tg: Tg, chat_id: int, index: str, provider: str, token: str,
                 *, dry_run: bool) -> None:
    """Retry one failed try-on, switching that run alone to `provider` first.

    The switch goes onto the drafted Job as well as the manifest, because
    [Run] re-renders the manifest from the jobs: a manifest-only edit would be
    undone by the next render, and Phase A would call the old provider again.
    Every other run keeps its params, so local_tryon_reusable still skips
    their finished images. The rest is _regen_tryon, unchanged.
    """
    if provider not in _RETRY_PROVIDERS:
        tg.send_message(chat_id, "that button is from an older version of the "
                                 "bot; send /start for the commands")
        return
    manifest_path = _job_manifest_path(chat_id)
    # The guards _regen_tryon applies, repeated here because the provider
    # switch below rewrites the manifest and must not happen when it refuses.
    if token != _run_token(chat_id):
        tg.send_message(chat_id, "the job changed since that message was sent, "
                                 "so nothing was retried.")
        return
    if dry_run:
        tg.send_message(chat_id, "dry run — retrying would spend API quota, "
                                 "so nothing ran")
        return
    if drain_running(manifest_path):
        tg.send_message(chat_id, "too late to retry — the GPU run has started. "
                                 "/status shows it.")
        return
    if phase_a_running(manifest_path):
        tg.send_message(chat_id, "the try-on phase is still running — wait for "
                                 "it to finish, then tap Retry again.")
        return
    if provider == "qwen-max" and not qwen_max_configured(ROOT):
        tg.send_message(chat_id, f"{_QWEN_MISSING} Nothing was retried.")
        return
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        tg.send_message(chat_id, f"could not retry — {exc}")
        return
    if not index.isdigit() or int(index) >= len(manifest.runs):
        tg.send_message(chat_id, "that button is from an older version of the "
                                 "bot; send /start for the commands")
        return
    run = manifest.runs[int(index)]
    stage_name = _local_tryon_stage(run)
    current = (effective_stage_params(stage_name, run.stage_params.get(stage_name))
               .get("provider") if stage_name else None)
    if provider != current:
        jobs = _jobs_for(chat_id)
        if [r.id for r in manifest.runs] != _unique_ids(jobs):
            tg.send_message(chat_id, "the drafted job no longer matches the batch "
                                     "on disk, so nothing was retried.")
            return
        jobs[int(index)].provider = provider
        write_manifest(jobs, manifest_path, now=time.strftime("%Y-%m-%d %H:%M:%S"))
        tg.send_message(chat_id, f"{_esc(run.id)} switched to "
                                 f"{_RETRY_PROVIDERS[provider]} for this retry; "
                                 "the other runs keep their provider.")
        token = _run_token(chat_id)
    _regen_tryon(tg, chat_id, index, token, dry_run=dry_run)


def _settle_regen(tg: Tg, chat_id: int, manifest_path: Path,
                  payload: dict) -> None:
    """After a regeneration's Phase A exits: if the new image did not land,
    put the old one back and say so.

    Phase A exits EXIT_NEEDS_POD even when a run failed (batch_run.py only
    stops early under --fail-fast), so without this a failed regeneration
    would be invisible: no new preview, then the ordinary rent panel, and the
    pod would redo the try-on itself or fail on a missing file. Restoring the
    journal entry and the file means the batch is exactly as it was before
    the tap, and the old preview's button still works for another try.
    """
    regen = payload.get("regen")
    if not regen:
        return
    state_file = state_path_for(manifest_path)
    state = load_state(state_file)
    entry = (state.get("runs") or {}).get(regen["run"])
    now = ((entry or {}).get("stages") or {}).get(regen["stage"]) or {}
    dest = Path(regen["dest"])
    if now.get("status") == "done" and dest.is_file():
        return          # the new preview already went out
    error = (entry or {}).get("error") or "it did not finish"
    backup = Path(regen["backup"]) if regen.get("backup") else None
    if backup is None:
        # Nothing to put back, so the run is simply a failed try-on again, and
        # _report_failed_tryons (called right after this) says so with the
        # reason and the retry buttons. A second message here repeated it.
        return
    if backup is not None and backup.is_file() and entry is not None:
        backup.replace(dest)
        entry.setdefault("stages", {})[regen["stage"]] = regen["entry"]
        entry["status"] = regen.get("run_status") or entry.get("status")
        if regen.get("run_error"):
            entry["error"] = regen["run_error"]
        else:
            entry.pop("error", None)
        save_state(state_file, state)
        kept = ("The previous image is back in place, and it is what the GPU "
                "run will use. Tap 🔄 on it to try again.")
    else:
        kept = "There was no earlier image to fall back to."
    tg.send_message(
        chat_id,
        f"{ICON_WARN} <b>Regenerating {_esc(regen['run'])} failed</b> — "
        f"{_esc(str(error))}\n{kept}",
        parse_mode=PARSE_HTML)


def tick_progress(tg: Tg, chat_id: int) -> None:
    """Re-render the progress message, and deliver the result when it ends.

    Called from main()'s poll loop, which wakes at least every 50s because
    get_updates long-polls for that long — so this costs no timer of its own
    and updates at a cadence that suits a job measured in tens of minutes.

    This is the completion poll `deliver_result` was written to wait for: its
    docstring said "nothing in this bot polls a drain to completion and fires a
    callback when it finishes ... This is the reachable close-the-loop hook
    until a completion poll exists." Until now the user had to remember to ask
    /result, with nothing telling them the job had finished — or failed.
    """
    path = _progress_path(chat_id)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest_path = Path(payload["manifest"])
        message_id = int(payload["message_id"])
        stages = list(payload.get("stages") or [])
    except (ValueError, KeyError, TypeError) as exc:
        # Stop trying rather than raise every 50s forever: the drain itself is
        # unaffected, and /status still works.
        log(f"progress file for chat {chat_id} is unreadable, dropping it: {exc!r}")
        path.unlink(missing_ok=True)
        return

    if payload.get("phase") == "local":
        return      # tick_phase_a owns this message — see _start_progress

    # Every tick, regardless of whether the drain is still running or about
    # to be reported finished below — Phase A can complete while the pod is
    # still being provisioned, minutes before anything else in this function
    # would otherwise say a word about it.
    _deliver_tryon_previews(tg, chat_id, manifest_path, payload)

    hpath = handoff_path(_job_manifest_path(chat_id))
    handoff = read_handoff(hpath)
    if handoff is not None and handoff.status in ("running", "failed") \
            and Path(handoff.manifest).resolve() != manifest_path.resolve():
        # drain.py's chain_or_teardown only ever writes "running" or "failed"
        # AFTER the link this progress message was tracking has finished —
        # "starting" is the transient in-between state, deliberately ignored
        # here rather than switching on a guess that has not resolved yet.
        # Close the finished link out exactly like the ordinary "Finished"
        # tail below, then either continue with what got picked up or say
        # why it didn't — a silent handoff is worse than the wait it saves.
        #
        # hpath is consumed here rather than left for the next drain to
        # overwrite: a chat whose NEXT job never queues anything (an
        # ordinary, unrelated confirm days later) goes straight from
        # claim_mailbox()=None to teardown() without ever touching this
        # file — found live 2026-09-02, replaying this exact branch against
        # a brand new job that had nothing to do with the old handoff.
        hpath.unlink(missing_ok=True)
        final_text = progress_text(manifest_path, lease=lease_for(manifest_path),
                                   stages=stages, **_billing_kwargs(payload))
        tg.edit_message(chat_id, message_id, final_text, parse_mode=PARSE_HTML)
        path.unlink(missing_ok=True)
        _ANIM_PAUSE.pop(chat_id, None)
        deliver_result(tg, chat_id, manifest_path)
        if handoff.status == "running":
            picked_up = Path(handoff.manifest)
            next_manifest = load_manifest(picked_up)
            next_stages: list[str] = []
            for run in next_manifest.runs:
                for stage in PIPELINES[run.pipeline]:
                    if stage not in next_stages:
                        next_stages.append(stage)
            tg.send_message(chat_id,
                            f"{ICON_OK_CE} Finished — automatically continuing with your "
                            "queued job on the same pod, no extra rental.",
                            parse_mode=PARSE_HTML)
            _start_progress(tg, chat_id, picked_up, next_stages)
        else:
            tg.send_message(chat_id,
                            f"{ICON_WARN} Finished, but the job you queued next could not "
                            f"start on the reused pod ({_esc(handoff.reason or 'unknown error')})"
                            f" — the pod was destroyed as usual, nothing extra "
                            f"was billed. Nothing is lost: send /again to reload "
                            f"it, then Run to try it as a fresh rental.",
                            parse_mode=PARSE_HTML)
        return

    running = drain_running(manifest_path)
    # Checked before the throttle below, never after: a cosmetic rate limit
    # must not be able to delay the delivery of a finished render.
    if running and time.time() < _ANIM_PAUSE.get(chat_id, 0.0):
        return

    text = progress_text(manifest_path, lease=lease_for(manifest_path),
                         stages=stages, **_billing_kwargs(payload))
    if running:
        try:
            if not tg.edit_message(chat_id, message_id, text,
                                   parse_mode=PARSE_HTML):
                # The user deleted the progress message. Rebuild it and record
                # the new id, rather than editing into the void for the rest of
                # a 40-minute render.
                payload["message_id"] = tg.send_message(chat_id, text,
                                                        parse_mode=PARSE_HTML)
                path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except TgError as exc:
            # Telegram asked for a pause. Honour its own number when it gives
            # one. Measured 2026-09-01: one edit every 2s for a full 40 minutes
            # — 1,147 consecutive edits to a single message, ZERO rejections —
            # so this branch is not expected to run. It exists anyway because
            # that is one chat on one day against a self-hosted server, the
            # limit is not published and can change without notice, and the
            # cost of being wrong is the bot arguing with flood control for the
            # length of a render the user is paying $0.99/hour for.
            wait = exc.retry_after or 60.0
            _ANIM_PAUSE[chat_id] = time.time() + wait
            log(f"progress edit throttled, pausing the animation {wait:.0f}s: {exc}")
        return

    # Finished — one last edit so the message ends on the truth, then the
    # files. The progress file goes first: if delivery raises, the next tick
    # must not deliver a second copy of everything.
    path.unlink(missing_ok=True)
    _ANIM_PAUSE.pop(chat_id, None)
    tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)
    deliver_result(tg, chat_id, manifest_path)


def tick_phase_a(tg: Tg, chat_id: int, *, dry_run: bool = False) -> None:
    """Turn a finished Phase A into the next thing the user sees.

    Separate from tick_progress rather than folded into it: that function's
    completion branch calls deliver_result, which is right when a drain ends
    and wrong when Phase A ends — Phase A ending with exit 3 means the batch
    is HALF done and the next step is a human decision about renting.

    The latch is `offered` in the progress file, not a module dict: a Phase A
    can outlive a bot restart (systemd Restart=always plus a 12-run batch of
    Gemini calls), and phase_a_exit deliberately keeps answering after the
    handle is reaped. What normally stops the panel being re-sent is the unlink
    every terminal branch below does — the poll loop's cadence keys on this
    file's existence, so a file left behind also leaves that loop spinning at 2s
    forever. The latch covers the one window the unlink cannot: a crash between
    writing it and rendering the panel, which may straddle that same restart.

    `dry_run` is taken because main()'s poll loop passes it to every tick
    (tick_migration_progress takes it too) and is unused because this tick
    launches nothing: the spend decision it renders is carried out later by
    _do_resume, which gets dry_run from _handle_callback's own plumbing.

    Ownership is the `phase` key in that same file, checked both ways: this
    function returns unless the message is marked "local", and tick_progress
    returns if it is. See _start_progress for what goes wrong otherwise. The
    check matters most on the handoff back — after the user taps spend,
    _do_resume rewrites the file with no phase, and phase_a_exit still
    remembers 3, so without this guard the next tick would re-send the rent
    panel over the top of a drain that is already running.
    """
    path = _progress_path(chat_id)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest_path = Path(payload["manifest"])
        message_id = int(payload["message_id"])
        stages = list(payload.get("stages") or [])
    except (ValueError, KeyError, TypeError) as exc:
        log(f"progress file for chat {chat_id} is unreadable, dropping it: {exc!r}")
        path.unlink(missing_ok=True)
        return

    if payload.get("phase") != "local":
        return          # a drain owns this message — tick_progress handles it

    if phase_a_running(manifest_path):
        if not payload.get("seen_running"):
            # Durable, and latched by the first tick that can prove the child
            # existed. "phase == local, not running, phase_a_exit is None" is
            # ALSO true in the window between _start_progress(..., phase="local")
            # and start_phase_a(...), so recovering on that bare triple would
            # unlink the progress file of a Phase A that is about to start.
            # This flag is what separates a restart orphan from a not-yet-
            # started one; see the rc-is-None branch below.
            payload["seen_running"] = True
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Try-on previews are worth sending during Phase A too: they are the
        # one piece of visible progress in a phase that records nothing else
        # until a whole stage finishes.
        _deliver_tryon_previews(tg, chat_id, manifest_path, payload)
        text = progress_text(manifest_path, lease=None, stages=stages, phase="local")
        if time.time() >= _ANIM_PAUSE.get(chat_id, 0.0):
            try:
                tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)
            except TgError as exc:
                wait = exc.retry_after or 60.0
                _ANIM_PAUSE[chat_id] = time.time() + wait
                log(f"phase-A progress edit throttled, pausing {wait:.0f}s: {exc}")
        return

    rc = phase_a_exit(manifest_path)
    if rc is None:
        if not payload.get("seen_running"):
            return      # not started yet, or not ours — see the flag above
        # A restart orphan. _PHASE_A and _PHASE_A_RC are in-memory, so this
        # process can never learn how that child ended, and no other path
        # clears the file: tick_progress returns at its ownership guard, and
        # /kill cannot reach it either — its Phase A branch gates on
        # phase_a_running, which is False for exactly the same reason this
        # branch is running (no handle in this process). Unlink and say so. Deliberately NOT handing
        # ownership back to tick_progress by dropping the `phase` key — that
        # makes its very next tick take the "Finished" branch and deliver_result
        # a half-finished batch, the exact bug the ownership marker exists to
        # prevent. The wording claims only what is known: the BOT restarted,
        # which is certain, not that the child died, which is not — a hand-run
        # bot's Phase A may still be alive, and only the systemd case
        # (Restart=always with the default KillMode=control-group) reliably
        # takes it down with the process.
        path.unlink(missing_ok=True)
        _ANIM_PAUSE.pop(chat_id, None)
        reusable, total = _preserved_tryon(manifest_path)
        kept = (f"{ICON_OK_CE} <b>Try-on {reusable}/{total} finished and is "
                "preserved.</b> Run will skip those instead of calling Gemini "
                "again." if total else
                "Nothing already finished was lost.")
        tg.send_message(
            chat_id,
            f"{ICON_WARN} <b>The bot restarted during the try-on phase</b> and "
            "can no longer see it — the try-on itself may still be running.\n"
            "Nothing was rented and no GPU time was spent.\n"
            f"{kept}",
            parse_mode=PARSE_HTML)
        log(f"chat {chat_id}: cleared a Phase A progress message this process "
            f"holds no handle for ({manifest_path.name})")
        return
    if payload.get("offered"):
        return          # already handed the decision to the user

    payload["offered"] = True
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _ANIM_PAUSE.pop(chat_id, None)
    # The same call the running branch makes, and the last chance to make it:
    # every branch below is terminal, and a try-on recorded done inside THIS
    # tick would otherwise never be previewed at all, because deliver_result
    # sends _final/*.mp4 only.
    try:
        _deliver_tryon_previews(tg, chat_id, manifest_path, payload)
        text = progress_text(manifest_path, lease=None, stages=stages, phase="local")
        tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)
    except TgError as exc:
        # Deliberately swallow-and-fall-through, NOT the pause-and-return shape
        # tick_progress's running branch and this function's OWN running branch
        # (above) use. Those pause because there is a next tick to retry the
        # edit on. This is the terminal tick: rc is already known and will not
        # change, `offered` is already written to disk above, and every branch
        # below does its own path.unlink(...) regardless of whether this edit
        # or preview landed. Returning here instead would drop the terminal
        # action — deliver_result on rc == 0, the rent panel on EXIT_NEEDS_POD,
        # the failure message otherwise — for at least a full poll interval,
        # and for rc == 0 specifically it would also skip delivering the user's
        # finished videos, for no benefit: nothing downstream depends on this
        # particular edit having succeeded (/status recomputes the same text
        # from the same journal on demand). Letting it propagate instead, as
        # this block used to, is the bug the re-review caught: the exception
        # would exit tick_phase_a before the dispatch below ever runs, leaving
        # the progress file (already `offered: true`) stranded and the poll
        # loop's `animating = _progress_path(...).exists()` pinned at 2s forever.
        log(f"phase-A terminal edit/preview failed, continuing to the "
            f"exit-{rc} branch anyway: {exc}")
    # After the previews, before the panel: a regeneration that failed must be
    # rolled back (and said so) before the user is asked to rent against it.
    # Same swallow-and-fall-through as above, for the same terminal-tick reason.
    try:
        _settle_regen(tg, chat_id, manifest_path, payload)
    except (TgError, OSError) as exc:
        log(f"phase-A regen settle failed, continuing to the exit-{rc} "
            f"branch anyway: {exc}")
    # After _settle_regen (a failed regeneration that restored its old image is
    # not a failure any more) and before the panel, so the user sees what
    # failed before being asked to rent. Same swallow-and-fall-through.
    try:
        _report_failed_tryons(tg, chat_id, manifest_path)
    except (TgError, OSError, ManifestError) as exc:
        log(f"phase-A failure notice failed, continuing to the exit-{rc} "
            f"branch anyway: {exc}")

    if rc == EXIT_NEEDS_POD:
        # Stock is measured HERE, not when [Run] was tapped — that gap is the
        # whole reason Phase A moved. Reusing _offer_run_confirm rather than a
        # second panel: its sold-out branch already drops the spend button
        # instead of leaving it enabled on a promise that can only fail
        # (2026-09-12, reported live: "5090 đã hết mà nút spend vẫn enable").
        #
        # Unlinked BEFORE the render, not after — the natural reading order is
        # the other way, so this is worth spelling out. _offer_run_confirm sends
        # a message and can raise TgError (flood limit, network), and an unlink
        # that sits behind a raise never runs. The stranded file would pin the
        # poll loop at 2s forever via `animating = _progress_path(...).exists()`
        # and freeze the message on "running the try-on" with nothing running —
        # the exact state this branch's unlink exists to prevent, and one no
        # other code path clears. Losing the panel to a TgError is the cheaper
        # failure: /status still reports the batch and /again still reloads it,
        # and the poll loop returns to its 50s idle cadence.
        # Not hoisted above _deliver_tryon_previews either, which looks like the
        # tidier version of the same idea: that call rewrites this very file
        # whenever it sends a preview, so an earlier unlink would be undone and
        # leave the file stranded with `offered` already set.
        path.unlink(missing_ok=True)
        _PHASE_A_OFFERED[chat_id] = _run_token(chat_id)
        _offer_rent_after_phase_a(tg, chat_id)
    elif rc == 0:
        path.unlink(missing_ok=True)
        deliver_result(tg, chat_id, manifest_path)
    else:
        path.unlink(missing_ok=True)
        tg.send_message(
            chat_id,
            f"{ICON_ERROR_CE} <b>The try-on phase failed</b> (exit {rc}) — "
            "nothing was rented and no GPU time was spent.\n"
            f"<code>{_esc(manifest_path.stem)}.phase-a.log</code> on the box has "
            "the detail. Fix it and tap Run again; the try-ons that did finish "
            "are kept.",
            parse_mode=PARSE_HTML)


def _fmt_gb(num_bytes) -> str:
    return f"{num_bytes / 1e9:.1f}GB"


def _fmt_elapsed(seconds: float) -> str:
    """mm:ss, or h:mm past the first hour — same reasoning as run.py's own
    _elapsed: a ticking number, not a spinner, is what proves a multi-minute
    sync is still alive rather than stuck."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h{minutes:02d}m" if hours else f"{minutes}m{secs:02d}s"


def _migrate_sync_detail(payload: dict) -> str:
    """The extra line under "copying data between temp pods…": which batch
    of units is in flight and a real byte count, not just that string sitting
    unchanged for 30+ minutes (2026-09-16: a run to EUR-IS-1 ran long enough
    that "is this stuck?" came up with nothing in the message to answer it).
    Returns "" until volume_migrate.py's sync() has written its first
    background poll tick (see PROGRESS_POLL_SEC there) — the plain phase text
    above already covers that gap.
    """
    parts = []
    batch_index, batch_total = payload.get("batch_index"), payload.get("batch_total")
    units = payload.get("units") or []
    if batch_index and batch_total:
        names = ", ".join(_esc(str(u)) for u in units) if units else "…"
        parts.append(f"batch {batch_index}/{batch_total}: {names}")
    total_bytes, bytes_copied = payload.get("total_bytes"), payload.get("bytes_copied")
    if isinstance(total_bytes, (int, float)) and total_bytes and isinstance(bytes_copied, (int, float)):
        pct = min(100, round(bytes_copied / total_bytes * 100))
        parts.append(f"{_fmt_gb(bytes_copied)}/{_fmt_gb(total_bytes)} ({pct}%)")
    started_at = payload.get("started_at")
    if isinstance(started_at, (int, float)):
        parts.append(f"running {_fmt_elapsed(time.time() - started_at)}")
    return " · ".join(parts)


def tick_migration_progress(tg: Tg, chat_id: int, *, dry_run: bool = False) -> None:
    """Re-render the migration progress message, same shape as tick_progress
    for a drain — one message, edited in place throughout, including its
    final done/failed state ("one last edit so the message ends on the
    truth" — tick_progress's own reasoning applies unchanged here; there is
    no migration equivalent of deliver_result's separate send of actual
    output files, so nothing should be sent as a substitute for editing the
    progress text itself). Called every poll tick alongside tick_progress;
    harmless no-op when no migration is running.

    On a successful "done", also resumes whatever manifest the recovery
    Migrate button (_CB_RECOVER_MIGRATE) left in _migrate_resume_marker() —
    that button is the only writer of that file, and migration_running()'s
    one-at-a-time guard means there is never more than one manifest waiting
    on it. A "failed" migration does NOT resume anything: the pinned
    datacenter never got the volume, so a retry there would just fail again
    the same way the original provision attempt did.
    """
    progress_path = _migrate_progress_path()
    if not progress_path.exists():
        return
    try:
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
        phase = str(payload["phase"])
    except (ValueError, KeyError, TypeError) as exc:
        # Stop trying rather than raise every 50s forever, same as
        # tick_progress does for its own unreadable progress file.
        log(f"migration progress file unreadable, dropping it: {exc!r}")
        progress_path.unlink(missing_ok=True)
        return

    text = {
        "create": f"{ICON_REFRESH_CE} <b>Migrating volume</b> — creating the destination volume…",
        "sync": f"{ICON_REFRESH_CE} <b>Migrating volume</b> — copying data between temp pods…",
        "verify": f"{ICON_REFRESH_CE} <b>Migrating volume</b> — verifying checksums…",
        "done": f"{ICON_OK_CE} Migration done.",
        "failed": f"{ICON_WARN} Migration failed: {_esc(payload.get('reason', 'unknown error'))}",
    }.get(phase, f"{ICON_REFRESH_CE} <b>Migrating volume</b> — {_esc(phase)}")
    if phase == "done" and payload.get("warning"):
        text += f"\n{_esc(payload['warning'])}"
    if phase == "sync":
        detail = _migrate_sync_detail(payload)
        if detail:
            text += f"\n{detail}"

    msg_path = _migrate_progress_message_path(chat_id)
    if msg_path.exists():
        message_id = json.loads(msg_path.read_text(encoding="utf-8"))["message_id"]
        tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)
    else:
        message_id = tg.send_message(chat_id, text, parse_mode=PARSE_HTML)
        msg_path.write_text(json.dumps({"message_id": message_id}), encoding="utf-8")

    if phase in ("done", "failed"):
        progress_path.unlink(missing_ok=True)
        msg_path.unlink(missing_ok=True)
        # The launch marker is this bot's own "a migration is starting" flag
        # (see _start_migration). A migration that has reached done/failed is
        # over whatever the marker says, and leaving it would refuse the next
        # one for the rest of its staleness window.
        _migrate_launch_marker().unlink(missing_ok=True)
        _MIGRATE_PROC.pop(_MIGRATE_PROC_KEY, None)

        resume_marker = _migrate_resume_marker()
        if phase == "done" and resume_marker.exists():
            try:
                stem = json.loads(resume_marker.read_text(encoding="utf-8"))["stem"]
            except (ValueError, KeyError, TypeError) as exc:
                log(f"migrate-resume marker unreadable, dropping it: {exc!r}")
                stem = None
            resume_marker.unlink(missing_ok=True)
            if stem:
                # A Vast rental has no volume to migrate, so this path is RunPod-only —
                # explicit, same reasoning as _RUNPOD_SUFFIX.
                _do_resume(tg, chat_id, ROOT / "batch" / f"{stem}.yaml",
                          dry_run=dry_run, gpu_provider="runpod")
        else:
            resume_marker.unlink(missing_ok=True)


_LAST_SUFFIX = ".last.json"


def _dump_jobs(jobs: list[Job]) -> list[dict]:
    return [{"pipeline": j.pipeline,
             "provider": j.provider,
             "slots": {r: str(v) for r, v in j.slots.items()},
             "probes": {r: asdict(pr) for r, pr in j.probes.items()}}
            for j in jobs]


def _load_jobs(payload: list) -> list[Job]:
    return [Job(pipeline=entry["pipeline"],
                # .get, not entry["provider"]: a basket dumped by a previous
                # version of this bot has no such key, and refusing to load an
                # otherwise good job over a missing cosmetic field is exactly
                # the failure _load_draft's own docstring warns against.
                provider=entry.get("provider", DEFAULT_PROVIDER),
                slots={r: Path(v) for r, v in entry["slots"].items()},
                probes={r: Probe(**d) for r, d in entry["probes"].items()})
            for entry in payload]


def _last_path(chat_id: int) -> Path:
    """The job most recently submitted, kept for /again.

    A separate file from the draft on purpose. _load_draft reads only
    `.draft.json`, so a submitted job can never be rehydrated into _STATE by a
    restart and re-confirmed by accident — the property /confirm's clear exists
    to protect. /again is an explicit request to copy it back.
    """
    return ROOT / "batch" / f"tg-{chat_id}{_LAST_SUFFIX}"


NOTHING_ASSEMBLED = ("📎 <b>Nothing assembled yet.</b>\n"
                     "Send a file as a <b>File</b> and I will measure it.")


def _redo_slot(tg: Tg, chat_id: int, role: str) -> None:
    """Put a filled slot's file back at the head of the queue and re-ask.

    The only previous way to correct a mis-labelled file was to send it again
    so _fill_slot would overwrite the slot — which means re-uploading, and only
    works if the file is still to hand. On 2026-08-31 the actual recovery was
    hand-editing a JSON file on the host.
    """
    job = _STATE.get(chat_id)
    if job is None or role not in job.slots:
        tg.send_message(chat_id, f"nothing is in {role} right now")
        return
    pr = job.probes.get(role)
    if pr is not None and pr.kind == "video":
        # slot_for assigns a video to `driver` structurally, so there is no
        # other role to move it to. Saying so beats re-asking a question that
        # has exactly one possible answer.
        tg.send_message(chat_id, "a video can only be the driver — send a "
                                 "different video as a File to replace it")
        return
    path = job.slots.pop(role)
    job.probes.pop(role, None)
    _LAST_VALIDATE.pop(chat_id, None)
    _CONFIRM_WARNED.discard(chat_id)
    # At the FRONT of the queue: the user asked about this file, so it is the
    # one to ask about, ahead of anything already parked.
    _PENDING.setdefault(chat_id, []).insert(0, (path, pr))
    _show_panel(tg, chat_id, note=f"took {path.name} out of {role}")
    _ask_about(tg, chat_id, pr, job.pipeline, path=path)


def _add_to_batch(tg: Tg, chat_id: int) -> None:
    """Commit the assembled job to the batch and keep its material for the next.

    Every slot and the pipeline stay in place — the user's choice when asked
    what varies between runs ("không cố định — giữ hết, tôi tự thay"), so the
    next job starts from this one and you replace only what you want changed.
    Nothing is re-uploaded.

    The consequence, handled rather than hidden: until something IS changed the
    job on screen is an exact duplicate of the entry just added, so `_jobs_for`
    leaves it out and the panel says why. Running two identical runs would
    render the same video twice and bill for both.
    """
    job = _STATE.get(chat_id)
    if job is None or missing_slots(job):
        tg.send_message(chat_id, "nothing complete to add yet — fill every "
                                 "required slot first")
        return
    basket = _BASKET.setdefault(chat_id, [])
    if any(_signature(job) == _signature(other) for other in basket):
        tg.send_message(chat_id, "that exact job is already in the batch — "
                                 "change a file or the pipeline first")
        return
    basket.append(_copy_job(job))
    # A copy stays behind as the working job, so editing it cannot reach back
    # into the entry just committed.
    _STATE[chat_id] = _copy_job(job)
    # The manifest now has one more run in it; the previous verdict was about a
    # different file.
    _LAST_VALIDATE.pop(chat_id, None)
    _render_and_validate(tg, chat_id)
    _show_panel(tg, chat_id, note=f"added job {len(basket)} to the batch — "
                                  "replace whatever should differ")


def _ask_to_clear(tg: Tg, chat_id: int) -> None:
    """The confirm step for /clear — shared by the command and the button.

    Deleting staged files is not recoverable from here, so neither entry point
    gets to skip the question.
    """
    job = _STATE.get(chat_id)
    n = len(job.slots) if job else 0
    tg.send_message(
        chat_id,
        f"Delete this job and its {n} staged file(s)? The originals in "
        "Telegram are untouched — only the copies here go.",
        buttons=[[("Yes, start over", _CB_CLEAR_GO, _ce_id(ICON_OK_CE)),
                  ("↩️ Keep it", _CB_CLEAR_NO)]])


def _clear_job(tg: Tg, chat_id: int) -> None:
    """Throw away the draft, the queue and the staged files for this chat."""
    if busy(_job_manifest_path(chat_id)):
        # The staged files ARE the running job's inputs — the manifest points
        # straight at them — so deleting them mid-drain breaks a run that is
        # already being paid for.
        #
        # busy(), not drain_running(): an unpaid Phase A reads this same
        # manifest, so it corrupts the same way. The distinction is the point
        # of the two predicates — this guard exists because a child READS the
        # file, not because a pod is billed, and /kill's POD-FORFEIT path
        # stays on drain_running so a try-on phase can never trigger one.
        #
        # WHICH of the two is holding it goes in the message, via _busy_reason:
        # the guard fires identically either way, but naming a drain during an
        # unpaid Phase A tells the user a pod is burning $0.99/hour when none
        # exists.
        tg.send_message(chat_id, f"{_busy_reason(chat_id)} for this job — clearing "
                                 "now would delete the files it is reading. "
                                 "Wait for it, then /clear.")
        return
    staged = ROOT / "batch" / STAGING_DIR_NAME / str(chat_id)
    removed = 0
    if staged.exists():
        removed = sum(1 for f in staged.rglob("*") if f.is_file())
        shutil.rmtree(staged, ignore_errors=True)
    # Not counted in `removed`: previews are the bot's own throwaways, and the
    # number reported is the one the user checks against what they sent.
    shutil.rmtree(_preview_dir(chat_id), ignore_errors=True)
    _STATE.pop(chat_id, None)
    _PENDING.pop(chat_id, None)
    _LAST_VALIDATE.pop(chat_id, None)
    _CONFIRM_WARNED.discard(chat_id)
    _ALBUM_KEY.pop(chat_id, None)
    _FIDELITY.pop(chat_id, None)
    _BASKET.pop(chat_id, None)
    # Replaced in place rather than deleted: /clear means the job never happened, and a
    # panel left behind describing files that are no longer on disk is the
    # stalest thing in the chat. Nothing spent, so nothing to keep a record of.
    # handle()'s finally calls _save_draft, which deletes the draft file itself
    # now that there is no state left to write.
    done = (f"{ICON_TRASH_CE} <b>Cleared.</b> {removed} staged file(s) deleted.\n"
            "Send a file as a <b>File</b> to start again.")
    message_id = _PANEL.pop(chat_id, None)
    _PANEL_NOTE.pop(chat_id, None)
    was_photo = chat_id in _PANEL_IS_PHOTO
    _PANEL_IS_PHOTO.discard(chat_id)
    _STRIP.pop(chat_id, None)
    # Replaced in place, not deleted (2026-09-01). Deleting it left the chat
    # holding only the loose messages around it, which is exactly the debris
    # the panel exists to prevent — and the user photographed that state and
    # reported it as the panel never having appeared. One line where the panel
    # was reads as "this is finished"; a hole reads as a bug.
    replaced = False
    if message_id is not None:
        replaced = (tg.edit_message_caption(chat_id, message_id, done,
                                            parse_mode=PARSE_HTML) if was_photo
                    else tg.edit_message(chat_id, message_id, done,
                                         parse_mode=PARSE_HTML))
    if not replaced:
        tg.send_message(chat_id, done, parse_mode=PARSE_HTML)


def _ask_to_wipe(tg: Tg, chat_id: int) -> None:
    """The confirm step for /wipe — mirrors /clear's, and for a stronger
    reason: this also removes the messages that would tell you what was
    running, or how to /again it.
    """
    n = len(_ledger_for(chat_id))
    tg.send_message(
        chat_id,
        f"Delete all {n} message(s) in this chat — yours and mine — and "
        "clear the job being assembled?\nTelegram will not let anything "
        "older than 48h be removed; those are reported, not silently left.",
        buttons=[[("Yes, wipe it", _CB_WIPE_GO, _ce_id(ICON_OK_CE)),
                  ("↩️ Keep it", _CB_WIPE_NO)]])


def _wipe_chat(tg: Tg, chat_id: int) -> None:
    """Delete every tracked message in this chat and the job being
    assembled, in one action (2026-09-02) — a chat clean enough to restart in.

    Shares /clear's manifest guard rather than repeating it: the staged files
    ARE a running job's inputs, and its progress message is the one thing
    telling the user it is still going, so a live drain refuses the whole
    thing, not only the file half.
    """
    if busy(_job_manifest_path(chat_id)):
        # busy() for the reason _clear_job's guard gives: this is the
        # manifest-is-being-read guard, not the pod-is-billed one, so an
        # unpaid Phase A has to refuse it too — and _busy_reason for the
        # reason given there as well, so the refusal names the right one.
        tg.send_message(chat_id, f"{ICON_ALERT_CE} {_busy_reason(chat_id)} for this "
                                 "chat's job — "
                                 "wiping now would delete the files it is "
                                 "reading, and the message that tells you "
                                 "when it's done. Wait for it, then /wipe.",
                        parse_mode=PARSE_HTML)
        return
    _clear_job(tg, chat_id)
    ids = _ledger_for(chat_id)
    removed = sum(1 for message_id in ids if tg.delete_message(chat_id, message_id))
    failed = len(ids) - removed
    _LEDGER[chat_id] = []
    _ledger_path(chat_id).unlink(missing_ok=True)
    report = f"🧹 <b>Wiped.</b> Deleted {removed} message(s)."
    if failed:
        report += (f" {failed} couldn't be removed — Telegram won't delete "
                   "anything older than 48h.")
    # This message is not itself in `ids` — it is sent, and recorded by
    # _track_sends, only after the sweep above already ran — so it is the
    # one survivor: the clean, empty-feeling chat /wipe promised.
    tg.send_message(chat_id, report, parse_mode=PARSE_HTML)


# The card this repo is built around — docs/gpu-pod.md's own conclusion is
# "giữ 5090 làm chính" (keep the 5090 as the main one). Checked unconditionally,
# NOT read from .env's GPU= (2026-09-02 fix): that value is whatever was last
# hand-picked to actually rent — often a fallback already, per
# docs/gpu-pod.md's own "đổi tay" instructions — so treating it as "the
# primary to check" made /gpu silently drop the 5090 from its own report the
# moment someone was already running on a fallback, which defeats the point
# of checking.
_PRIMARY_GPU_ID = "NVIDIA GeForce RTX 5090"

# Fallback cards, picked by hand in .env — pod-provision.sh dropped
# GPU_FALLBACK on purpose, so /gpu exists to inform that hand-pick rather
# than let it be a guess. RTX 4090 first: docs/gpu-pod.md's own measured
# comparison (10/08/2026) found it only 1.48x slower on the same real job
# (443.75s -> 656s) and confirmed it runs Wan2.2 Animate correctly — a
# measured number, not an assumption. RTX PRO 4500 second: half the 5090's
# price, but its render time has never been measured in this repo, only
# observed as noticeably slower in real use. L40S third: same Ada Lovelace
# die/compute-capability (sm_89) as the RTX 4090 above, so it takes the
# identical, already-working setup path (lib-gpu.sh detects capability via
# nvidia-smi, not by GPU name), only ~$0.10/h above the 5090 (checked live
# 2026-09-06). RTX PRO 6000 Blackwell Server Edition fourth: same
# architecture family as the 5090 itself (Blackwell, cc >= 10), but ~2x its
# price ($2.09/h vs ~$0.99/h) — no Blackwell card sits close to the 5090's
# price, so this is offered for when architecture match matters more than
# staying cheap, not as a budget option.
_FALLBACK_GPU_IDS = ("NVIDIA GeForce RTX 4090", "NVIDIA RTX PRO 4500 Blackwell",
                     "NVIDIA L40S", "NVIDIA RTX PRO 6000 Blackwell Server Edition")

# Every GPU /gpu (and /subscribe, below) knows how to check — the primary
# plus its fallbacks, in the same order /gpu reports them.
_GPU_CATALOG = (_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS)

# callback_data stays short (Bot API caps it at 64 bytes) — a switch button
# carries one of these keys, never the full gpuId string.
_GPU_SHORT = {_PRIMARY_GPU_ID: "5090", "NVIDIA GeForce RTX 4090": "4090",
             "NVIDIA RTX PRO 4500 Blackwell": "pro4500", "NVIDIA L40S": "l40s",
             "NVIDIA RTX PRO 6000 Blackwell Server Edition": "pro6000"}
_GPU_BY_SHORT = {v: k for k, v in _GPU_SHORT.items()}

# Human-facing name for a GPU runpodctl doesn't list at all right now (so
# there is no entries[0].display_name to read one from) — matches the
# "RTX 4090" / "RTX PRO 4500" / "L40S" / "RTX PRO 6000" shape runpodctl's
# own display_name uses for the others, confirmed live 2026-09-06.
_GPU_DISPLAY_SHORT = {_PRIMARY_GPU_ID: "RTX 5090", "NVIDIA GeForce RTX 4090": "RTX 4090",
                     "NVIDIA RTX PRO 4500 Blackwell": "RTX PRO 4500",
                     "NVIDIA L40S": "L40S",
                     "NVIDIA RTX PRO 6000 Blackwell Server Edition": "RTX PRO 6000"}

# runpodctl's own stock words, ranked best-first — used only to sort the
# "other regions" list so the most promising alternative surfaces first.
_STOCK_RANK = {"high": 0, "medium": 1, "low": 2, "none": 3}

# A coloured dot reads faster than the word next to it, on a phone screen
# glanced at before deciding whether to spend $0.99/h. Traffic-light order,
# extended one step for "none". Plain Unicode, deliberately — no matching
# custom emoji square exists in any pack checked 2026-09-04 (see ICON_*_CE
# above), and a plain dot renders identically with or without one.
_STOCK_ICON = {"high": "🟢", "medium": "🟡", "low": "🟠", "none": "🔴"}


def _stock_icon(status: str) -> str:
    """The dot, plus a loud animated flag when there is truly nothing to rent.

    "none" is the one status where a glance at the dot alone risks being read
    as "loading" rather than "empty" — the siren removes that ambiguity.
    """
    dot = _STOCK_ICON.get(status, "⬜")
    return f"{ICON_CRITICAL_CE} {dot}" if status == "none" else dot


def _report_gpu_stock(tg: Tg, chat_id: int, *, message_id: int | None = None,
                      force: bool = False) -> None:
    """Live RunPod stock for the 5090 and its fallbacks, at every datacenter
    that carries them — free, no pod rented.

    The Network Volume's own datacenter is marked and listed first: it is
    the only one a pod can rent in and still mount the volume
    (pod-provision.sh pins --data-center-ids to it). Renting anywhere else
    is a real option (docs/gpu-pod.md's EU-CZ-1 failover) but not an
    instant switch — a fresh pod there has no database and re-downloads
    ~33GB of models until the volume is synced or migrated to it — so
    "other regions" is worded as a fallback with a cost, not a same-speed
    alternative.

    `message_id` edits that message in place instead of sending a new one
    (see _edit_or_send) — set when the 🔄 Refresh button below re-renders
    this same report. `force` bypasses stock_at_cached's 60s TTL for a real
    live recheck, used only by that button.
    """
    volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
    home_dc = volume_datacenter(volume_id)
    wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
    try:
        stock = stock_at(wanted) if force else stock_at_cached(wanted)
    except RuntimeError as exc:
        _edit_or_send(tg, chat_id, message_id, f"couldn't reach runpodctl: {exc}",
                     [[("Refresh", _CB_GPU_REFRESH, _ce_id(ICON_REFRESH_CE))]])
        return

    lines = ["📦 <b>GPU stock</b>"]
    configured = env_get(ROOT / ".env", "GPU")
    if configured:
        lines.append(f"{ICON_NVIDIA_CE} Currently selected: <b>{_esc(configured)}</b>")
    if home_dc:
        lines.append(f"📍 <b>{_esc(home_dc)}</b> (your volume)")
    else:
        lines.append("(volume datacenter unknown — showing every region)")

    other_lines: list[str] = []
    for wanted_id in wanted:
        entries = stock.get(wanted_id)
        if not entries:
            short = _GPU_DISPLAY_SHORT.get(wanted_id, wanted_id)
            lines.append(f"  <b>{_esc(short)}</b>: 🔴 sold out everywhere")
            continue
        price = f"{ICON_MONEY_CE} ${entries[0].price_per_hr:.2f}/h" if entries[0].price_per_hr else "?"
        # None, not "not offered here", when home_dc itself is unknown — that
        # reads as "checked and absent", which is a claim this branch has no
        # basis for.
        home = next((e for e in entries if home_dc and e.datacenter_id == home_dc),
                    None)
        if home is not None:
            icon = _stock_icon(home.stock_status.lower())
            lines.append(f"  {_esc(entries[0].display_name)}: "
                         f"{icon} <b>{_esc(home.stock_status)}</b> · {price}")
        else:
            lines.append(f"  <b>{_esc(entries[0].display_name)}</b> · {price}")

        elsewhere = sorted(
            (e for e in entries if e is not home and e.stock_status.lower() != "none"),
            key=lambda e: _STOCK_RANK.get(e.stock_status.lower(), 9))
        for e in elsewhere[:2]:
            icon = _stock_icon(e.stock_status.lower())
            e_price = f"{ICON_MONEY_CE} ${e.price_per_hr:.2f}/h" if e.price_per_hr else "?"
            other_lines.append(f"  {_esc(e.display_name)} — {_esc(e.datacenter_id)}: "
                               f"{icon} {_esc(e.stock_status)} · {e_price}")

    if other_lines:
        lines.append("\n<b>Other regions</b> (need the volume synced there "
                     f"first, {MIGRATE_DURATION_SHORT} — not an instant switch):")
        lines.extend(other_lines)

    _edit_or_send(tg, chat_id, message_id, "\n".join(lines),
                 [[("Refresh", _CB_GPU_REFRESH, _ce_id(ICON_REFRESH_CE))]], parse_mode=PARSE_HTML)


# Below this many hours of runway, /balance warns before a rent is attempted:
# one motion job runs ~40 minutes on the 5090, plus pod setup, so under an
# hour a single job may not finish before the balance runs dry.
_LOW_RUNWAY_HOURS = 1.0


def _report_balance(tg: Tg, chat_id: int, *, message_id: int | None = None) -> None:
    """/balance — the RunPod prepaid balance, and how many hours of the
    configured GPU it buys. Free, no pod rented.

    Runway uses the same $/h the [Run] button quotes (_panel_price), so the
    two never disagree. It ignores the Network Volume's own monthly charge,
    which is small next to a GPU hour — hence "≈".
    """
    buttons = [[("Refresh", _CB_BALANCE_REFRESH, _ce_id(ICON_REFRESH_CE))]]
    try:
        balance = account_balance()
    except RuntimeError as exc:
        _edit_or_send(tg, chat_id, message_id, f"couldn't reach runpodctl: {exc}", buttons)
        return

    configured = env_get(ROOT / ".env", "GPU") or _PRIMARY_GPU_ID
    gpu_name = _GPU_DISPLAY_SHORT.get(configured, configured)
    price = _panel_price()
    hours = balance / price if price > 0 else 0.0
    lines = [f"{ICON_MONEY_CE} <b>RunPod balance: ${balance:.2f}</b>",
             f"⏱ ≈ {_fmt_elapsed(hours * 3600)} of {_esc(gpu_name)} at ${price:.2f}/h"]
    if hours < _LOW_RUNWAY_HOURS:
        lines.append(f"{ICON_WARN} Under {_LOW_RUNWAY_HOURS:g}h — top up before renting, "
                     "one motion job may not finish.")
    _edit_or_send(tg, chat_id, message_id, "\n".join(lines), buttons, parse_mode=PARSE_HTML)


# One-shot GPU-stock watches: (gpu_id, datacenter_id) pairs a chat asked to
# hear about the moment they stop being sold out. Own file per chat, not
# folded into the draft — same reasoning as _LEDGER's (2026-09-02): a
# subscription has nothing to do with whatever job is or isn't being
# assembled, and _save_draft deletes the draft the instant a job clears.
_GPU_SUBS: dict[int, list[dict]] = {}
_GPU_SUBS_LOADED: set[int] = set()


def _gpu_subs_path(chat_id: int) -> Path:
    return ROOT / "batch" / f"tg-{chat_id}.gpusubs.json"


def _gpu_subs_for(chat_id: int) -> list[dict]:
    """Every (gpu_id, datacenter_id) this chat is watching. Loaded once per
    process per chat, same as `_ledger_for`."""
    if chat_id not in _GPU_SUBS_LOADED:
        _GPU_SUBS_LOADED.add(chat_id)
        path = _gpu_subs_path(chat_id)
        try:
            _GPU_SUBS[chat_id] = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (ValueError, TypeError) as exc:
            log(f"gpu subs for chat {chat_id} unreadable, starting over: {exc!r}")
    return _GPU_SUBS.setdefault(chat_id, [])


def _save_gpu_subs(chat_id: int) -> None:
    path = _gpu_subs_path(chat_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(_GPU_SUBS.get(chat_id) or []), encoding="utf-8")
    tmp.replace(path)


def _offer_gpu_sub_targets(tg: Tg, chat_id: int) -> None:
    """/subscribe's first step — which of the five GPUs /gpu already tracks."""
    tg.send_message(
        chat_id,
        f"{ICON_ASK_CE} <b>Subscribe to GPU stock</b>\nWhich GPU?",
        buttons=[[(_GPU_DISPLAY_SHORT[gpu_id], _CB_GPUSUB_PICK + _GPU_SHORT[gpu_id])]
                 for gpu_id in _GPU_CATALOG],
        parse_mode=PARSE_HTML)


def _home_datacenter() -> str | None:
    """Where the Network Volume lives — the only datacenter a pod can
    actually rent in without migrating first. Same lookup `_report_gpu_stock`
    does, reused here so /subscribe can flag the same caveat."""
    return volume_datacenter(env_get(ROOT / ".env", "POD_VOLUME_ID"))


def _offer_gpu_sub_datacenters(tg: Tg, chat_id: int, message_id: int, short: str) -> None:
    """/subscribe's second step — every datacenter runpodctl lists for that
    GPU right now, dot included, so subscribing to one already in stock is a
    visible (if harmless) choice rather than a silent one.

    Every entry runpodctl reports is offered, not just the home datacenter —
    a GPU that is out of stock at home but in stock elsewhere is exactly the
    case /gpu's own "Other regions" section exists for. But renting anywhere
    other than home needs the volume migrated there first
    (docs/gpu-pod.md's EU-CZ-1 failover, ~15-25 min), so the home datacenter
    is marked and every other one carries that same caveat /gpu already
    states — a user report (2026-09-12) found the un-annotated list
    surprising: "5090 hình như chỉ có trên 2 datacenter thôi mà".

    Asks for `include_unavailable` (2026-09-12, same user's next report): a
    GPU with zero stock at EVERY datacenter is dropped from runpodctl's
    default output entirely, so the plain `stock_at_cached` this bot's other
    call sites use would show nothing to subscribe to at exactly the moment
    subscribing is the point — the user was mid-sentence about it: "5090
    đang hết ở các datacenter nên tôi mới làm tính năng subscribe này".
    /gpu itself does not ask for this — it already has its own "sold out
    everywhere" line for the absent case.
    """
    gpu_id = _GPU_BY_SHORT.get(short)
    if gpu_id is None:
        tg.edit_message(chat_id, message_id,
                        "that button is from an older version of the bot; "
                        "send /subscribe again")
        return
    try:
        stock = stock_at_cached(list(_GPU_CATALOG), include_unavailable=True)
    except RuntimeError as exc:
        tg.edit_message(chat_id, message_id, f"couldn't reach runpodctl: {exc}")
        return
    entries = stock.get(gpu_id) or []
    if not entries:
        tg.edit_message(chat_id, message_id,
                        f"runpodctl doesn't list any datacenter at all for "
                        f"{_esc(_GPU_DISPLAY_SHORT[gpu_id])} right now — try "
                        "/subscribe again later.",
                        parse_mode=PARSE_HTML)
        return
    home_dc = _home_datacenter()
    buttons = []
    for e in entries:
        # _STOCK_ICON's plain dot, NOT _stock_icon() — that wrapper's siren
        # is an HTML <tg-emoji> tag meant for message text, and a button's
        # `text` is plain, unparsed Telegram-side (reported 2026-09-12: it
        # showed up as literal angle-bracket text on every "none" button).
        # icon_custom_emoji_id (Bot API 9.4) is the field that actually lets
        # a BUTTON carry it, added below instead.
        dot = _STOCK_ICON.get(e.stock_status.lower(), "⬜")
        label = f"{dot} {e.datacenter_id}"
        if e.datacenter_id == home_dc:
            label = f"📍 {label}"
        data = f"{_CB_GPUSUB_DC}{short}:{e.datacenter_id}"
        if e.stock_status.lower() == "none":
            buttons.append([(label, data, _ce_id(ICON_CRITICAL_CE))])
        else:
            buttons.append([(label, data)])
    if home_dc is None:
        note = "\n(volume datacenter unknown — showing every region)"
    elif any(e.datacenter_id != home_dc for e in entries):
        note = (f"\n📍 = your volume's home datacenter, rentable now. Any "
                f"other region needs the volume synced there first "
                f"({MIGRATE_DURATION_SHORT}) — not an instant switch.")
    else:
        note = ""
    tg.edit_message(
        chat_id, message_id,
        f"{ICON_ASK_CE} <b>{_esc(_GPU_DISPLAY_SHORT[gpu_id])}</b> — "
        f"which datacenter?{note}",
        buttons=buttons,
        parse_mode=PARSE_HTML)


def _add_gpu_sub(tg: Tg, chat_id: int, message_id: int, short: str, dc: str) -> None:
    """/subscribe's last step — record the pair and confirm in place."""
    gpu_id = _GPU_BY_SHORT.get(short)
    if gpu_id is None:
        tg.edit_message(chat_id, message_id,
                        "that button is from an older version of the bot; "
                        "send /subscribe again")
        return
    subs = _gpu_subs_for(chat_id)
    if any(s["gpu_id"] == gpu_id and s["datacenter_id"] == dc for s in subs):
        tg.edit_message(chat_id, message_id,
                        f"already subscribed to {_esc(_GPU_DISPLAY_SHORT[gpu_id])} "
                        f"@ {_esc(dc)} — see /unsubscribe to remove it.")
        return
    subs.append({"gpu_id": gpu_id, "datacenter_id": dc})
    _save_gpu_subs(chat_id)
    home_dc = _home_datacenter()
    caveat = (f"\n{ICON_WARN} not your volume's home datacenter — renting here "
             f"needs it synced there first ({MIGRATE_DURATION_SHORT})."
             if home_dc and dc != home_dc else "")
    tg.edit_message(
        chat_id, message_id,
        f"🔔 <b>Subscribed.</b> {_esc(_GPU_DISPLAY_SHORT[gpu_id])} @ {_esc(dc)}"
        f"{caveat}\n"
        "You'll get a message here the moment it has stock, checked "
        "automatically — no need to /gpu. Clears itself once it fires.",
        parse_mode=PARSE_HTML)


def _gpu_subs_lines_and_buttons(chat_id: int) -> tuple[str, list]:
    """The /unsubscribe body — shared by the command and the remove callback,
    which redraws the same list rather than resending it."""
    subs = _gpu_subs_for(chat_id)
    if not subs:
        return "no active GPU subscriptions.", []
    lines = ["🔔 <b>Active GPU subscriptions</b>"]
    buttons = []
    for s in subs:
        short = _GPU_DISPLAY_SHORT.get(s["gpu_id"], s["gpu_id"])
        dc = s["datacenter_id"]
        lines.append(f"  {_esc(short)} @ {_esc(dc)}")
        buttons.append([(f"{short} @ {dc}",
                        f"{_CB_GPUSUB_RM}{_GPU_SHORT.get(s['gpu_id'], '')}:{dc}",
                        _ce_id(ICON_TRASH_CE))])
    return "\n".join(lines), buttons


def _list_gpu_subs(tg: Tg, chat_id: int) -> None:
    text, buttons = _gpu_subs_lines_and_buttons(chat_id)
    tg.send_message(chat_id, text, buttons=buttons or None, parse_mode=PARSE_HTML)


def _remove_gpu_sub(tg: Tg, chat_id: int, message_id: int, short: str, dc: str) -> None:
    gpu_id = _GPU_BY_SHORT.get(short)
    subs = _gpu_subs_for(chat_id)
    remaining = [s for s in subs
                if not (s["gpu_id"] == gpu_id and s["datacenter_id"] == dc)]
    if len(remaining) != len(subs):
        _GPU_SUBS[chat_id] = remaining
        _save_gpu_subs(chat_id)
    text, buttons = _gpu_subs_lines_and_buttons(chat_id)
    tg.edit_message(chat_id, message_id, text, buttons=buttons or None,
                    parse_mode=PARSE_HTML)


def _tick_gpu_subs(tg: Tg, chat_id: int) -> None:
    """Fire any subscription whose (gpu, datacenter) is no longer sold out.

    Reuses `stock_at_cached` — the same 60s-TTL cache /gpu itself reads from
    — so a chat with active subscriptions costs no extra `runpodctl` calls
    beyond what the poll loop's own cadence already pays for. One-shot: a
    fired subscription is removed immediately, same tick, on the user's own
    request (2026-09-12) — "báo 1 lần rồi gỡ, giống đặt báo thức 1 lần".
    """
    subs = _gpu_subs_for(chat_id)
    if not subs:
        return
    try:
        stock = stock_at_cached(list(_GPU_CATALOG))
    except RuntimeError as exc:
        log(f"gpu-sub check skipped, will retry next tick: {exc}")
        return
    remaining: list[dict] = []
    fired: list[tuple[dict, object]] = []
    for sub in subs:
        entries = stock.get(sub["gpu_id"]) or []
        hit = next((e for e in entries
                   if e.datacenter_id == sub["datacenter_id"]
                   and e.stock_status.lower() != "none"), None)
        if hit is None:
            remaining.append(sub)
        else:
            fired.append((sub, hit))
    if fired:
        _GPU_SUBS[chat_id] = remaining
        _save_gpu_subs(chat_id)
    for sub, hit in fired:
        short = _GPU_DISPLAY_SHORT.get(sub["gpu_id"], sub["gpu_id"])
        price = f"${hit.price_per_hr:.2f}/h" if hit.price_per_hr else "?"
        tg.send_message(
            chat_id,
            f"🔔 <b>{_esc(short)}</b> is now available at "
            f"<b>{_esc(sub['datacenter_id'])}</b>: "
            f"{_stock_icon(hit.stock_status.lower())} {_esc(hit.stock_status)} · "
            f"{ICON_MONEY_CE} {price}\n"
            "This subscription cleared itself — /subscribe again to re-arm.",
            parse_mode=PARSE_HTML)


def _gpu_price(gpu_id: str, stock: dict) -> float:
    """The configured GPU's own $/h, from the stock check just made —
    never a second runpodctl round trip. $0.99 (the 5090's own price) is
    the fallback for when the check itself failed or the id is unlisted,
    matching what [Run] has always quoted rather than inventing a new
    default.
    """
    entries = stock.get(gpu_id)
    return entries[0].price_per_hr if entries and entries[0].price_per_hr else 0.99


def _panel_cost_str() -> str:
    """Price + live stock for whichever GPU is configured right now, for the
    panel's ready-line (_panel_next_line) and its Run button (_panel_buttons)
    — replacing the flat assumed "$0.99/hour" both used to show with no GPU
    or region attached (2026-09-03: "hiện run nhưng không thấy có thông tin
    nào về gpu nào đang chọn ở vùng nào cùng stock").

    Backed by stock_at_cached (60s TTL) rather than stock_at — the panel
    redraws on nearly every action while a batch is assembled, and a live
    check with no caching would mean a runpodctl round trip per file upload,
    per slot answer, per pipeline switch. Fails open to the old flat
    placeholder on any lookup failure or missing config, exactly like
    _offer_run_confirm: an informational number must never be the reason the
    panel itself breaks.
    """
    configured = env_get(ROOT / ".env", "GPU") or _PRIMARY_GPU_ID
    volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
    home_dc = volume_datacenter(volume_id)
    if not home_dc:
        return "💸 $0.99/hour"
    wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
    try:
        stock = stock_at_cached(wanted)
    except RuntimeError:
        return "💸 $0.99/hour"
    price = _gpu_price(configured, stock)
    entries = stock.get(configured) or []
    home = next((e for e in entries if e.datacenter_id == home_dc), None)
    if home is None:
        return f"💸 ${price:.2f}/hour ({_esc(configured)} not listed at {_esc(home_dc)})"
    icon = _stock_icon(home.stock_status.lower())
    return f"💸 ${price:.2f}/h · {icon} {_esc(home.display_name)} @ {_esc(home_dc)}"


def _panel_price() -> float:
    """Just the $/h number behind _panel_cost_str, for the Run button's label
    — a Telegram inline button can't carry the icon/region detail, and the
    button's price must never drift from what the ready-line above it says.
    """
    configured = env_get(ROOT / ".env", "GPU") or _PRIMARY_GPU_ID
    volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
    if not volume_datacenter(volume_id):
        return 0.99
    wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
    try:
        stock = stock_at_cached(wanted)
    except RuntimeError:
        return 0.99
    return _gpu_price(configured, stock)


def _migrate_lease_path() -> Path:
    """volume_migrate.py's own LEASE_PATH. A function, not a constant, for
    the same reason _migrate_progress_path is one: a test that reassigns
    bot.ROOT must not reach the live repo's migration state."""
    return ROOT / "batch" / "volume-migrate-lease.json"


def _migrate_launch_marker() -> Path:
    """This bot's own "a migration is starting" flag, distinct from the lease.

    volume_migrate.py writes its lease only AFTER create_volume plus two REST
    pod-create round trips have all succeeded — tens of seconds at best,
    minutes when RunPod is slow. `Popen` returns in milliseconds. Everything
    in between is a window where the lease does not exist yet, so
    `migration_running()` answered False and a second tap of "Yes, migrate"
    launched a SECOND migration.

    That is not a cosmetic race. The loser's lease write overwrites the
    winner's, so the winner's two temp pods stop being claimed by anything —
    and its destination VOLUME is never claimed by anything at all, because
    pod_watchdog reconciles pods and has no concept of a volume. An orphaned
    volume is a permanent monthly charge that nothing in this repo will ever
    notice (docs/gpu-pod.md: ~$0.07/GB/month).
    """
    return ROOT / "batch" / "volume-migrate.launching.json"


def _migrate_log_path() -> Path:
    """Where the migration subprocess's stdout/stderr go — beside the
    manifests, exactly as tgbot/run.py's start_drain does for a drain, and
    for both of its reasons: a failed run has to be debuggable afterwards,
    and its output must not interleave into the bot's own journald stream."""
    return ROOT / "batch" / "volume-migrate.log"


def _migrate_resume_marker() -> Path:
    """Which stalled manifest to resume once the CURRENT migration reaches
    "done" — written only by the recovery Migrate button (_CB_RECOVER_MIGRATE
    in _handle_callback), never by the plain /gpu "Other regions" flow, which
    has nothing to resume. Safe against migration_running()'s own one-at-a-
    time guard: only one migration can ever be in flight, so this never has
    to disambiguate which migration a marker belongs to — there is at most
    one. Cleared here, by tick_migration_progress on done/failed, and by the
    _CB_MIGRATE_NO cancel — any of the three is the end of what it was for.
    """
    return ROOT / "batch" / "migrate-resume.json"


# Popen handle for a migration THIS process started, so a finished one stops
# blocking the next. Same shape and same limits as tgbot/run.py's _RUNNING:
# process memory, empty again after a bot restart — which is exactly why the
# marker file below carries a timestamp the restarted process can still read.
_MIGRATE_PROC_KEY = "migration"
_MIGRATE_PROC: dict[str, subprocess.Popen] = {}

# How long the marker alone may hold the gate shut. It has to cover
# create_volume + two REST pod creates (the window before volume_migrate.py's
# own lease appears) and no more: past that, either the lease exists and
# answers for itself, or the launch died and the gate must reopen. Generous
# on purpose — the cost of being too long is "wait a few minutes before
# retrying", the cost of being too short is an orphaned volume nobody bills
# you for out loud.
_MIGRATE_LAUNCH_GRACE_SEC = 300


def _migration_launching() -> bool:
    """True between `_start_migration`'s decision to launch and the lease."""
    marker = _migrate_launch_marker()
    if not marker.exists():
        return False

    proc = _MIGRATE_PROC.get(_MIGRATE_PROC_KEY)
    if proc is not None:
        # .poll() also reaps, so a finished child stops looking alive — an
        # os.kill(pid, 0) check would see a zombie and say yes forever.
        if proc.poll() is None:
            return True
        marker.unlink(missing_ok=True)
        return False

    # No handle: a different bot process wrote this (motion-bot.service is
    # Restart=always). Nothing here can poll that child, so the clock is the
    # only bound available.
    try:
        started_at = float(json.loads(marker.read_text(encoding="utf-8"))["at"])
    except (OSError, ValueError, KeyError, TypeError):
        marker.unlink(missing_ok=True)
        return False
    if time.time() - started_at > _MIGRATE_LAUNCH_GRACE_SEC:
        marker.unlink(missing_ok=True)
        return False
    return True


def migration_running() -> bool:
    """A volume migration currently in flight — checked before offering a
    NEW migration button, and before letting /confirm rent at the OLD
    datacenter mid-copy.

    Two sources, and neither alone is enough — the same OR, for the same
    reason, as tgbot/run.py's drain_running. The lease is the durable record
    once volume_migrate.py has provisioned both temp pods; the launch marker
    covers the window BEFORE that, which is wide enough to lose a volume in
    (see _migrate_launch_marker).
    """
    if read_migrate_lease(_migrate_lease_path()) is not None:
        return True
    return _migration_launching()


def _switch_type_options(configured: str, home_dc: str | None, stock: dict,
                         wanted: list) -> tuple[list, list]:
    """Same-datacenter alternatives to the currently configured GPU.

    Returns (text_lines, buttons): text_lines is the "Switch to:" summary
    printed on the main Choose GPU screen, buttons is the same alternatives
    paired with the _CB_RUN_SWITCH button that flips .env's GPU= to them.
    Shared between _offer_run_confirm (to decide whether the ▸ Switch GPU
    type button even has anything behind it) and _offer_run_switch_menu (the
    submenu that button opens) so neither can drift from the other's idea of
    what's offered.
    """
    lines: list[str] = []
    buttons: list[tuple[str, str]] = []
    for gpu_id in wanted:
        if gpu_id == configured:
            continue
        entries = stock.get(gpu_id) or []
        home = next((e for e in entries if e.datacenter_id == home_dc), None)
        if home is None:
            lines.append(f"  {_esc(gpu_id)}: not offered at {_esc(home_dc)}")
            continue
        icon = _stock_icon(home.stock_status.lower())
        lines.append(f"  {icon} <b>{_esc(home.display_name)}</b> — "
                     f"{_esc(home.stock_status)} · {ICON_MONEY_CE} ${home.price_per_hr:.2f}/h")
        short = _GPU_SHORT.get(gpu_id)
        if short:
            buttons.append((f"🖥 {home.display_name} — {home.stock_status} · "
                            f"${home.price_per_hr:.2f}/h",
                            _CB_RUN_SWITCH + short))
    return lines, buttons


def _migrate_options(stock: dict, wanted: list, home_dc: str | None) -> tuple[list, list]:
    """Every OTHER datacenter each GPU is stocked at, not just the home one
    (2026-09-02) — "5090 is out, why not check other regions" was a fair
    question. Renting there means migrating the Network Volume first
    (~15-25 min, docs/gpu-pod.md), which is why each entry gets its own
    MIGRATE button rather than switching anything directly — a destructive
    operation, so the button only opens _ask_migrate's confirm screen, never
    starts anything itself.

    Returns (text_lines, buttons), same split as _switch_type_options and for
    the same reason: shared by the main screen's summary and
    _offer_run_migrate_menu.
    """
    lines: list[str] = []
    buttons: list[tuple[str, str]] = []
    # Checked once, not per candidate — a second migration racing the first
    # would fight it for the same temp pods, and this can't change mid-loop.
    can_migrate = not migration_running()
    for gpu_id in wanted:
        entries = stock.get(gpu_id) or []
        home = next((e for e in entries if e.datacenter_id == home_dc), None)
        elsewhere = sorted(
            (e for e in entries if e is not home and e.stock_status.lower() != "none"),
            key=lambda e: _STOCK_RANK.get(e.stock_status.lower(), 9))
        for e in elsewhere[:2]:
            icon = _stock_icon(e.stock_status.lower())
            price = f"{ICON_MONEY_CE} ${e.price_per_hr:.2f}/h" if e.price_per_hr else "?"
            lines.append(f"  {icon} {_esc(e.display_name)} — "
                         f"{_esc(e.datacenter_id)}: {_esc(e.stock_status)} · {price}")
            # NOT gated on _GPU_SHORT any more. The payload only needs the
            # DATACENTER — a migration moves the volume and says nothing about
            # which GPU is rented afterwards — and requiring a short code to
            # build a prefix nobody read meant a GPU absent from that table
            # silently offered no migrate button at all.
            if can_migrate:
                buttons.append((f"🛫 {e.display_name} — {e.datacenter_id} "
                                f"(${e.price_per_hr:.2f}/h)",
                                _CB_MIGRATE_ASK + e.datacenter_id))
    return lines, buttons


def _edit_or_send(tg: Tg, chat_id: int, message_id: int | None, text: str,
                  buttons: list, *, parse_mode: str | None = None) -> None:
    """Redraw one screen of the Choose GPU flow in place when possible
    (2026-09-12) — switching type, opening/leaving a submenu and refreshing
    stock used to each send a brand new message, so a few taps left a wall
    of near-duplicate screens behind. Mirrors the edit-first, send-as-
    fallback shape _show_panel already uses for the same reason.

    Falls back to a fresh send whenever there is nothing to edit yet (the
    very first [Run] tap lives on the job panel, a DIFFERENT message) or the
    edit target is gone (deleted by the user, or too old to edit).
    """
    if message_id is not None and tg.edit_message(
            chat_id, message_id, text, buttons=buttons, parse_mode=parse_mode):
        return
    tg.send_message(chat_id, text, buttons=buttons, parse_mode=parse_mode)


def _provider_row(active: str) -> list:
    """The first row of the Choose GPU screen: [RunPod] [Vast], the active one ticked. Tapping the
    ticked one just redraws the screen it is already on."""
    return [("RunPod ✓" if active == "runpod" else "RunPod", _CB_RUN_RUNPOD),
            ("Vast ✓" if active == "vast" else "Vast", _CB_RUN_VAST)]


def _panel_manifest(chat_id: int) -> Manifest | None:
    """The manifest the Choose GPU screen is deciding a rental for: the one on disk once Phase A
    has offered its rent panel (its run token matches), otherwise the draft about to be written.
    Neither read touches the live file's mtime."""
    if _PHASE_A_OFFERED.get(chat_id) == _run_token(chat_id):
        try:
            return load_manifest(_job_manifest_path(chat_id))
        except (ManifestError, OSError):
            return None
    return _draft_manifest(chat_id)


def _offer_vast_panel(tg: Tg, chat_id: int, *, message_id: int | None, force: bool,
                      spend_cb: str, heading: str | None) -> None:
    """The Vast tab (spec §3.5): the offer, its price, this batch's bandwidth and session cost,
    the cold start, and a spend button only when nothing blocks it. No datacenter or stock lines
    and no Switch GPU / Other regions: v1 searches only the configured 5090 and a Vast rental has
    no volume to migrate. The marketplace search is a blocking call of a few seconds (4 s measured
    2026-09-19, bounded at 120 s), so the caller shows an interstitial first."""
    manifest = _panel_manifest(chat_id)
    if manifest is None:
        _edit_or_send(tg, chat_id, message_id,
                      "no complete job yet — send the required files first",
                      [_provider_row("vast")])
        return
    gb = vast_download_gb(manifest)
    view = vast_build_view(
        manifest, gb=gb, enabled=_vast_enabled(),
        quote_fn=lambda: vast_fetch_quote(gb, force=force, repo_root=_REPO_ROOT),
        credit_fn=vast_credit)
    lines = [heading or f"{ICON_NVIDIA_CE} <b>Choose GPU</b>", "", *view.lines]
    buttons = [_provider_row("vast"),
               [("Refresh", _CB_RUN_REFRESH + "v", _ce_id(ICON_REFRESH_CE))]]
    if view.can_spend:
        buttons.append([(f"Yes, spend ≈${view.session_usd:.2f} on Vast", spend_cb + _VAST_SUFFIX,
                         _ce_id(ICON_ROCKET_CE)),
                        ("Cancel", _CB_RUN_NO)])
    else:
        buttons.append([("Cancel", _CB_RUN_NO)])
    _edit_or_send(tg, chat_id, message_id, "\n".join(lines), buttons, parse_mode=PARSE_HTML)


def _offer_run_confirm(tg: Tg, chat_id: int, *, message_id: int | None = None,
                       force: bool = False, spend_cb: str | None = None,
                       heading: str | None = None, phase_a: bool = False,
                       gpu_provider: str | None = None) -> None:
    """The step between [Run] and spending money: always lists every known
    GPU's live stock/price at the home datacenter and lets [Confirm] switch
    to any of them before renting (2026-09-02, widened from "only offer a
    switch when the current one reads Low/none" — a picker that only
    appears when something is already wrong is not a picker, it is a
    warning, and the user asked to be able to choose regardless of stock).

    Deliberately does NOT try to move the rental to a different
    DATACENTER automatically, even though the 5090 does exist at EU-CZ-1
    (docs/gpu-pod.md) — that needs the Network Volume synced there first,
    a 15-25 minute, multi-pod runbook. Since 2026-09-02 it does have a
    one-tap version (the migrate button below, behind its own destructive
    confirm), but it is still a separate, destructive operation and never
    something [Confirm] does implicitly: this path only ever switches the
    GPU TYPE, never the datacenter, so it only ever rewrites .env's GPU= to
    something the SAME pod-provision.sh call would already rent correctly.

    A stock-check failure, or an unknown home datacenter, fails OPEN
    (falls through to the plain confirm): an informational check must
    never be the reason [Run] itself stops working, and a picker with no
    datacenter to compare against would be showing numbers that do not
    mean what they claim to.

    `spend_cb` and `heading` exist because this panel is rendered twice with
    different meanings (2026-09-16): once before anything has run, where
    [Yes, spend] starts Phase A, and once after Phase A has finished, where it
    rents the pod for a batch whose try-on is already on disk. The stock
    rendering is identical in both — that is the reason to parameterise rather
    than duplicate — and the second one is the whole point of the change, since
    it measures stock at the moment of the decision instead of minutes before.

    `phase_a` replaces the whole screen with a plain try-on confirm and returns
    before any stock is read. With the try-on moved ahead of the rental the
    first tap no longer rents anything: it spends Gemini quota. This used to
    relabel only the spend button and still draw the full Choose GPU picker,
    which the user reported live (2026-09-18) as the GPU panel showing up
    before the try-on had run. It is the wrong question at that moment: the
    stock it quoted would be minutes stale by the time a pod was rented, and
    the real GPU decision happens on the second panel tick_phase_a renders
    once the try-on results exist.

    `message_id` edits that message in place instead of sending a new one
    (see _edit_or_send) — set by every caller except the two that send a fresh
    message: the very first [Run] tap on the job panel, and tick_phase_a's
    post-Phase-A panel. `force` bypasses stock_at_cached's 60s TTL for a
    real live recheck, used only by the 🔄 Refresh button.

    `gpu_provider` "vast" draws the Vast tab (_offer_vast_panel) instead of RunPod's stock screen;
    anything else (None included) is the RunPod screen, which gains only the [RunPod] [Vast] row
    on top — but mints its own spend button with an explicit `:runpod` suffix regardless of what
    this parameter was, so the tap that follows never depends on .env. The Phase A try-on confirm
    below has no provider row on purpose: that tap rents nothing, and the GPU is chosen on the
    panel drawn after the try-on finishes.
    """
    configured = env_get(ROOT / ".env", "GPU") or _PRIMARY_GPU_ID
    volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
    home_dc = volume_datacenter(volume_id)
    # Defaults resolved here rather than at each of the two mint sites below:
    # the fail-open branch (no stock data) and the normal one each build a
    # spend button, and two call sites defaulting independently is how a panel
    # ends up rendering one destination and spending with another — only when
    # the stock check fails, i.e. only in production.
    spend_cb = spend_cb or (_CB_RUN_GO + _run_token(chat_id))
    if phase_a:
        # No Refresh button either: this screen shows no stock to refresh.
        _edit_or_send(
            tg, chat_id, message_id,
            "This runs the try-on over the API first — Gemini quota, no GPU "
            "rented yet. You choose the GPU after the try-on finishes, with "
            "stock measured then.\nConfirm?",
            [[("Yes — run try-on first (Gemini quota, no GPU yet)", spend_cb,
               _ce_id(ICON_ROCKET_CE)),
              ("Cancel", _CB_RUN_NO)]])
        return
    if gpu_provider == "vast":
        _offer_vast_panel(tg, chat_id, message_id=message_id, force=force,
                          spend_cb=spend_cb, heading=heading)
        return
    wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
    try:
        stock = ((stock_at(wanted) if force else stock_at_cached(wanted))
                 if home_dc else {})
    except RuntimeError:
        stock = {}

    price = _gpu_price(configured, stock)
    # Resolved once: the fail-open branch and the normal one each mint a spend
    # button, and two sites wording it independently is how they drift.
    spend_label = f"Yes, spend ${price:.2f}/h"
    # This screen only ever rents RunPod (the vast branch above returned already), so its own
    # spend button always names its provider explicitly — closing the .env fallback for every
    # NEWLY minted button, same reasoning as _RUNPOD_SUFFIX's own comment.
    runpod_spend_cb = spend_cb + _RUNPOD_SUFFIX
    if not stock or not home_dc:
        _edit_or_send(
            tg, chat_id, message_id,
            f"This rents a GPU pod at ${price:.2f}/hour and starts the job.\n"
            "Confirm?",
            [_provider_row("runpod"),
             [("Refresh", _CB_RUN_REFRESH + "m", _ce_id(ICON_REFRESH_CE))],
             [(spend_label, runpod_spend_cb, _ce_id(ICON_ROCKET_CE)),
              ("Cancel", _CB_RUN_NO)]])
        return

    # "Current" gets its own paragraph rather than an inline "(current)" tag
    # (2026-09-02) — on a phone, three bold lines that only differ by six
    # small letters at the end read as one undifferentiated list; a reader
    # reported not being able to tell which was already selected.
    lines = [heading or (f"{ICON_NVIDIA_CE} <b>Choose GPU</b> — renting at "
                         f"{_esc(home_dc)}"), ""]
    configured_home = next((e for e in (stock.get(configured) or [])
                            if e.datacenter_id == home_dc), None)
    # Sold out at home covers both shapes runpodctl can report: an entry that
    # explicitly reads "none", or no entry there at all. Either way, [Run]
    # spending money on THIS card right now would just fail — so the button
    # that promises it is dropped rather than left enabled on a false promise
    # (2026-09-12, reported live: "5090 đã hết mà nút spend vẫn enable").
    sold_out = configured_home is None or configured_home.stock_status.lower() == "none"
    if configured_home is not None:
        icon = _stock_icon(configured_home.stock_status.lower())
        lines.append(f"Current: {icon} <b>{_esc(configured_home.display_name)}</b> — "
                     f"{_esc(configured_home.stock_status)} · "
                     f"{ICON_MONEY_CE} ${configured_home.price_per_hr:.2f}/h")
    else:
        lines.append(f"Current: <b>{_esc(configured)}</b> — "
                     f"not offered at {_esc(home_dc)}")

    alt_lines, switch_buttons = _switch_type_options(configured, home_dc, stock, wanted)
    other_lines, migrate_buttons = _migrate_options(stock, wanted, home_dc)
    if alt_lines:
        lines += ["", "Switch to:", *alt_lines]
    if other_lines:
        lines += ["", "Other regions (needs the volume migrated there first, "
                      f"{MIGRATE_DURATION_SHORT} — not an instant switch):",
                  *other_lines]
    elif not switch_buttons and not migrate_buttons:
        # Nothing else at this datacenter, and no other region has it
        # either — the manual EU-CZ-1 runbook is the only remaining option.
        lines += ["", "No other GPU or region has better stock right now. "
                      "See docs/gpu-pod.md for the manual EU-CZ-1 runbook."]

    # One button per CATEGORY here, not one per GPU (2026-09-12) — the flat
    # list used to pack up to ~10 switch/migrate buttons two-per-row, and
    # long names like "RTX PRO 4500" or "EU-CZ-1" got clipped on a phone
    # before a reader could tell which one they were about to tap. Each
    # category button opens a submenu (_offer_run_switch_menu /
    # _offer_run_migrate_menu) with the same options one full-width button
    # per row, so nothing there is packed tight enough to truncate.
    buttons = [_provider_row("runpod")]
    if switch_buttons:
        buttons.append([("Switch GPU type ▸", _CB_RUN_SWITCH_MENU, _ce_id(ICON_NVIDIA_CE))])
    if migrate_buttons:
        buttons.append([("Other regions ▸", _CB_RUN_MIGRATE_MENU, _ce_id(ICON_DEPART_CE))])
    buttons.append([("Refresh", _CB_RUN_REFRESH + "m", _ce_id(ICON_REFRESH_CE))])
    # No spend button at all when sold out (2026-09-12) — a "Yes, spend" that
    # can only fail is worse than no button, and Refresh (just above) is the
    # honest next action instead.
    if sold_out:
        buttons.append([("Cancel", _CB_RUN_NO)])
    else:
        buttons.append([(spend_label, runpod_spend_cb, _ce_id(ICON_ROCKET_CE)),
                        ("Cancel", _CB_RUN_NO)])
    _edit_or_send(tg, chat_id, message_id, "\n".join(lines), buttons,
                 parse_mode=PARSE_HTML)


def _offer_run_for_chat(tg: Tg, chat_id: int, *, message_id: int | None = None,
                        force: bool = False, gpu_provider: str | None = None) -> None:
    """[Run]'s first screen, choosing between the two flows by manifest content.

    A chat whose draft has local try-on gets the two-step flow (Phase A, then
    decide about the GPU with the try-on results in hand and stock measured
    now). One without it keeps the single tap it has always had: there is
    nothing for Phase A to run, so an extra screen would report nothing and
    cost a round trip.

    All four PRE-spend call sites route through here rather than calling
    _offer_run_confirm directly (_CB_RUN_ASK, _CB_RUN_SWITCH, _CB_RUN_BACK and
    _CB_RUN_REFRESH). They have to, or the panel re-rendered after a GPU switch
    or a Refresh would drop back to the one-step flow and its button would
    promise a rental that the first screen said was two steps away.

    tick_phase_a's post-Phase-A panel is the one caller that must NOT come
    through here, and does not: by then the try-on has already run, so the
    answer would be a stale True and the button would offer to spend Gemini
    quota a second time on a batch whose images are already on disk. It passes
    its own spend_cb and heading and leaves phase_a at its default.

    Asks the DRAFT, not the manifest on disk: at _CB_RUN_ASK time the file may
    not have been written yet.

    Once tick_phase_a has offered the rent panel for this exact manifest
    (_PHASE_A_OFFERED holds its run token), every re-render goes back to THAT
    panel. Without it, Switch GPU / Refresh / Back on the post-Phase-A panel
    came through here, saw a try-on draft, and dropped the user back on the
    "run try-on first" screen — a button that re-ran Phase A instead of
    renting.

    `gpu_provider` is threaded to whichever screen is drawn, so a Refresh or the Vast tab stays on
    the provider the user chose.
    """
    if _PHASE_A_OFFERED.get(chat_id) == _run_token(chat_id):
        _offer_rent_after_phase_a(tg, chat_id, message_id=message_id, force=force,
                                  gpu_provider=gpu_provider)
        return
    _offer_run_confirm(tg, chat_id, message_id=message_id, force=force,
                       phase_a=_job_has_local_tryon(chat_id), gpu_provider=gpu_provider)


def _offer_rent_after_phase_a(tg: Tg, chat_id: int, *,
                              message_id: int | None = None,
                              force: bool = False,
                              gpu_provider: str | None = None) -> None:
    """The Choose GPU panel for a batch whose try-on is already on disk: its
    spend button resumes into a rental (_CB_PHASE_A_SPEND) rather than
    starting Phase A again."""
    _offer_run_confirm(
        tg, chat_id, message_id=message_id, force=force,
        spend_cb=f"{_CB_PHASE_A_SPEND}{_run_token(chat_id)}",
        heading=f"{ICON_NVIDIA_CE} <b>Try-on finished</b> — now rent a GPU?",
        gpu_provider=gpu_provider)


def _offer_run_switch_menu(tg: Tg, chat_id: int, *, message_id: int | None = None,
                           force: bool = False) -> None:
    """The submenu behind the Choose GPU screen's ▸ Switch GPU type button —
    same-datacenter alternatives, one full-width button per row.

    Recomputes stock from scratch rather than reusing anything from the
    screen that opened it: nothing is threaded through callback_data (it
    stays a short, fixed string — Bot API caps it at 64 bytes), and
    stock_at_cached's own 60s TTL makes a second call here effectively free.
    A stale button tapped after the config changed just shows whatever is
    true now, same fail-open posture as _offer_run_confirm itself. `force`
    bypasses that TTL, for the 🔄 Refresh button.
    """
    configured = env_get(ROOT / ".env", "GPU") or _PRIMARY_GPU_ID
    volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
    home_dc = volume_datacenter(volume_id)
    wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
    try:
        stock = ((stock_at(wanted) if force else stock_at_cached(wanted))
                 if home_dc else {})
    except RuntimeError:
        stock = {}
    _lines, buttons = _switch_type_options(configured, home_dc, stock, wanted)
    if not buttons:
        _edit_or_send(tg, chat_id, message_id,
                      "nothing else to switch to right now.",
                      [[("◀ Back", _CB_RUN_BACK)]])
        return
    rows = [[b] for b in buttons]
    rows.append([("Refresh", _CB_RUN_REFRESH + "s", _ce_id(ICON_REFRESH_CE))])
    rows.append([("◀ Back", _CB_RUN_BACK)])
    _edit_or_send(tg, chat_id, message_id,
                 f"Switch to a different GPU, still at {_esc(home_dc)}:",
                 rows, parse_mode=PARSE_HTML)


def _offer_run_migrate_menu(tg: Tg, chat_id: int, *, message_id: int | None = None,
                            force: bool = False) -> None:
    """The submenu behind the Choose GPU screen's ▸ Other regions button —
    mirrors _offer_run_switch_menu but for cross-datacenter migrate
    candidates. Tapping one of these still only opens _ask_migrate's
    destructive confirm screen; nothing here starts a migration itself.
    `force` bypasses stock_at_cached's TTL, for the 🔄 Refresh button.
    """
    volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
    home_dc = volume_datacenter(volume_id)
    wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
    try:
        stock = ((stock_at(wanted) if force else stock_at_cached(wanted))
                 if home_dc else {})
    except RuntimeError:
        stock = {}
    _lines, buttons = _migrate_options(stock, wanted, home_dc)
    if not buttons:
        _edit_or_send(tg, chat_id, message_id,
                      "no other region has better stock right now.",
                      [[("◀ Back", _CB_RUN_BACK)]])
        return
    rows = [[b] for b in buttons]
    rows.append([("Refresh", _CB_RUN_REFRESH + "g", _ce_id(ICON_REFRESH_CE))])
    rows.append([("◀ Back", _CB_RUN_BACK)])
    _edit_or_send(tg, chat_id, message_id,
                 "Migrate the volume to rent elsewhere "
                 f"({MIGRATE_DURATION_SHORT} — not an instant switch):",
                 rows)


def _close_progress_as_killed(tg: Tg, chat_id: int) -> None:
    """Retire whichever progress message this chat has, drain's or Phase A's.

    Both of _do_kill's branches need exactly this and used to carry their own
    copy of it. One body, because the failure it prevents is identical for
    both: the file left behind pins the poll loop at 2s forever via
    `animating = _progress_path(...).exists()` (~30 Telegram calls a minute
    instead of ~1.2), under a message frozen on whatever it last said with
    nothing running. Nothing else clears it — tick_progress and tick_phase_a
    both return at their ownership guards once the thing they watch is gone.

    Every failure is swallowed: the message may have been deleted by the user
    or by /wipe, and the payload may predate a format change. The unlink is
    the part that matters and it happens regardless, which is why it sits
    outside the try.
    """
    prog = _progress_path(chat_id)
    if prog.exists():
        try:
            payload = json.loads(prog.read_text(encoding="utf-8"))
            tg.edit_message(chat_id, int(payload["message_id"]),
                            "🛑 <b>Killed by request</b> — nothing left running.",
                            parse_mode=PARSE_HTML)
        except (ValueError, KeyError, TypeError, TgError):
            pass
        prog.unlink(missing_ok=True)
    _ANIM_PAUSE.pop(chat_id, None)


def _ask_kill(tg: Tg, chat_id: int) -> None:
    """The confirm step for /kill — mirrors [Run]'s Yes/Cancel, for the
    opposite reason: this one forfeits money already spent instead of
    committing new money.

    The Phase A branch below is PARALLEL to the pod one, not a widening of it:
    /kill's meaning for a billed drain is unchanged. It exists because
    2026-09-16's [Run] rewiring moved Phase A out of the drain's own Popen and
    into its own handle (run._PHASE_A), invisible to drain_running — so from
    that change on, a try-on phase stuck hammering a misconfigured Gemini key
    would have been answered "there is no pod to kill" while it kept spending
    quota, with no way at all to stop it short of restarting the bot.

    `and not drain_running` is what keeps it parallel rather than in front. A
    drain must always win the branch: if both were ever live for one manifest,
    the cheap branch would report "nothing was rented" and leave a real pod
    billing — and the user, correctly believing /kill had handled it, would
    have no reason to run it again. Money beats quota whenever the two
    disagree, so the expensive branch is the one that must be unreachable by
    accident.
    """
    manifest_path = _job_manifest_path(chat_id)
    if phase_a_running(manifest_path) and not drain_running(manifest_path):
        tg.send_message(
            chat_id,
            f"{ICON_WARN} This stops the try-on phase in progress — no pod is "
            "rented, so there is nothing to destroy, but Gemini calls already "
            "made are not refunded. Are you sure?",
            buttons=[[("🛑 Yes, stop it", _CB_KILL_GO),
                      ("↩️ Leave it running", _CB_KILL_NO)]],
            parse_mode=PARSE_HTML)
        return
    if not drain_running(manifest_path):
        tg.send_message(chat_id, "nothing is running for this chat right now — "
                                 "there is no pod to kill")
        return
    lease = lease_for(manifest_path)
    spent = ""
    if lease is not None:
        mins = int((time.time() - lease.provisioned_at) / 60)
        # RunPod's flat rate is only true of RunPod; a Vast pod is priced by its own offer, which
        # the lease does not carry, so it gets the time and no invented dollar figure.
        spent = (f" — already {mins} min (${mins / 60 * 0.99:.2f}) on the pod"
                 if lease.provider == "runpod" else f" — already {mins} min on the pod")
    tg.send_message(
        chat_id,
        f"{ICON_WARN} This destroys the pod right now{spent}. Whatever is mid-render "
        "is lost — no output, no resume. Are you sure?",
        buttons=[[("🛑 Yes, kill it", _CB_KILL_GO), ("↩️ Leave it running", _CB_KILL_NO)]],
        parse_mode=PARSE_HTML)


def _signal_drain_group(proc, sig: int) -> None:
    """Signal the WHOLE process group `start_drain` (tgbot/run.py) launched with
    `start_new_session=True` — make, drain.py and the vast_rent.py it may spawn all sit in it.
    A bare `proc.terminate()`/`proc.kill()` only reaches this one process; before F2 (2026-09-19)
    that left an orphaned vast_rent.py that kept renting after /kill had already told the user
    the pod was destroyed. Falls back to the plain Popen method when the group is already gone
    (ProcessLookupError) or signalling it is not permitted for some other OS reason.
    """
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        (proc.terminate if sig == signal.SIGTERM else proc.kill)()


def _do_kill(tg: Tg, chat_id: int) -> None:
    """The emergency stop (2026-09-02): destroy the pod right now, on request.

    Two layers, because neither alone is trustworthy. Signalling the Popen's process group
    (only present when THIS bot process is the one that started the drain) stops batch_run.py
    from moving on to its next stage, but SIGTERM does not run drain.py's `finally:
    teardown()` — Python's default handler kills the process outright rather than raising
    something `finally` could catch — so `make gpu-destroy` is always run here directly
    afterwards too, exactly as pod_watchdog.py's tier 3 does not trust a runner to clean up
    after itself. `make gpu-destroy` already re-lists and verifies the pod is actually gone
    (Makefile:161-167) rather than trusting its own exit code, so this reuses that rather than
    re-deriving it.

    The wait is 30s, not the original 5s (F2/C2, 2026-09-19): vast_rent.py's own unwind (destroy
    the just-created instance, verify it, clear .env) can itself take several seconds of API
    calls, and 5s was cutting that off mid-unwind, escalating to SIGKILL while it was still
    trying to destroy the very instance the kill was meant to stop.

    The Phase A branch returns before any of that, and must: nothing was
    rented, so `make gpu-destroy` here would tear down whatever unrelated pod
    .env happens to name, and clear_lease would wipe a lease that belongs to
    someone else's run. It carries _ask_kill's `and not drain_running` for the
    reason given there — a billed pod always wins the branch — and re-derives
    both predicates rather than trusting the ask step, the same
    don't-trust-the-ask idiom _run_token encodes. That matters more here than
    usual: a phase can finish, or a drain can start, in the seconds a confirm
    button sits unanswered.
    """
    manifest_path = _job_manifest_path(chat_id)
    if phase_a_running(manifest_path) and not drain_running(manifest_path):
        stopped = stop_phase_a(manifest_path)
        _close_progress_as_killed(tg, chat_id)
        # Conditioned on stop_phase_a's return value, which is its whole
        # contract: claiming a stop for a phase that had already exited tells
        # the user they halted a run that may well have succeeded.
        tg.send_message(
            chat_id,
            "🛑 Stopped the try-on phase. Nothing was rented, and Gemini calls "
            "already made are not refunded." if stopped else
            "the try-on phase had already finished — nothing to stop.")
        return

    proc = _RUNNING.get(manifest_path.resolve())
    if proc is not None and proc.poll() is None:
        _signal_drain_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _signal_drain_group(proc, signal.SIGKILL)

    tg.send_message(chat_id, "🛑 destroying the pod…")
    # The lease says which cloud rented the pod. The bot's own environment does not, and a bare
    # `make gpu-destroy` would fall back to .env's provider. Read before clear_lease below.
    # On chained links, drain.py's chain_or_teardown rewrites lease.manifest to the claimed
    # link, so lease_for(manifest_path) returns None; fall back to the global lease file.
    lease = lease_for(manifest_path) or read_lease(LEASE_PATH)
    kill_env = {**os.environ, "GPU_PROVIDER": lease.provider} if lease is not None else None
    try:
        result = subprocess.run(["make", "gpu-destroy"], cwd=_REPO_ROOT,
                                capture_output=True, text=True, timeout=180, env=kill_env)
        destroyed = result.returncode == 0
        output = (result.stdout + result.stderr).strip()[-TAIL_CHARS:]
        detail = "" if destroyed else f"\n<blockquote expandable>{_esc(output)}</blockquote>"
    except subprocess.TimeoutExpired:
        destroyed = False
        detail = "\n`make gpu-destroy` did not finish within 180s — check the box by hand."

    # Cleared regardless of whether the destroy itself succeeded: a lease for
    # a pod that may or may not be gone is worse than none, because it is the
    # one thing that makes drain_running() (and therefore a second /confirm)
    # believe a dead job is still live.
    clear_lease(LEASE_PATH)
    _close_progress_as_killed(tg, chat_id)

    if destroyed:
        tg.send_message(chat_id, "🛑 Killed. Pod destroyed and verified gone.")
    else:
        tg.send_message(chat_id,
                        f"{ICON_WARN} <b>gpu-destroy may not have worked</b> — check "
                        f"manually, it may still be billing.{detail}",
                        parse_mode=PARSE_HTML)


def _ask_migrate(tg: Tg, chat_id: int, to_dc: str) -> None:
    """The confirm step for a Network Volume migration — destructive, unlike
    [Run]'s Yes/Cancel, because this one deletes the SOURCE volume once the
    copy verifies, not just spends money going forward.

    Takes only the destination datacenter. It used to take a `gpu_id` too,
    decoded from the callback payload and then never read — a migration moves
    a volume and has nothing to do with which GPU gets rented afterwards.
    """
    if migration_running():
        tg.send_message(chat_id, "a volume migration is already in progress — "
                                 "wait for it to finish before starting another")
        return
    tg.send_message(
        chat_id,
        f"This copies your Network Volume to {_esc(to_dc)}: ~2 temporary CPU pods "
        f"for the duration, then <b>deletes the current volume</b> once the copy is "
        f"verified byte-for-byte. {MIGRATE_DURATION_TEXT}. "
        f"<b>Cannot be undone</b> once the old volume is deleted.",
        parse_mode=PARSE_HTML,
        buttons=[[("Yes, migrate", _CB_MIGRATE_GO + to_dc, _ce_id(ICON_DEPART_CE)),
                  ("Cancel", _CB_MIGRATE_NO)]])


def _start_migration(tg: Tg, chat_id: int, to_dc: str) -> None:
    """Launch scripts/volume_migrate.py, once.

    The marker is written BEFORE Popen and synchronously, not after: the whole
    point is to close the window between deciding to launch and
    volume_migrate.py writing its own lease minutes later. See
    _migrate_launch_marker for what a second migration in that window costs.
    """
    if migration_running():
        tg.send_message(chat_id, "a volume migration is already in progress")
        return

    marker = _migrate_launch_marker()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"at": time.time(), "to_dc": to_dc}),
                      encoding="utf-8")
    try:
        # Output to a log file, not a pipe and not inherited (I3): a migration
        # runs for tens of minutes, an unread Popen pipe deadlocks the child
        # when its OS buffer fills, and inheriting the bot's own stdout puts
        # the script's traceback into journald interleaved with the bot's.
        # Same shape as tgbot/run.py's start_drain.
        with open(_migrate_log_path(), "ab") as log_file:
            proc = subprocess.Popen(
                ["python3", "scripts/volume_migrate.py", "--to-dc", to_dc, "--yes"],
                cwd=_REPO_ROOT, stdout=log_file, stderr=subprocess.STDOUT)
    except OSError as exc:
        # The launch itself failed, so nothing will ever clear this marker by
        # finishing. Remove it now rather than make the user wait out
        # _MIGRATE_LAUNCH_GRACE_SEC for a migration that never started.
        marker.unlink(missing_ok=True)
        log(f"could not start volume_migrate.py: {exc!r}")
        tg.send_message(chat_id, "could not start the migration — check the box. "
                                 "Nothing was created.")
        return
    _MIGRATE_PROC[_MIGRATE_PROC_KEY] = proc

    tg.send_message(chat_id, f"{ICON_DEPART_CE} Migration to {_esc(to_dc)} started — this will take "
                             f"{MIGRATE_DURATION_PLAIN}. I will report progress here.",
                    parse_mode=PARSE_HTML)


def _again(tg: Tg, chat_id: int) -> None:
    """Rebuild the last submitted job so it can be re-run with one thing changed.

    This repo's working method is A/B: change one variable, hold the rest. Until
    now /confirm cleared the draft, so re-running the same material through a
    different pipeline meant re-uploading every file.
    """
    if _STATE.get(chat_id) is not None:
        # Refuse rather than overwrite: a half-built job is work already done,
        # and nothing else would recover it.
        tg.send_message(chat_id, "you have a job in progress — /job to see it, "
                                 "/clear to drop it, then /again")
        return
    path = _last_path(chat_id)
    if not path.exists():
        tg.send_message(chat_id, "nothing to repeat yet — /again reuses the "
                                 "material from the last job you ran")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        # Records written before batches existed hold a single job at the top
        # level. Read both rather than discard a user's last batch on upgrade.
        entries = payload["jobs"] if "jobs" in payload else [payload]
        jobs = _load_jobs(entries)
        if not jobs:
            raise ValueError("no runs recorded")
        for restored in jobs:
            if restored.pipeline not in PIPELINES:
                raise ValueError(f"unknown pipeline {restored.pipeline!r}")
    except (ValueError, KeyError, TypeError) as exc:
        log(f"last-job file for chat {chat_id} is unreadable: {exc!r}")
        tg.send_message(chat_id, "the last job's record is unreadable — send "
                                 "the files again")
        return
    # The staged copies may have been swept by `make batch-clean` or /clear
    # since. Named individually: "some files are missing" is not actionable.
    gone = sorted({f"{r} ({sp.name})" for restored in jobs
                   for r, sp in restored.slots.items() if not sp.is_file()})
    if gone:
        tg.send_message(chat_id,
                        "cannot repeat that batch — these files are no longer "
                        f"on disk: {', '.join(gone)}")
        return
    # The last job becomes the editable one and the rest go back in the basket,
    # which is the shape they were submitted in.
    *earlier, current = jobs
    _BASKET[chat_id] = earlier
    _STATE[chat_id] = current
    # Used to be its own button-less tg.send_message telling the user to type
    # /pipeline — the panel below already carries Run and the ⚙️ pipeline
    # switch as buttons, so folding this into its note (the same merge every
    # other flow uses, e.g. _add_to_batch, _drop_current) puts the buttons on
    # the message the user actually reads instead of a second one below it.
    _maybe_show_manifest(tg, chat_id, current,
                         note=f"reusing the last batch — {len(jobs)} job(s)")


def _do_resume(tg: Tg, chat_id: int, manifest_path: Path, *, dry_run: bool,
               gpu_provider: str | None = None) -> bool:
    """Continue a batch whose pod rental already failed once — reached only
    from the recovery buttons _deliver_provision_failure offers, after the
    user picked a different GPU (_CB_RECOVER_SWITCH) or asked to retry the
    same one (_CB_RECOVER_RETRY), after a migration finished
    (tick_migration_progress's own resume-on-done), or from the post-Phase-A
    rent panel (_CB_PHASE_A_SPEND), where the batch's try-on is already on
    disk and the pod is the only thing still missing.

    Deliberately NOT routed through _do_confirm: that function's checks
    (a drafted job in _STATE, the unanswered-file queue, cached validation)
    are about a job still being assembled in chat, and there is none of that
    for a manifest that was already confirmed and ran its local phase —
    money was already committed to THIS exact manifest the first time
    /confirm ran. _do_resume is the second, and only other, function in this
    file allowed to call start_drain: unlike _do_confirm it can only ever be
    reached for a manifest that already has a recorded batch id (proof it
    already passed the money gate once), so it is not a second way to spend
    money the user has not already agreed to.

    `gpu_provider` is "vast" when the user picked Vast on the panel, None for everything else
    (drain then uses .env's provider, as it always did). A Vast rental has no Network Volume, so
    the migration guard does not apply to it, and it must pass _vast_refusal — the same checks the
    panel's hidden spend button shows — before anything is started. Returns True only when a drain
    was started, so a caller can tell a refusal from a launch.
    """
    if gpu_provider != "vast" and migration_running():
        tg.send_message(chat_id, "a volume migration is in progress for this "
                                 "pod's datacenter — wait for it to finish "
                                 "before retrying")
        return False
    # busy(), not drain_running(): this is about to hand the manifest to
    # drain.py, which READS it — the predicate run.busy's own docstring names
    # for exactly that. A live Phase A holds no lease and registers no _RUNNING
    # entry, so drain_running() answers False for it and the resume would start
    # underneath a child still writing the same batch/<name>.state.json; two
    # writers on one journal is the corruption those guards exist to prevent.
    # The reply needs no rewording for that case: "already running" is true of
    # a Phase A too, and unlike /clear's and /wipe's strings it never claimed
    # the thing running was a drain.
    if busy(manifest_path):
        tg.send_message(chat_id, "already running — nothing to resume")
        return False
    state = load_state(state_path_for(manifest_path))
    if not state.get("batch"):
        tg.send_message(chat_id, f"nothing to resume for {_esc(manifest_path.stem)} "
                                 "— that batch never started")
        return False
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        tg.send_message(chat_id, f"could not resume — {_esc(str(exc))}")
        return False
    if gpu_provider == "vast":
        refusal = _vast_refusal(manifest)
        if refusal is not None:
            tg.send_message(chat_id, refusal, parse_mode=PARSE_HTML)
            return False
    clear_provision_failure(provision_failure_path(manifest_path))
    stages: list[str] = []
    for run in manifest.runs:
        for stage in PIPELINES[run.pipeline]:
            if stage not in stages:
                stages.append(stage)
    where = " on Vast.ai" if gpu_provider == "vast" else ""
    tg.send_message(chat_id, f"{ICON_ROCKET_CE} <b>Retrying</b> — renting a pod{where} "
                             f"again for {_esc(manifest_path.stem)}.",
                    parse_mode=PARSE_HTML)
    # Every caller now names its provider explicitly ("vast" from the panel, "runpod" from the
    # RunPod-only recovery buttons) except _do_resume's own direct callers with none to give (e.g.
    # a bare _do_resume(...) in a test) — those still fall back to .env exactly as before.
    provider_kwargs = {} if gpu_provider is None else {"gpu_provider": gpu_provider}
    start_drain(manifest_path, dry_run=dry_run, resume=True, **provider_kwargs)
    _start_progress(tg, chat_id, manifest_path, stages, **provider_kwargs)
    return True


def _manifest_write_ok(chat_id: int) -> bool:
    """May this chat's manifest file be rewritten right now?

    One predicate for the guards that exist because a child process READS that
    file (drain.py, in both its modes). /clear and /wipe use it as their whole
    answer; _render_and_validate uses it only for the narrower
    mailbox-already-occupied case, because it has its own unconditional Phase A
    refusal above that and must still let a plain drain through to the mailbox.

    busy() rather than drain_running(), since --phase-a-only is drain.py too
    and a rewrite mid-Phase-A corrupts a running child's input.
    """
    return not busy(_job_manifest_path(chat_id))


# The two answers _busy_reason can give. Constants, not literals at each site,
# because _do_phase_a's refusal BRANCHES on which one it got — the drain case
# can point at /confirm and the Phase A case must not — and comparing against a
# re-typed sentence would send that branch silently down its else the first
# time anyone reworded the prose.
_REASON_DRAIN = "a drain is running"
_REASON_PHASE_A = "the try-on phase is running"


def _busy_reason(chat_id: int) -> str:
    """The leading clause of a refusal from a guard that just failed
    _manifest_write_ok — which half of busy()'s `or` is actually true.

    busy() is `drain_running() or phase_a_running()`, and saying "a drain is
    running" for the second half is a lie in the direction that costs the most:
    it tells the user a pod is burning $0.99/hour when nothing is rented at
    all, so "wait for it" reads as "hurry", and the honest next action (/kill,
    which since 2026-09-17 can stop a Phase A) is never suggested.

    drain_running is reached through `run_mod`, not the name imported at the
    top of this file, so it resolves through the SAME module globals busy()
    resolves it through. Any other spelling lets the two disagree: a caller
    that substitutes one and not the other would get busy() True and
    drain_running False, and this function would name Phase A for a live
    drain. Phase A is the else branch for the same reason — busy() was already
    True, so if it is not a drain there is nothing else it can be.
    """
    return (_REASON_DRAIN if run_mod.drain_running(_job_manifest_path(chat_id))
            else _REASON_PHASE_A)


def _job_has_local_tryon(chat_id: int) -> bool:
    """Would this chat's next batch have a Phase A?

    Reads the drafted jobs, not the manifest on disk: at [Run] time the file
    may be stale or absent, and the jobs in _STATE/_BASKET are what
    write_manifest is about to render.

    Renders to a throwaway file and loads it back rather than inspecting the
    jobs directly, because the alternative is a second opinion —
    _local_tryon_stage is the only thing allowed to answer this, and it takes a
    loaded Run. A TemporaryDirectory, not mkdtemp(): this runs on every [Run]
    tap, and a leaked directory per tap on a long-lived VPS process is how /tmp
    fills up with something nobody owns.

    Fails towards False, i.e. towards _do_confirm and today's behaviour: a
    manifest that will not render here will not render in write_manifest
    either, and _do_confirm reports that properly. Silently doing nothing would
    be worse than falling through.
    """
    manifest = _draft_manifest(chat_id)
    return manifest is not None and has_local_tryon(manifest)


def _draft_manifest(chat_id: int) -> Manifest | None:
    """The manifest this chat's drafted jobs WOULD write, loaded back from a throwaway file — or
    None when there is no job or it will not render. The one place that answers "what is about to
    be submitted" without touching the live manifest file, whose mtime is the run token that every
    button in the chat is checked against."""
    queued = _jobs_for(chat_id)
    if not queued:
        return None
    try:
        text = render_manifest(queued, now=time.strftime("%Y-%m-%d %H:%M:%S"))
        with tempfile.TemporaryDirectory() as d:
            probe = Path(d) / "probe.yaml"
            probe.write_text(text, encoding="utf-8")
            return load_manifest(probe)
    except (ManifestError, OSError):
        return None


def _vast_enabled() -> frozenset[str]:
    """Pipelines allowed to rent on Vast: VAST_ENABLED_PIPELINES from the environment, then .env.
    Empty by default — spec §1 wants one measured Vast session per pipeline family before its
    spend button exists, and as of 2026-09-19 none has been run through the bot."""
    return parse_enabled(os.environ.get("VAST_ENABLED_PIPELINES")
                         or env_get(ROOT / ".env", "VAST_ENABLED_PIPELINES"))


# How long a price quote may back a Vast spend: the panel's own cache lives 60 s, and a person
# reading a panel and tapping takes minutes; ten minutes covers that without trusting a quote from
# yesterday. The GB tolerance is rounding: vast_quote keys its cache on the size to 0.1 GB.
_QUOTE_MAX_AGE_S = 600.0
_QUOTE_GB_TOLERANCE = 0.15


def _vast_queue_refusal(chat_id: int, live_path: Path) -> str | None:
    """Why the job being assembled must NOT be queued onto the drain running now; None means it
    may. Only a Vast pod needs this.

    A job queued while a drain runs is claimed by drain.py's chain_or_teardown and run on the SAME
    pod (scripts/batchlib_ext/handoff.py). A Vast pod has only the models of the manifest it was
    rented for (wait_and_bootstrap sets VAST_MODEL_IDS from that manifest alone) and no volume to
    fall back on, so a queued job whose pipeline was never enabled for Vast, or that needs a model
    the pod lacks, would run on a billing box and fail — or sit queued — at the worker. RunPod's pod
    mounts the whole model volume, so it is exempt. The lease says which cloud the drain is on;
    lease_for misses a chained link (its lease points at the claimed manifest), so it falls back to
    the global lease file exactly as _do_kill does."""
    lease = lease_for(live_path) or read_lease(LEASE_PATH)
    if lease is None or lease.provider != "vast":
        return None
    draft = _draft_manifest(chat_id)
    if draft is None:
        return None
    reasons = static_blockers(draft, _vast_enabled())
    if not reasons:
        # Both reads can raise KeyError: static_blockers tolerates a pipeline outside PIPELINES
        # (PIPELINES.get(..., [])), but models_for_manifest indexes PIPELINES directly and does
        # not — reachable only if a draft ever carried such a pipeline, which the bot's own job
        # picker never assembles (2026-09-19 review finding), but the fail-closed answer must
        # cover the draft's own manifest too, not only the on-pod one.
        try:
            on_pod = models_for_manifest(load_manifest(live_path))
            missing = sorted(models_for_manifest(draft) - on_pod)
        except (ManifestError, OSError, KeyError):
            reasons.append("could not determine the models this job or the running pod needs, "
                           "so its safety cannot be checked")
        else:
            if missing:
                reasons.append("this pod only has the models for the batch it was rented for; "
                               f"this job also needs {', '.join(missing)}")
    if not reasons:
        return None
    return (f"{ICON_WARN} <b>Not queued</b> — the pod running now is on Vast.ai, and nothing was "
            "added to it:\n" + "\n".join(f"• {_esc(reason)}" for reason in reasons)
            + "\nYour files are kept. Wait for the current job to finish, then Run this one as "
              "its own rental.")


def _vast_refusal(manifest: Manifest | None) -> str | None:
    """Why a Vast spend must NOT start now, as HTML for the chat; None means it may.

    The money-gate twin of the panel's hidden spend button. Telegram keeps buttons tappable
    forever, so a button drawn when the account was funded and the pipeline enabled can be tapped
    after either changed — the panel alone is not a gate. No marketplace search here: the credit is
    compared with the estimate from the last quote, so a stale tap cannot stall the bot."""
    if manifest is None:
        return f"{ICON_WARN} <b>Not renting on Vast</b> — no manifest to check. Nothing was spent."
    quote = vast_last_quote()
    # A quote counts only while it is still about THIS rental: fetched for this batch's download
    # size and recently enough that the offer and the price are plausibly still there. Anything
    # else is treated as no quote, so the gate refuses instead of comparing the credit with the
    # wrong batch's estimate (or a day-old one).
    if quote is not None and (time.time() - quote.fetched_at > _QUOTE_MAX_AGE_S
                              or abs(quote.gb - vast_download_gb(manifest)) > _QUOTE_GB_TOLERANCE):
        quote = None
    reasons = spend_blockers(manifest, _vast_enabled(), credit_fn=vast_credit, quote=quote)
    if not reasons:
        return None
    return (f"{ICON_WARN} <b>Not renting on Vast</b> — nothing was spent:\n"
            + "\n".join(f"• {_esc(reason)}" for reason in reasons))


def _journal_is_resumable(manifest_path: Path) -> bool:
    """Is the journal beside this manifest an unfinished batch of THIS job?

    The manifest path is one per chat (_job_manifest_path), so its journal
    outlives the job that wrote it. Resuming just because a journal existed
    attached a brand-new job to a batch that had finished two days earlier
    (reported live 2026-09-18: batch 2026-09-16-1706 came back with its two
    finished runs listed above the four new ones on the progress panel, the
    new runs writing into the old out/ directory, and deliver_result about to
    resend the old videos). Worse, a run id the old batch had finished would
    have been skipped outright by run_batch's "already done" check.

    So resume only when both hold:
      - every run in the journal is a run of the current manifest, so the
        journal was written for this job and not for an earlier one;
      - at least one of them is not done yet, so there is something left to
        continue.
    A journal with a batch id but no runs yet also counts: Phase A writes its
    batch id before the first Gemini call, and resuming it costs nothing.
    Everything else starts a new batch. The worst case is paying Gemini again
    for a job re-run unchanged after it finished, which is what a re-run is.
    """
    state = load_state(state_path_for(manifest_path))
    if not state.get("batch"):
        return False
    runs = state.get("runs") or {}
    if not runs:
        return True
    try:
        current = {run.id for run in load_manifest(manifest_path).runs}
    except (ManifestError, OSError, UnicodeDecodeError):
        return False
    if not set(runs) <= current:
        return False
    return any((entry or {}).get("status") != "done" for entry in runs.values())


def _start_phase_a_and_report(tg: Tg, chat_id: int, manifest_path: Path,
                              stages: list[str]) -> None:
    """Launch Phase A and say what it is about to cost.

    resume=True only when the journal is an unfinished batch of this same job
    (_journal_is_resumable): Phase A writes its batch id before the first
    Gemini call, so such a journal means try-ons may already be paid for, and
    resume is what makes them skipped rather than billed twice.
    """
    start_phase_a(manifest_path, resume=_journal_is_resumable(manifest_path))
    tg.send_message(
        chat_id,
        f"{ICON_ROCKET_CE} <b>Running the try-on over the API.</b>\n"
        "This spends Gemini quota, not GPU time — no pod is rented yet. When "
        "it finishes I will show live GPU stock and ask before spending "
        "anything.",
        parse_mode=PARSE_HTML)
    # phase="local" hands the progress message to tick_phase_a. Omitting it
    # leaves the message owned by tick_progress, which sees no lease and no
    # _RUNNING entry, concludes the batch finished, and delivers a
    # half-finished result instead of the rent panel.
    _start_progress(tg, chat_id, manifest_path, stages, phase="local")


def _do_phase_a(tg: Tg, chat_id: int, *, dry_run: bool) -> None:
    """The pre-spend half of _do_confirm: same guards, same manifest write,
    no pod and no confirm flag.

    Shares _do_confirm's guard order deliberately — migration_running first,
    then completeness, then the unanswered-file queue — so the two entry
    points cannot drift into accepting a job the other would refuse. What it
    does NOT do is freeze the panel or clear _STATE: Phase A is not a
    submission, and the spend decision still has to happen afterwards.

    `dry_run` makes this a total no-op, checked before anything else. It is a
    BOT-LEVEL testing switch — `make bot-dry` is documented as "One polling
    round … invoking no jobs" — and Phase A is a job: start_phase_a spawns
    `make drain … PHASE_A=1`, which calls Gemini and bills quota for real.
    Do not confuse it with drain.py's own --yes gate (the confirm flag only
    run.py may write, deliberately unspellable here — see its docstring),
    which is a REAL user's spend decision on a pod. Phase A runs independent
    of THAT gate (--phase-a-only returns above it, spec §6.1) — but that says
    nothing about whether the bot should have launched a child at all, which
    is the only question --dry-run asks. Keeping the parameter also lets
    _CB_RUN_GO thread it to both branches identically instead of remembering
    which of the two takes it.
    """
    if dry_run:
        tg.send_message(chat_id, "dry run — Phase A would spend Gemini quota, "
                                 "so nothing ran")
        return
    if migration_running():
        tg.send_message(chat_id, "a volume migration is in progress for this pod's "
                                 "datacenter — wait for it to finish before renting")
        return
    queued = _jobs_for(chat_id)
    if not queued:
        tg.send_message(chat_id, "no complete job yet — send the required files first")
        return
    pending = _PENDING.get(chat_id) or []
    if pending and chat_id not in _CONFIRM_WARNED:
        _CONFIRM_WARNED.add(chat_id)
        tg.send_message(chat_id,
                        f"{ICON_FLAG_CE} {len(pending)} file(s) still unassigned — answer "
                        f"them, or send /confirm again to run without them",
                        parse_mode=PARSE_HTML)
        return
    if not _manifest_write_ok(chat_id):
        # Which half of busy() is holding it decides the way OUT, not just the
        # wording, so this branches rather than naming /confirm unconditionally.
        #
        # Behind a DRAIN, /confirm really does still work: it writes the
        # mailbox and replies "Queued", and the job rides the pod already paid
        # for (queue-depth-1, 2026-09-02). [Run] used to reach _do_confirm and
        # get that for free; _do_phase_a cannot queue — a Phase A has no pod to
        # chain onto — so the least it can do is name the command that still
        # can, or a working feature looks removed rather than moved.
        #
        # Behind a PHASE A it does not, and saying so would dead-end the user:
        # _do_confirm's own guard turns exactly this state away with "the
        # try-on phase is still running … or /kill to stop it". Two refusals
        # pointing at each other is the `Đợi`-button-advising-/confirm-again
        # bug, so this one offers the same two real exits that one does.
        reason = _busy_reason(chat_id)
        if reason == _REASON_DRAIN:
            tg.send_message(chat_id, f"{reason} for this job — /status shows "
                                     "it. Run cannot queue behind it, but "
                                     "/confirm still can.")
        else:
            tg.send_message(chat_id, f"{reason} for this job — /status shows "
                                     "it. Wait for it to finish, or /kill to "
                                     "stop it, then tap Run again.")
        return
    live_path = _job_manifest_path(chat_id)
    write_manifest(queued, live_path, now=time.strftime("%Y-%m-%d %H:%M:%S"))
    stages: list[str] = []
    for other in queued:
        for stage in PIPELINES[other.pipeline]:
            if stage not in stages:
                stages.append(stage)
    _PHASE_A_OFFERED.pop(chat_id, None)
    _start_phase_a_and_report(tg, chat_id, live_path, stages)


def _do_confirm(tg: Tg, chat_id: int, *, dry_run: bool,
                phase_a_choice: str | None = None,
                gpu_provider: str | None = None) -> None:
    """THE money gate for a FRESH spend decision. The only OTHER function
    that may call start_drain is _do_resume, which continues a manifest
    already confirmed here once — see its own docstring for why that is not
    a second way to spend money the user has not agreed to.

    Extracted from the /confirm branch on 2026-08-31 when the Run button
    arrived. Two entry points must not mean two gates: every check below —
    phase_a_running, completeness, the unanswered queue, the validation
    verdict, and drain_running — has to apply identically whether the user
    typed /confirm or tapped a button, and the way to guarantee that is one
    body with two callers rather than two bodies that agree today.

    `phase_a_choice` makes this one body re-entrant for a SINGLE spend
    decision: the first entry stops at the chooser below and returns, and the
    tapped button calls it again with "reuse" or "rerun". The paragraph above
    is why that second entry re-runs every gate rather than trusting the first
    — `drain_running` in particular may have flipped in the minutes the
    chooser sat unanswered. What it does skip is write_manifest, and only on
    that second entry; see the comment there for why skipping the write is
    required rather than merely cheaper.

    `grep -rn "start_drain" scripts/tgbot/bot.py` must show exactly two call
    sites: this one, and _do_resume's.

    `gpu_provider` is "vast" when the panel's Vast tab minted the button, None otherwise. Vast has
    no Network Volume, so the migration guard below does not apply to it; and a NEW Vast rental
    (not a job queued onto a pod already running) must pass _vast_refusal, evaluated BEFORE the
    manifest is rewritten — that rewrite changes its mtime, which is the run token, and would kill
    the very panel the user is about to tap again after a refusal.
    """
    # Checked before anything else, including completeness — a migration mid-
    # copy is moving the Network Volume this pod would mount, so renting must
    # not be allowed to race it regardless of how complete the job is.
    if gpu_provider != "vast" and migration_running():
        tg.send_message(chat_id, "a volume migration is in progress for this pod's "
                                 "datacenter — wait for it to finish before renting")
        return
    # Second, ahead of completeness for the same reason the migration check is:
    # this is about the world, not about the job, and the job being perfect
    # does not make renting safe.
    #
    # Reachable only since [Run] started Phase A (2026-09-17): _do_phase_a
    # deliberately leaves _STATE intact — Phase A is not a submission — so the
    # draft is still here and /confirm is still tappable while the try-on child
    # runs. Verified by reproduction: without this, the `running` gate below
    # (drain_running, False for a Phase A) let it through to write_manifest
    # over the file that child is reading and then to start_drain WITH the
    # confirm flag — a second pod at $0.99/hour, two writers on one
    # state.json, and Gemini billed again for try-ons already in flight.
    #
    # phase_a_running, NOT busy(): busy() would also catch a live drain, and a
    # live drain is not a refusal here — it is the queue-depth-1 path
    # (2026-09-02), which puts this job in the mailbox and rides the pod
    # already paid for. A Phase A has no mailbox to queue into, so there is
    # nothing to offer but the wait. _do_resume, which has no queue path at
    # all, uses busy() for the same underlying reason.
    live_path = _job_manifest_path(chat_id)
    if phase_a_running(live_path):
        tg.send_message(chat_id,
                        "the try-on phase is still running for this job — wait "
                        "for it to finish, or /kill to stop it, then /confirm "
                        "again")
        return
    # `dry_run` is threaded from the caller (main()'s --dry-run; False for real
    # usage and for every call in this file's own tests) all the way to the one
    # start_drain below. The CLI flag has to actually reach that line, or
    # "--dry-run: never invokes drain" in this module's docstring would be
    # false — and the button path has to thread it just as far as the typed one.
    job = _STATE.get(chat_id)
    queued = _jobs_for(chat_id)
    if not queued:
        tg.send_message(chat_id, "no complete job yet — send the required files first")
        return
    pending = _PENDING.get(chat_id) or []
    if pending and chat_id not in _CONFIRM_WARNED:
        # /confirm used to succeed with files still queued and unanswered,
        # then drop them silently on the state clear below (finding I6,
        # 2026-08-31). Refuse once, naming the count; a second /confirm
        # runs without them, because "I meant the optional one to be
        # skipped" is a legitimate intent and there is no other way to
        # express it.
        _CONFIRM_WARNED.add(chat_id)
        tg.send_message(chat_id,
                        f"{ICON_FLAG_CE} {len(pending)} file(s) still unassigned — answer "
                        f"them, or send /confirm again to run without them",
                        parse_mode=PARSE_HTML)
        return

    # Ordered AFTER the pending check on purpose: the render below writes
    # the manifest, and writing one we are about to refuse to run is noise.
    validated = _LAST_VALIDATE.get(chat_id)
    if validated is None:
        # Never attempted — the only way to get here is the write guard in
        # _render_and_validate having refused while a drain was live
        # (finding B, 2026-08-31). The job was already complete at that
        # moment, so no further slot fill re-enters _maybe_show_manifest
        # and nothing would ever set this; the old code refused here with
        # "fix the error already shown" when no error had ever been shown,
        # and the only escape was re-sending a file, which nothing tells
        # the user. Attempt it now instead: by this point the drain has
        # normally finished, the write guard passes, and the normal path
        # resumes. If it has NOT finished, _render_and_validate says so
        # itself and names /status — a true reason with a real action.
        if not _render_and_validate(tg, chat_id):
            # It already sent the specific reason; a second, vaguer line
            # would only bury it.
            return
        # The confirmation screen was never shown for this job, so send
        # the manifest now: nothing may spend $0.99/hour without the exact
        # inputs it spent on being in the transcript.
        tg.send_message(chat_id,
                        _active_manifest_path(chat_id).read_text(encoding="utf-8"))
    elif not validated:
        # Attempted and failed. Refuse rather than trust a downstream
        # safety net: drain.py's own Phase A validate would likely catch
        # this before a pod is rented, but that file is read-only to this
        # bot, so this guard cannot rely on it (Task 7 fix round 1,
        # Finding 2). Retrying the validate here would be pointless — the
        # job has not changed since it failed.
        tg.send_message(chat_id,
                        "this manifest did not pass `make batch-validate`, and "
                        "its output was sent above — nothing will run. Fix what "
                        "it named and send the file(s) again.")
        return

    # A drain already running for this chat is no longer a refusal
    # (2026-09-02): it means THIS job goes into the mailbox instead of being
    # rented for on its own — drain.py's own chain_or_teardown claims it and
    # runs it on the same pod the instant the current job finishes, rather
    # than destroying and re-renting. The queue-depth-1 guard (a mailbox
    # already occupied) already ran inside _render_and_validate above.
    #
    # `live_path` is the one bound by the Phase A guard at the top, not a
    # fresh call: one name for one file through the whole function. Re-derived
    # here is what it used to be, and re-deriving a path a guard already acted
    # on is how the two can quietly stop being the same file.
    running = drain_running(live_path)
    if running and phase_a_choice is None:
        # _render_and_validate already checked the draft before it wrote the mailbox; the pod or
        # the enabled list may have changed since, so check again — and take the job back OUT of
        # the mailbox on a refusal, because a file left there would still be claimed.
        refusal = _vast_queue_refusal(chat_id, live_path)
        if refusal is not None:
            mailbox_path(live_path).unlink(missing_ok=True)
            tg.send_message(chat_id, refusal, parse_mode=PARSE_HTML)
            return
    if gpu_provider == "vast" and not running:
        refusal = _vast_refusal(_draft_manifest(chat_id))
        if refusal is not None:
            tg.send_message(chat_id, refusal, parse_mode=PARSE_HTML)
            return
    manifest_path = mailbox_path(live_path) if running else live_path
    # Re-written on the FIRST entry, even when `validated` was cached True: the
    # cache only remembers that the JOB CONTENT was valid, not which file it
    # was last written to. If a drain started in the seconds between the last
    # validate and this tap, `manifest_path` above just switched from the live
    # path to the mailbox, and the mailbox would otherwise sit empty — queued
    # in every OTHER sense but never actually written to disk. write_manifest
    # is a plain YAML dump, no subprocess, so redoing it here costs nothing.
    #
    # That empty-mailbox protection is FIRST-entry-only, on purpose: the second
    # entry skips the write below even when a drain has since appeared, and the
    # tail's `elif running:` branch reports that race honestly instead. Read
    # that branch before "restoring" the write here — writing the mailbox would
    # not make its "Queued." message true, it would buy a duplicate paid run.
    if phase_a_choice is None:
        # Skipped on the second entry, and not as an optimisation: _run_token
        # IS this file's mtime_ns, so rewriting it would invalidate the
        # chooser button the user just tapped. The bytes on disk are already
        # the ones the chooser was minted from.
        write_manifest(queued, manifest_path, now=time.strftime("%Y-%m-%d %H:%M:%S"))
    # Every stage any queued job will run, in pipeline order, de-duplicated.
    # The progress bar counts against this: a batch mixing two pipelines has to
    # show the union or the denominator would be wrong for half of it.
    stages: list[str] = []
    for other in queued:
        for stage in PIPELINES[other.pipeline]:
            if stage not in stages:
                stages.append(stage)
    # AFTER the write above, so the token minted here matches the manifest now
    # on disk; BEFORE _freeze_panel and before the state clear, because both
    # would claim a submission that has not happened yet. Freezing also strips
    # the Run keyboard from a job the user is still deciding about, and the
    # clear would leave the second entry with no job to confirm at all — it
    # would answer "no complete job yet", which reads as a lost draft.
    if not running and phase_a_choice is None:
        reusable, total = _preserved_tryon(manifest_path)
        if reusable:
            token = _run_token(chat_id)
            # Carries whatever provider _CB_RUN_GO's tap already named (now always explicit for a
            # newly minted RunPod button too, see _RUNPOD_SUFFIX) forward onto the chooser buttons
            # below — dropping it here would silently return to the .env fallback one screen later.
            suffix = f":{gpu_provider}" if gpu_provider else ""
            tg.send_message(
                chat_id,
                f"{ICON_ASK_CE} <b>Try-on already ran</b> for these exact inputs "
                f"({reusable}/{total} run(s)).\n"
                "Reusing it costs no Gemini quota. Re-running replaces those "
                "images and pays for them again.",
                parse_mode=PARSE_HTML,
                buttons=[[("Reuse — no Gemini spend", _CB_PHASE_A_REUSE + token + suffix,
                           _ce_id(ICON_OK_CE)),
                          ("Re-run try-on", _CB_PHASE_A_RERUN + token + suffix,
                           _ce_id(ICON_ROCKET_CE))]])
            return
    # BEFORE start_drain and before the state clear: this is the last instant
    # the submitted job exists in memory, and freezing the panel here is what
    # leaves the exact inputs permanently in the transcript.
    _freeze_panel(tg, chat_id, f"submitted {time.strftime('%H:%M')}")
    if not running:
        # THE money gate (see docstring) — the only line that may rent a pod.
        # Queuing (the `running` branch below) never reaches this: the
        # mailbox file alone is drain.py's signal, claimed by the process
        # already running, on the pod already paid for.
        #
        # resume is True only when the chooser ran: phase_a_choice is set
        # exactly when a journal with reusable try-on exists, and resume is
        # what makes that try-on skipped rather than paid for twice.
        #
        # A stale button answering after the journal vanished lands in
        # resolve_batch_id's own "RESUME=1 but nothing to continue" branch
        # (batch_run.py:73) and runs as a new batch. That branch DOES report
        # itself — but `decision.note` is printed to the drain's stdout
        # (batch_run.py:148), which is the drain log on the pod, not this chat.
        # The user reads "🚀 Started." below and pays for a try-on they asked to
        # reuse. So it is reported, not silent, and still not visible to the
        # only person who could act on it.
        #
        # Left that way on purpose, not overlooked: the window needs the journal
        # deleted between the two entries, and closing it means re-reading
        # _preserved_tryon here — a second journal read on the money path, the
        # same duplication already flagged at _preserved_tryon — plus a new
        # user-visible message that would need its own test. Revisit together
        # with that finding, not separately.
        # See _do_resume's identical line — every caller here now names its provider explicitly.
        provider_kwargs = {} if gpu_provider is None else {"gpu_provider": gpu_provider}
        start_drain(manifest_path, dry_run=dry_run,
                    resume=phase_a_choice is not None,
                    force_local=phase_a_choice == "rerun",
                    **provider_kwargs)
    # Clear in-memory state so the next file starts a fresh job rather
    # than mutating one already handed to a running drain. The manifest
    # itself, and the drain's own journal, stay on disk regardless.
    dropped = len(_PENDING.pop(chat_id, []) or [])
    submitted_count = len(queued)
    _BASKET.pop(chat_id, None)
    # Copied to `.last.json` BEFORE the clear, so /again can rebuild it.
    # Deliberately not left in `.draft.json`: _load_draft reads that file, so a
    # restart would resurrect a job already handed to a running drain.
    # The whole batch, not just the last job: /again exists so a batch can be
    # re-run with one thing changed, and restoring one run out of four would
    # quietly discard the other three.
    _last_path(chat_id).write_text(json.dumps(
        {"jobs": _dump_jobs(queued)}, indent=2), encoding="utf-8")
    _STATE.pop(chat_id, None)
    _LAST_VALIDATE.pop(chat_id, None)
    _CONFIRM_WARNED.discard(chat_id)
    _ALBUM_KEY.pop(chat_id, None)
    _FIDELITY.pop(chat_id, None)
    _STRIP.pop(chat_id, None)
    if dropped:
        tg.send_message(chat_id, f"running without {dropped} unassigned file(s)")
    if running and phase_a_choice is None:
        tg.send_message(chat_id,
                        f"📥 <b>Queued.</b> {submitted_count} job(s) will start "
                        "automatically on the same pod the moment the current "
                        "job finishes — no extra rental. /status shows both.",
                        parse_mode=PARSE_HTML)
        # No progress message yet — tick_progress starts one itself once
        # drain.py's handoff file says this was actually picked up. Sending
        # one now would claim progress on a job that has not started.
    elif running:
        # The race: a drain appeared on this manifest while the chooser sat
        # unanswered. Reachable three ways, and the three co-occur by
        # construction — the stock-out card that offers Retry / Switch-GPU
        # exists exactly when Phase A finished and the journal holds a
        # preserved try-on, which is exactly when the chooser appears:
        # _CB_RECOVER_RETRY and _CB_RECOVER_SWITCH both _do_resume THIS file
        # (its stem is _job_manifest_path's), and so does
        # tick_migration_progress's resume-on-done, which needs no tap at all.
        # _run_token does not catch it: it is the LIVE path's mtime_ns, and
        # nothing in drain.py or the runner rewrites the manifest — they write
        # the .state.json journal.
        #
        # "Queued." above would be a lie here, because it describes the mailbox
        # file this branch deliberately did NOT write. Making it true is the
        # expensive mistake rather than the fix: claim_mailbox renames the
        # mailbox to a fresh stem (handoff.py:78) and chain_or_teardown runs
        # that stem with neither --resume nor --force-local (drain.py:236), so
        # state_path_for yields a fresh journal and resolve_batch_id mints a
        # fresh batch id — a second full GPU run of the same video AND a second
        # Gemini try-on payment, whichever button was tapped, then announced as
        # a feature. The mailbox cannot carry force_local at all.
        #
        # Nothing was lost, though: the running drain was started with
        # resume=True on this very manifest, so the try-on IS being reused and
        # the video does get made. What differs by choice is whether that is
        # what the user asked for, so the two get different text. The
        # _freeze_panel and the _STATE clear above both stay correct on this
        # branch — the job genuinely is running, and a draft left in _STATE
        # would let a later /confirm submit it a second time.
        if phase_a_choice == "rerun":
            tg.send_message(
                chat_id,
                f"{ICON_WARN} <b>Already running.</b> A drain picked this batch "
                "up while you were deciding, so it is running now with the "
                "try-on <b>reused</b>. Your re-run could not be applied — a "
                "batch already on a pod cannot be told to redo its try-on.\n"
                "When it finishes: /again, then Run, and choose <b>Re-run "
                "try-on</b> there.",
                parse_mode=PARSE_HTML)
        else:
            tg.send_message(
                chat_id,
                f"{ICON_WARN} <b>Already running.</b> A drain picked this batch "
                "up while you were deciding, so it is running now with the "
                "try-on <b>reused</b> — which is the choice you made. No second "
                "rental, and no second Gemini call.",
                parse_mode=PARSE_HTML)
    else:
        if gpu_provider == "vast":
            quote = vast_last_quote()
            rate = f"about ${quote.dph:.2f}/hour (quoted)" if quote is not None else "Vast's hourly rate"
            where = f"on Vast.ai at {rate}"
        else:
            where = "on one pod at $0.99/hour"
        tg.send_message(chat_id,
                        f"{ICON_ROCKET_CE} <b>Started.</b> {submitted_count} job(s) {where}."
                        "\nI will keep the message below updated and "
                        "send the results when it finishes — no need to ask.",
                        parse_mode=PARSE_HTML)
        _start_progress(tg, chat_id, manifest_path, stages,
                        **({} if gpu_provider is None else {"gpu_provider": gpu_provider}))
    return


def _handle(tg: Tg, update: dict, *, allowed_user_id: int,
           dry_run: bool = False) -> None:
    if not allowed(update, allowed_user_id):
        return                              # silent: do not confirm the bot exists
    query = update.get("callback_query")
    if query:
        # Before `update["message"]` below, which a button press does not have
        # in that shape (see _identify).
        _, chat_id = _identify(update)
        _handle_callback(tg, chat_id, query, dry_run=dry_run)
        return
    msg = update["message"]
    chat_id = msg["chat"]["id"]
    # How far the panel has drifted from the bottom of the chat is measured
    # from this: in a private chat, message ids increment by one per message,
    # so `newest - panel` is a message count, not an estimate. Recorded before
    # anything below can draw the panel. max() because updates can be replayed
    # after a restart (the offset is not persisted) and drift must not go
    # backwards and pin a panel that has really scrolled away.
    _LAST_SEEN[chat_id] = max(_LAST_SEEN.get(chat_id, 0), msg.get("message_id") or 0)

    # Accepting any of these would put a silently degraded input into a
    # $0.99/hour render, with no answer this bot could ever give — there is
    # nothing to probe on a sticker or a voice note. See NON_FILE_MEDIA.
    kind = next((k for k in NON_FILE_MEDIA if msg.get(k)), None)
    if kind:
        cost = _RECOMPRESSION_COST.get(kind)
        why = (f"measured 2026-08-31, {cost}" if cost
               else "Telegram re-encodes everything sent outside the File path")
        # Naming "Send as File" matters more than naming the paperclip:
        # verified 2026-08-31 that the iOS picker offers it, so the fix costs
        # one extra tap and the user never has to leave Photos. Telling them to
        # use Files instead sends them off to save the picture somewhere first,
        # which is the friction that makes the whole rule feel arbitrary.
        tg.send_message(chat_id,
                        f"That arrived as a {kind}, not a File — {why}.\n"
                        'Send it again as a File: in the picker tap "..." and '
                        'choose "Send as File", or attach it with the '
                        "paperclip -> File.")
        return

    doc = msg.get("document")
    photo = msg.get("photo")
    video = msg.get("video")
    # photo/video are accepted despite the recompression measured above —
    # relaxed 2026-09-03 (see NON_FILE_MEDIA's comment). `recompressed` names
    # which one, purely so the warning after staging can quote the right cost;
    # it changes nothing else about how the file is handled from here on.
    recompressed: str | None = None
    if video:
        media, file_name = video, video.get("file_name")
        recompressed = "video"
    elif photo:
        # Every size Telegram generated for this photo, smallest first — take
        # the largest so an accepted photo is at least the best copy Telegram
        # kept, never the ~0.6%-of-original default. Photos carry no
        # file_name; _stage_file falls back to the getFile path's own name.
        media, file_name = max(photo, key=lambda ps: ps.get("file_size") or 0), None
        recompressed = "photo"
    else:
        media, file_name = doc, (doc.get("file_name") if doc else None)

    if media:
        # Every step from getFile to probe is inside this try (finding I1,
        # 2026-08-31). Only probe() used to be: a TgError from getFile, a
        # KeyError on a missing file_path, and every carefully worded message
        # to_png_if_heic raises ("ffmpeg is not installed", "reported success
        # but wrote no file") all escaped handle(), were swallowed by main()'s
        # `except Exception: log(...)`, and the user got nothing back at all.
        # OSError is in the list because _stage_file's copy is the first thing
        # that touches the host filesystem — it raises RuntimeError itself so
        # the token in the source path can never reach the reply, but a
        # mkdir/stat on the way there can still surface as a plain OSError.
        # Before the slow part, not after: ffprobe on a 25MB video plus the
        # staging copy is long enough that a silent bot reads as a stuck one.
        # A spinner rather than send_chat_action (2026-09-02): Telegram's own
        # "uploading" indicator disappears after ~5s and nothing here ever
        # refreshed it, so anything slower than that read as stuck again.
        try:
            with _spinner(tg, chat_id, f"checking {file_name or 'the file'}"):
                src = Path(tg.call("getFile", file_id=media["file_id"])["file_path"])
                path = _stage_file(chat_id, src, file_name)
                path = to_png_if_heic(path)
                p = probe(path)
                # Inside the try, not one line below it (finding D, 2026-08-31).
                # Its open()/stat() on a just-written file is near-certain to
                # succeed, but "near-certain" was the whole of the guarantee: an
                # OSError here escaped handle() into main()'s blanket
                # `except Exception: log(...)` and the user got nothing back —
                # exactly the silence finding I1 existed to remove.
                # Acceptance A6 compares this against the delivered file's digest.
                fidelity = _fidelity_line(path)
                _FIDELITY.setdefault(chat_id, {})[str(path)] = fidelity
        except (RuntimeError, TgError, KeyError, OSError) as exc:
            # ffprobe raises rather than guessing (ingest.probe's own
            # contract) — the file never enters a job, so it can never
            # silently pick a wrong preset or a wrong slot.
            tg.send_message(chat_id, f"that file was not accepted: {exc}")
            return

        if recompressed:
            cost = _RECOMPRESSION_COST.get(recompressed)
            # _esc on a module constant looks redundant until you read the
            # video measurement: it quotes "13,196 -> 6,603 kbps", and a bare
            # ">" in a parse_mode=HTML body makes Telegram reject the WHOLE
            # message — the user would get no warning at all, which is worse
            # than the literal <tg-emoji> tag this parse_mode was added to fix.
            tg.send_message(chat_id,
                            f"{ICON_WARN} that arrived as a {recompressed}, not a File — "
                            f"measured 2026-08-31, {_esc(cost)}. Accepted anyway; "
                            "send it again as a File if this run doesn't come "
                            "out right.",
                            parse_mode=PARSE_HTML)

        _CONFIRM_WARNED.discard(chat_id)

        job = _job_for(chat_id)
        role = slot_for(p, job)
        if role is None:
            # Ambiguous (an image): job.slot_for already refuses to guess —
            # queue it and ask, rather than reintroducing a filename
            # heuristic here. The answer arrives as a later plain-text
            # message. Queued, not overwritten: a phone naturally attaches
            # several images in one send, which arrive as consecutive
            # updates with no chance to answer in between (see _PENDING).
            queue = _PENDING.setdefault(chat_id, [])
            queue.append((path, p))
            # The panel carries the "waiting for a label" line, so it has to
            # move for a queued file too — otherwise the one screen the user
            # reads goes stale precisely when the state got more complicated.
            if len(queue) == 1:
                _show_panel(tg, chat_id)
                _ask_about(tg, chat_id, p, job.pipeline, path=path)
            else:
                # Only the head is ever asked about, so a second image gets
                # acknowledged on the panel instead of in a message of its own
                # — the panel already lists everything waiting for a label, and
                # a duplicate line per queued file is the wall this replaced.
                _show_panel(tg, chat_id,
                            note=f"queued {path.name} — answer the question above first")
            return

        # A video: structural, always `driver` — no question needed.
        _fill_slot(tg, chat_id, job, role, path, p)
        _maybe_show_manifest(tg, chat_id, job)
        return

    text = msg.get("text") or ""

    tiktok_url = tiktok.find_url(text)
    if tiktok_url:
        # Checked before every /command below AND before the _PENDING
        # fallthrough at the end of this function (2026-09-04): a pasted
        # link must win over "reply character/outfit/background" the same
        # way an uploaded video does — job.slot_for is structural for
        # kind=="video", no question ever attaches to it.
        #
        # Not `_spinner`: that thread redraws a fixed label on a timer and
        # has no channel for yt-dlp's own percentage, which is the whole
        # reason this got its own progress message instead of reusing it.
        message_id = tg.send_message(chat_id, "⬇️ downloading tiktok video… 0%")
        last_shown = 0.0
        last_edit_ts = 0.0

        def on_progress(pct: float) -> None:
            nonlocal last_shown, last_edit_ts
            now = time.monotonic()
            # Throttled on BOTH size and time (2026-09-04): yt-dlp reports a
            # new percentage many times a second, and editMessageText has
            # the same real-world throttling _spinner's own docstring
            # measured — an edit every frame would burn through it for a
            # number nobody can read that fast anyway.
            if (pct - last_shown < 5.0) and (now - last_edit_ts < 2.0):
                return
            last_shown, last_edit_ts = pct, now
            try:
                tg.edit_message(chat_id, message_id,
                                f"⬇️ downloading tiktok video… {pct:.0f}%")
            except TgError as exc:
                log(f"tiktok progress edit throttled, skipping: {exc}")

        try:
            tmp_path = tiktok.download(tiktok_url, on_progress=on_progress)
        except (RuntimeError, OSError) as exc:
            tg.edit_message(chat_id, message_id,
                            f"couldn't download that TikTok video: {exc}")
            return

        try:
            path = _stage_file(chat_id, tmp_path, f"tiktok-{int(time.time())}.mp4")
            p = probe(path)
            fidelity = _fidelity_line(path)
            _FIDELITY.setdefault(chat_id, {})[str(path)] = fidelity
        except (RuntimeError, TgError, KeyError, OSError) as exc:
            tg.edit_message(chat_id, message_id, f"that file was not accepted: {exc}")
            return
        finally:
            # tiktok.download() only cleans up after itself when IT raises
            # (its own contract) — on success the caller owns the temp dir,
            # and this is the only path that ever reaches here with one.
            shutil.rmtree(tmp_path.parent, ignore_errors=True)

        tg.delete_message(chat_id, message_id)
        _CONFIRM_WARNED.discard(chat_id)
        job = _job_for(chat_id)
        role = slot_for(p, job)   # a video: structural, always `driver`
        _fill_slot(tg, chat_id, job, role, path, p)
        _maybe_show_manifest(tg, chat_id, job)
        return

    if text.startswith("/tryon"):
        # On request, not by default: sending 01-tryon.png with every job would
        # be noise, but when the final video looks wrong, what try-on produced
        # is the first thing worth checking, and this makes it one tap rather
        # than an SSH session.
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            tg.send_message(chat_id,
                            "usage: /tryon <batch-id>  (the id progress showed, "
                            "e.g. 2026-08-31-2140)")
            return
        # `<batch>` or `<batch>/<run>`. Split first so each component is
        # validated on its own by _safe_child and neither can smuggle a path.
        target, _, wanted_run = parts[1].partition("/")
        batch_dir = _safe_child(ROOT / "out", target)
        if batch_dir is None:
            # Refuse without echoing the argument back: reflecting whatever
            # was typed into the reply is how a refusal message becomes its
            # own small problem.
            tg.send_message(chat_id,
                            "that batch id is not allowed — send a bare id, "
                            "no path separators or '..'")
            return
        # Every run in the batch, not a hardcoded "job" (2026-09-01). Manifests
        # used to have exactly one run so the id was a constant; a batch has
        # one per queued job, named after its material, and a /tryon that still
        # looked for `runs/job/` would answer "no try-on image found" for every
        # batch the bot now produces.
        runs_dir = batch_dir / "runs"
        if wanted_run:
            one = _safe_child(runs_dir, wanted_run)
            found = [one / "01-tryon.png"] if one and (one / "01-tryon.png").exists() else []
        else:
            found = sorted(runs_dir.glob("*/01-tryon.png")) if runs_dir.is_dir() else []
        if not found:
            tg.send_message(chat_id, "no try-on image found for that batch")
            return
        if len(found) > TRYON_MAX_SENT:
            names = "\n".join(f"• <code>{_esc(f.parent.name)}</code>" for f in found)
            tg.send_message(chat_id,
                            f"that batch has {len(found)} runs — name one:\n{names}\n"
                            f"<i>/tryon {_esc(batch_dir.name)}/&lt;run&gt;</i>",
                            parse_mode=PARSE_HTML)
            return
        for image in found:
            tg.send_document(chat_id, image, caption=f"try-on · {image.parent.name}")
        return

    if text.startswith("/result"):
        parts = text.split(maxsplit=1)
        if len(parts) == 2:
            manifest_path = _safe_child(ROOT / "batch", parts[1])
            if manifest_path is None:
                tg.send_message(chat_id,
                                "that manifest name is not allowed — send a "
                                "bare filename under batch/, no path "
                                "separators or '..'")
                return
        else:
            # Bare /result (the Result button sends exactly this): the same
            # manifest /status already defaults to for this chat, so the
            # button works without the user typing a filename.
            manifest_path = _job_manifest_path(chat_id)
        if not manifest_path.exists():
            if len(parts) == 2:
                tg.send_message(chat_id, "no manifest found with that name")
            else:
                tg.send_message(chat_id,
                                "nothing finished yet for this chat — or "
                                "name a manifest: /result 2026-08-31-2140.yaml")
            return
        deliver_result(tg, chat_id, manifest_path)
        return

    if text.startswith("/status"):
        # The plan (Task 5) specifies "progress is one edited message,
        # re-rendered about every 30 seconds", and progress_text was built and
        # tested for it — but nothing ever called it, so after /confirm the
        # user got one message and then silence for 12+ minutes with no way to
        # ask whether it was running or dead (finding I3, 2026-08-31). This is
        # the cheap half: pull, not push, over the same already-tested
        # renderer. The timed edit loop is still unwired.
        manifest_path = _job_manifest_path(chat_id)
        if not manifest_path.exists():
            # Not a dead end (2026-08-31). "nothing started" is true but
            # useless while a job is being assembled, which is most of the
            # time /status gets asked. Answer the question actually being put.
            if _STATE.get(chat_id) is None:
                tg.send_message(chat_id, f"💤 <b>Nothing running.</b>\n\n"
                                         f"{NOTHING_ASSEMBLED}",
                                parse_mode=PARSE_HTML)
            else:
                # The panel itself, moved down, rather than a second copy of it
                # here: two live keyboards for one job is how a tap lands on
                # the wrong one.
                _show_panel(tg, chat_id, bump=True,
                            note="nothing running yet — this is what you are "
                                 "assembling")
            return
        # The same renderer the auto-updating message uses, so /status can
        # never disagree with what is already on screen.
        stages = None
        phase = None
        payload: dict = {}
        prog = _progress_path(chat_id)
        if prog.exists():
            try:
                payload = json.loads(prog.read_text(encoding="utf-8"))
                stages = payload.get("stages")
                # Without this, a Phase A renders "waiting for the pod —
                # nothing recorded yet": no lease plus an empty journal is the
                # combination that used to mean exactly one thing, and Phase A
                # broke it. The message on screen says the try-on is running,
                # so /status saying the pod is coming is the disagreement the
                # comment above promises cannot happen.
                phase = payload.get("phase")
            except ValueError:
                stages = None
        tg.send_message(chat_id,
                        progress_text(manifest_path,
                                      lease=lease_for(manifest_path),
                                      stages=stages, phase=phase,
                                      **_billing_kwargs(payload)),
                        parse_mode=PARSE_HTML)
        return

    if text.startswith("/confirm"):
        _do_confirm(tg, chat_id, dry_run=dry_run)
        return

    if text.startswith("/job"):
        job = _STATE.get(chat_id)
        if job is None:
            tg.send_message(chat_id, NOTHING_ASSEMBLED, parse_mode=PARSE_HTML)
        else:
            # _maybe_show_manifest, not _show_panel directly: `_LAST_VALIDATE`
            # is deliberately memory-only (see `_save_draft`'s docstring) and
            # every deploy restarts motion-bot, so a chat reopened after a
            # restart has slots/basket restored from disk but no verdict —
            # `_sheet_for` then refuses to draw the material preview at all.
            # Every OTHER panel redraw already re-validates first (or just
            # invalidated the job itself); /job going straight to `_show_panel`
            # was the one path that skipped it, so a restart silently dropped
            # previews for jobs the user had already finished assembling.
            # bump: /job is an explicit "show me now", and a silent edit to a
            # message somewhere above would look like the command did nothing.
            _maybe_show_manifest(tg, chat_id, job, bump=True)
        return

    if text.startswith("/clear"):
        if _STATE.get(chat_id) is None and not (_PENDING.get(chat_id) or []):
            tg.send_message(chat_id, "nothing to clear")
            return
        _ask_to_clear(tg, chat_id)
        return

    if text.startswith("/wipe"):
        _ask_to_wipe(tg, chat_id)
        return

    if text.startswith("/gpu"):
        _report_gpu_stock(tg, chat_id)
        return

    if text.startswith("/balance"):
        _report_balance(tg, chat_id)
        return

    if text.startswith("/subscribe"):
        _offer_gpu_sub_targets(tg, chat_id)
        return

    if text.startswith("/unsubscribe"):
        _list_gpu_subs(tg, chat_id)
        return

    if text.startswith("/kill"):
        _ask_kill(tg, chat_id)
        return

    if text.startswith("/again"):
        _again(tg, chat_id)
        return

    if text.startswith("/pipeline"):
        job = _job_for(chat_id)
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            _offer_pipelines(tg, chat_id)
            return
        _switch_pipeline_and_report(tg, chat_id, parts[1].strip())
        return

    if text.startswith("/provider"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            _offer_providers(tg, chat_id)
            return
        _switch_provider_and_report(tg, chat_id, parts[1].strip())
        return

    if text.startswith("/start"):
        tg.send_message(
            chat_id,
            f"{ICON_SPEAK_CE} <b>Here's how this works:</b>\n\n"
            f"{ICON_CLIP_CE} <b>File, not Photo</b> — picker → \"...\" → Send as File\n"
            "🎬 Videos are the driver. For images, I'll ask — just tap.\n\n"
            f"{ICON_OK_CE} Full job shown before anything runs.\n"
            f"{ICON_SPEND_CE} <b>Nothing rents a GPU until you tap Run and confirm.</b>\n"
            "A batch with API try-on spends Gemini quota first, before any pod "
            "exists — you get asked about the GPU afterwards, with live stock.\n\n"
            "Buttons below, or type the commands.",
            parse_mode=PARSE_HTML,
            reply_keyboard=START_KEYBOARD)
        return

    # A plain-text reply, meant to answer the question asked about the HEAD
    # of the pending queue (never any other queued file — only the head is
    # ever asked about). Anything not a recognised slot name is re-asked,
    # never guessed — mirrors job.slot_for's own refusal to guess.
    queue = _PENDING.get(chat_id)
    if queue:
        job = _job_for(chat_id)
        options = _askable_roles(job.pipeline)
        answer = text.strip().lower()
        if answer in options:
            _answer_slot(tg, chat_id, answer)
        else:
            tg.send_message(chat_id, f"didn't recognise that — reply one of: "
                            f"{' / '.join(options)}")


# Registered with Telegram at startup so typing "/" offers them instead of
# requiring the user to remember. Descriptions are what shows in that menu, so
# the money one has to say so there — the menu is where a tap originates.
BOT_COMMANDS = [
    ("start", "what this bot does and the commands"),
    ("job", "what is assembled so far, and what is missing"),
    ("pipeline", "show or switch the pipeline"),
    ("provider", "show or switch who runs try-on (self-host GPU vs API)"),
    ("again", "reuse the last job's files, e.g. with another pipeline"),
    ("clear", "throw away the job being assembled"),
    ("status", "progress of this chat's job"),
    ("confirm", "SPENDS MONEY - rents a GPU at $0.99/h and starts (Run may ask first)"),
    ("result", "the finished video, or the failure logs"),
    ("tryon", "just the try-on image, when the result looks wrong"),
    ("wipe", "delete every message in this chat, yours and mine"),
    ("gpu", "check RunPod 5090 stock before you rent — free"),
    ("balance", "RunPod balance and how many GPU hours it buys — free"),
    ("subscribe", "get a message the moment a GPU has stock in a region"),
    ("unsubscribe", "stop watching a GPU/region — list and remove"),
    ("kill", "EMERGENCY STOP - destroys the pod right now, abandons the run"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = env_get(ROOT / ".env", "TG_BOT_TOKEN")
    raw_id = env_get(ROOT / ".env", "TG_ALLOWED_USER_ID")
    base = env_get(ROOT / ".env", "TG_API_BASE") or "http://127.0.0.1:8081"
    if not token or not raw_id:
        print("TG_BOT_TOKEN and TG_ALLOWED_USER_ID must be set in .env", file=sys.stderr)
        return 2
    # Validated here, loudly, rather than at first use: an unknown pipeline
    # name in .env would otherwise surface as a confusing manifest-validation
    # failure on the phone, hours later, after material was already uploaded.
    global _DEFAULT_PIPELINE, _DEFAULT_PROVIDER
    configured = env_get(ROOT / ".env", "TG_PIPELINE")
    if configured:
        if configured not in PIPELINES:
            print(f"TG_PIPELINE={configured!r} is not a known pipeline. "
                  f"Known: {', '.join(sorted(PIPELINES))}", file=sys.stderr)
            return 2
        _DEFAULT_PIPELINE = configured

    configured_provider = env_get(ROOT / ".env", "TG_PROVIDER")
    if configured_provider:
        if configured_provider not in PROVIDER_LABELS:
            print(f"TG_PROVIDER={configured_provider!r} is not a known "
                  f"provider. Known: {', '.join(sorted(PROVIDER_LABELS))}",
                  file=sys.stderr)
            return 2
        _DEFAULT_PROVIDER = configured_provider

    tg = _track_sends(Tg(token=token, base_url=base))
    allowed_user_id = int(raw_id)
    offset = 0
    # Best-effort: a failure here costs a menu, not the bot. Raising would stop
    # a working bot from starting over a cosmetic call.
    try:
        tg.call("setMyCommands", commands=[{"command": c, "description": d}
                                          for c, d in BOT_COMMANDS])
    except TgError as exc:
        log(f"setMyCommands failed, continuing without the menu: {exc}")
    log(f"started, api={base}, dry_run={args.dry_run}, pipeline={_DEFAULT_PIPELINE}")
    while True:
        try:
            # The long-poll IS the progress message's own refresh timer
            # (2026-09-01). While a drain is running the poll shortens to 2s so
            # the loop comes round often enough to keep run._elapsed() — the
            # real, still-ticking mm:ss that proves the process is alive —
            # roughly in step with wall-clock time; the rest of the time it
            # stays at 50s, which costs one request per 50s and no timer of
            # its own.
            # Keyed on the progress file rather than a flag, so a bot restarted
            # mid-render picks the fast cadence straight back up.
            animating = _progress_path(allowed_user_id).exists()
            for update in tg.get_updates(
                    offset, timeout=(_POLL_ANIMATED_SEC if animating
                                     else _POLL_IDLE_SEC)):
                offset = update["update_id"] + 1
                handle(tg, update, allowed_user_id=allowed_user_id,
                       dry_run=args.dry_run)
            # After the updates, not instead of them.
            # One chat, because the allowlist is one user (spec section 2).
            # tick_phase_a sits next to tick_progress for readability — the two
            # read the same _progress_path. Order is not what makes that safe:
            # the `phase` guard in each tick is, and they would be correct in
            # either order.
            tick_progress(tg, allowed_user_id)
            tick_phase_a(tg, allowed_user_id, dry_run=args.dry_run)
            tick_migration_progress(tg, allowed_user_id, dry_run=args.dry_run)
            _tick_gpu_subs(tg, allowed_user_id)
            _tick_staging_prune()
            _tick_out_prune(tg, allowed_user_id)
        except TgError as exc:
            log(f"poll failed, continuing: {exc}")
            time.sleep(5)
        except Exception as exc:            # one bad update must not end the bot
            log(f"update failed, continuing: {exc!r}")
        if args.once:
            return 0


if __name__ == "__main__":
    sys.exit(main())
