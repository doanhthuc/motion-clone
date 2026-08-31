#!/usr/bin/env python3
"""The bot loop. Thin: dispatch, per-chat state, and wiring — no logic.

    python3 scripts/tgbot/bot.py            # long-poll forever
    python3 scripts/tgbot/bot.py --once     # one getUpdates round, for testing
    python3 scripts/tgbot/bot.py --dry-run  # never invokes drain

Reads TG_BOT_TOKEN, TG_ALLOWED_USER_ID and TG_API_BASE from the root .env.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.config import env_get
from batchlib.manifest import load_state, state_path_for
from batchlib.pipelines import optional_roles, required_roles
# Not `from batchlib_ext...` or `scripts/batchlib/...` — drain.py itself lives
# at scripts/drain.py, a plain top-level module, same as batch_run.py. scripts/
# is already on sys.path (the insert above), so this is the plan's own "import
# it, do not reimplement" for failed_job_ids rather than re-deriving "did this
# run fail" from state.json by hand a second time.
from drain import failed_job_ids
# Absolute, NOT `from .tgclient import ...`. This file runs as
# `python3 scripts/tgbot/bot.py`, i.e. as __main__, where a relative import
# raises ImportError regardless of sys.path. The insert above puts scripts/ on
# the path, which is what makes the absolute form work from either entry point.
from tgbot.tgclient import Tg, TgError
from tgbot.ingest import Probe, describe, probe, to_png_if_heic
from tgbot.job import Job, missing_slots, slot_for, write_manifest
from tgbot.run import (drain_running, estimate_minutes, final_files, lease_for,
                       progress_text, start_drain, summary_text)

ROOT = Path(__file__).resolve().parents[2]

# `make batch-validate` and the Makefile it lives in are only ever at the real
# repo root, never wherever a test points ROOT (path-safety tests above
# reassign `bot.ROOT` to a tempdir). A separate, never-reassigned constant —
# same reasoning as tgbot/run.py's own ROOT for LEASE_PATH.
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Every manifest this bot renders (tgbot/job.py:render_manifest) hardcodes a
# single run id, "job" — Plan 2A is the single-job slice, multi-job baskets
# are Plan 2B. Hardcoding it here matches that, rather than inventing a run
# selector for a run that never has more than one name.
SOLE_RUN_ID = "job"

# The one pipeline this bot assembles a job for. Plan 2A is the single-job
# slice — a recipe picker (batch/recipes/*.yaml) is explicitly 2B's job (see
# task-7-brief.md "Notes for Plan 2B"). tryon-motion-enhance is the pipeline
# whose required+optional materials are exactly the spec's "four labelled
# slots" (character, outfit, background, driver) — docs/superpowers/specs/
# 2026-08-30-telegram-batch-control-design.md section 1.
JOB_PIPELINE = "tryon-motion-enhance"

# Per-chat state, in memory only — Plan 2A is one job at a time per chat. A
# bot restart loses an unsubmitted draft, never a running job: drain_running()
# also consults the on-disk lease (tgbot/run.py), which is the durable half.
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

# Every message kind that is NOT the File path, and so must be refused.
#
# Measured 2026-08-31, sending out/2026-08-23-1540/_final/nhanvat1__dandong-3.mp4
# from the iPhone client twice. As a File: 24,558,897 bytes in, 24,558,897 out,
# sha256 identical. Through the Photo/Video tab with its "1080p" option: the
# frame (1088x1920) and the frame count (444) both survive untouched, but the
# bitrate is HALVED — 13,196 -> 6,603 kbps — and 49.8% of the bytes are gone.
# So the loss this refusal prevents is not a smaller picture, it is every frame
# re-compressed: invisible in a file listing, invisible in a still, and exactly
# what this repo's chroma/exposure/jitter measurements are sensitive to.
#
# `video` is why this list exists rather than a bare `msg.get("photo")` check.
# The Photo/Video tab is the iOS default and its "1080p" label reads as
# lossless, so it is the first mistake a real user makes — it was the first
# mistake made while measuring the above. A `video` matched neither the photo
# branch nor the document branch, fell through to the text handlers as "",
# matched no command, and the bot replied NOTHING AT ALL. Silence on the most
# likely wrong move is worse than the quality loss it hides.
NON_FILE_MEDIA = ("photo", "video", "animation", "audio",
                  "voice", "video_note", "sticker")

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
    msg = update.get("message") or {}
    sender = msg.get("from") or {}
    chat = msg.get("chat") or {}
    return sender.get("id") == allowed_user_id and chat.get("id") == allowed_user_id


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
    return f"{path.stat().st_size} bytes\nsha256 {digest.hexdigest()}"


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
        tg.send_message(chat_id, f"no batch recorded yet for {manifest_path.name}")
        return
    batch_dir = ROOT / "out" / batch_id

    for path in final_files(batch_dir):
        tg.send_document(chat_id, path, caption=path.name)

    # On failure, attach exactly what scripts/drain.py's teardown() already
    # pulled onto local disk BEFORE destroying the pod. Never reach for the
    # pod here — by the time this runs it is normally already gone.
    for run_id, _job_id in failed_job_ids(state):
        run_dir = batch_dir / "runs" / run_id
        for name in ("pod-job.log", "run.log"):
            log_path = run_dir / name
            if log_path.exists():
                tg.send_document(chat_id, log_path, caption=f"{run_id}/{name}")

    tg.send_message(chat_id, summary_text(batch_dir))


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


def _askable_roles(pipeline: str) -> list[str]:
    """Slot names a user can be asked to name — every material role except
    `driver`, which is structural (job.slot_for never asks about a video)."""
    return sorted((required_roles(pipeline) | optional_roles(pipeline)) - {"driver"})


def _ask_about(tg: Tg, chat_id: int, p: Probe, pipeline: str, extra: str = "") -> None:
    """Ask which slot a parked, ambiguous file belongs to."""
    options = " / ".join(_askable_roles(pipeline))
    body = f"{describe(p)}\n{extra}" if extra else describe(p)
    tg.send_message(chat_id, f"{body}\nWhich slot is this? Reply: {options}")


def _fill_slot(tg: Tg, chat_id: int, job: Job, role: str, path: Path, p: Probe,
               extra: str = "") -> None:
    """Put a file in a slot and say so — including when it displaces one.

    The one acknowledgement path for every fill (findings I6/I7,
    2026-08-31). Both ways into a slot could previously overwrite a file the
    user had already placed and reply as if nothing had been lost: resending a
    video silently replaced `driver`, and answering a role that was already
    filled both popped the queue head and overwrote the slot, so the original
    was unrecoverable in the same breath. Overwriting is still allowed — it is
    how you correct a mistake — but it is now named.
    """
    replacing = role in job.slots
    job.slots[role] = path
    job.probes[role] = p
    prefix = f"replacing the previous {role}\n" if replacing else ""
    suffix = f"\n{extra}" if extra else ""
    tg.send_message(chat_id, f"{prefix}{role}: {describe(p)}{suffix}")


def _render_and_validate(tg: Tg, chat_id: int, job: Job) -> bool:
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

    One asymmetry is load-bearing: the drain-still-running branch leaves
    `_LAST_VALIDATE` UNSET rather than setting it False. Unset means "never
    attempted", which is what /confirm keys off to retry later; False means
    "attempted and the manifest is bad", which /confirm must not retry.
    """
    manifest_path = _job_manifest_path(chat_id)
    if drain_running(manifest_path):
        # The money guard on the WRITE side, not just on /confirm (finding C1,
        # 2026-08-31). scripts/drain.py:220-248 runs batch_run.py as a separate
        # process TWICE, each time re-reading this manifest from disk: phase A
        # (--no-start), then provision + bootstrap, then phase B (--resume). So
        # for the 10-35 minutes after /confirm the file is still live input, and
        # _job_manifest_path is one deterministic path per chat. A user
        # assembling the next job during the render — the most ordinary thing
        # imaginable — would fill the last slot here and put job B onto the pod
        # rented for job A, with nobody asked. The journal collides the same
        # way: state_path_for derives batch/tg-<chat>.state.json from this same
        # name, so --resume would re-attach to job A's recorded job_ids and
        # /result for A could never be answered again.
        tg.send_message(chat_id,
                        "a drain is still running for this chat's job — wait for "
                        "it to finish before starting another. Your files are "
                        "kept; send /status for progress. Once it has finished, "
                        "send /confirm and this job will be checked and started.")
        return False
    write_manifest(job, manifest_path, now=time.strftime("%Y-%m-%d %H:%M:%S"))

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


def _maybe_show_manifest(tg: Tg, chat_id: int, job: Job) -> None:
    """Once every required slot is filled: render, validate for free, show it.

    This is the plan's "[Run]" step (docs/superpowers/plans/2026-08-31-…
    Task 5: "renders the manifest, runs make batch-validate (free), and shows
    the result with an estimate") — triggered automatically by the last slot
    fill rather than a separate command, since there is no button to tap for
    it. It rents nothing: `make batch-validate` never touches the pod.
    """
    if missing_slots(job):
        return
    if not _render_and_validate(tg, chat_id, job):
        return

    manifest_path = _job_manifest_path(chat_id)
    minutes = estimate_minutes(job)
    tg.send_message(
        chat_id,
        manifest_path.read_text(encoding="utf-8") +
        f"\nestimated {minutes} min (measured once on one batch — not a promise)\n"
        "This will rent a GPU pod at $0.99/hour. Reply /confirm to spend money "
        "and start, or nothing yet costs anything.")


def handle(tg: Tg, update: dict, *, allowed_user_id: int,
           dry_run: bool = False) -> None:
    if not allowed(update, allowed_user_id):
        return                              # silent: do not confirm the bot exists
    msg = update["message"]
    chat_id = msg["chat"]["id"]

    # Accepting any of these would put a silently degraded input into a
    # $0.99/hour render; ignoring them leaves the user watching a chat that
    # never answers. See NON_FILE_MEDIA for the measurement.
    kind = next((k for k in NON_FILE_MEDIA if msg.get(k)), None)
    if kind:
        tg.send_message(chat_id,
                        f"That arrived as a {kind}, not a File, so Telegram "
                        "re-encoded it — measured 2026-08-31: same 1088x1920 "
                        "frame, but half the bitrate and 49.8% of the bytes "
                        "gone.\nSend it again with the paperclip -> File.")
        return

    doc = msg.get("document")
    if doc:
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
        try:
            src = Path(tg.call("getFile", file_id=doc["file_id"])["file_path"])
            path = _stage_file(chat_id, src, doc.get("file_name"))
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
        except (RuntimeError, TgError, KeyError, OSError) as exc:
            # ffprobe raises rather than guessing (ingest.probe's own
            # contract) — the file never enters a job, so it can never
            # silently pick a wrong preset or a wrong slot.
            tg.send_message(chat_id, f"that file was not accepted: {exc}")
            return

        _CONFIRM_WARNED.discard(chat_id)

        job = _STATE.setdefault(chat_id, Job(slots={}, probes={}, pipeline=JOB_PIPELINE))
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
            if len(queue) == 1:
                _ask_about(tg, chat_id, p, job.pipeline, extra=fidelity)
            else:
                # Still show describe() — the quality gate must stay visible
                # for every accepted file, not just the one currently asked
                # about — but don't ask again yet: only the head is asked.
                tg.send_message(chat_id,
                                f"{describe(p)}\n{fidelity}\nqueued — answer the "
                                "previous question first")
            return

        # A video: structural, always `driver` — no question needed.
        _fill_slot(tg, chat_id, job, role, path, p, extra=fidelity)
        _maybe_show_manifest(tg, chat_id, job)
        return

    text = msg.get("text") or ""

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
        batch_dir = _safe_child(ROOT / "out", parts[1])
        if batch_dir is None:
            # Refuse without echoing the argument back: reflecting whatever
            # was typed into the reply is how a refusal message becomes its
            # own small problem.
            tg.send_message(chat_id,
                            "that batch id is not allowed — send a bare id, "
                            "no path separators or '..'")
            return
        # SOLE_RUN_ID: Plan 2A's manifests only ever have one run, "job".
        path = batch_dir / "runs" / SOLE_RUN_ID / "01-tryon.png"
        if not path.exists():
            tg.send_message(chat_id, "no try-on image found for that batch")
            return
        tg.send_document(chat_id, path, caption="try-on")
        return

    if text.startswith("/result"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            tg.send_message(chat_id,
                            "usage: /result <manifest filename under batch/>, "
                            "e.g. /result 2026-08-31-2140.yaml")
            return
        manifest_path = _safe_child(ROOT / "batch", parts[1])
        if manifest_path is None:
            tg.send_message(chat_id,
                            "that manifest name is not allowed — send a bare "
                            "filename under batch/, no path separators or '..'")
            return
        if not manifest_path.exists():
            tg.send_message(chat_id, "no manifest found with that name")
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
            tg.send_message(chat_id, "nothing started yet for this chat")
            return
        tg.send_message(chat_id, progress_text(manifest_path,
                                               lease=lease_for(manifest_path)))
        return

    if text.startswith("/confirm"):
        # THE money gate. This is the only line in this file that may call
        # start_drain — grep -rn "start_drain" must show every call site
        # here, after the drain_running check below. `dry_run` comes from
        # the caller (main()'s --dry-run, default False for real usage and
        # for every call in this file's own tests) — the CLI flag has to
        # actually reach this line, or "--dry-run: never invokes drain" in
        # this module's own docstring would be false.
        job = _STATE.get(chat_id)
        if job is None or missing_slots(job):
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
                            f"{len(pending)} file(s) still unassigned — answer "
                            f"them, or send /confirm again to run without them")
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
            if not _render_and_validate(tg, chat_id, job):
                # It already sent the specific reason; a second, vaguer line
                # would only bury it.
                return
            # The confirmation screen was never shown for this job, so send
            # the manifest now: nothing may spend $0.99/hour without the exact
            # inputs it spent on being in the transcript.
            tg.send_message(chat_id,
                            _job_manifest_path(chat_id).read_text(encoding="utf-8"))
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

        manifest_path = _job_manifest_path(chat_id)
        if drain_running(manifest_path):
            # The money guard: a second drain on one manifest corrupts the
            # journal (two runners, one state.json) and double-books the GPU
            # (run_enhance's comfy_recycle assumes exclusive use) —
            # docs/batch-runner.md.
            tg.send_message(chat_id,
                            "a drain is already running for this job — wait for it "
                            "to finish before confirming again")
            return
        start_drain(manifest_path, dry_run=dry_run)
        # Clear in-memory state so the next file starts a fresh job rather
        # than mutating one already handed to a running drain. The manifest
        # itself, and the drain's own journal, stay on disk regardless.
        dropped = len(_PENDING.pop(chat_id, []) or [])
        _STATE.pop(chat_id, None)
        _LAST_VALIDATE.pop(chat_id, None)
        _CONFIRM_WARNED.discard(chat_id)
        if dropped:
            tg.send_message(chat_id, f"running without {dropped} unassigned file(s)")
        tg.send_message(chat_id,
                        "started — renting a GPU pod at $0.99/hour now.\n"
                        f"Check back with /result {manifest_path.name} once it's done.")
        return

    if text.startswith("/start"):
        tg.send_message(chat_id,
                        "Ready. Send a file with the paperclip -> File.\n"
                        "/status progress · /confirm spends money · "
                        "/result <manifest>.yaml · /tryon <batch-id>")
        return

    # A plain-text reply, meant to answer the question asked about the HEAD
    # of the pending queue (never any other queued file — only the head is
    # ever asked about). Anything not a recognised slot name is re-asked,
    # never guessed — mirrors job.slot_for's own refusal to guess.
    queue = _PENDING.get(chat_id)
    if queue:
        job = _STATE.setdefault(chat_id, Job(slots={}, probes={}, pipeline=JOB_PIPELINE))
        options = _askable_roles(job.pipeline)
        answer = text.strip().lower()
        if answer in options:
            path, p = queue.pop(0)
            if not queue:
                _PENDING.pop(chat_id, None)
            _CONFIRM_WARNED.discard(chat_id)
            # _fill_slot, not a bare assignment: answering a role that is
            # already filled pops the queue head AND overwrites the slot, so
            # the displaced file is gone in the same step (finding I7).
            _fill_slot(tg, chat_id, job, answer, path, p)
            _maybe_show_manifest(tg, chat_id, job)
            if queue:
                # The next file was already queued (arrived before this
                # answer) — ask about it immediately rather than waiting for
                # another document to trigger it.
                _, next_p = queue[0]
                _ask_about(tg, chat_id, next_p, job.pipeline)
        else:
            tg.send_message(chat_id, f"didn't recognise that — reply one of: "
                            f"{' / '.join(options)}")


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
    tg = Tg(token=token, base_url=base)
    allowed_user_id = int(raw_id)
    offset = 0
    log(f"started, api={base}, dry_run={args.dry_run}")
    while True:
        try:
            for update in tg.get_updates(offset):
                offset = update["update_id"] + 1
                handle(tg, update, allowed_user_id=allowed_user_id,
                       dry_run=args.dry_run)
        except TgError as exc:
            log(f"poll failed, continuing: {exc}")
            time.sleep(5)
        except Exception as exc:            # one bad update must not end the bot
            log(f"update failed, continuing: {exc!r}")
        if args.once:
            return 0


if __name__ == "__main__":
    sys.exit(main())
