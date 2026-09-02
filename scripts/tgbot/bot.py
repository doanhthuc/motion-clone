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
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.config import env_get, env_set
from batchlib.manifest import load_manifest, load_state, state_path_for
from batchlib.pipelines import PIPELINES, optional_roles, required_roles
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
from tgbot.ingest import (Probe, describe, probe, quality_warning,
                         quality_warning_html,
                         to_png_if_heic)
from tgbot.job import (Job, missing_slots, run_id_for, slot_for,
                       write_manifest)
from tgbot.preview import sheet, slot_preview
from tgbot.run import (LEASE_PATH, _RUNNING, drain_running, estimate_minutes,
                       final_files, lease_for, progress_text, start_drain,
                       summary_text)
from batchlib_ext.gpu_stock import stock_at, volume_datacenter
from batchlib_ext.handoff import handoff_path, mailbox_path, read_handoff
from batchlib_ext.lease import clear_lease
from batchlib_ext.migrate_lease import read_migrate_lease

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

# What each wrong path actually costs, so the refusal argues from a number
# instead of from authority. Measured 2026-08-31 from the iPhone client, one
# file sent every available way.
#
# Images come out worse than video, and in three ways at once: Telegram caps
# the long edge at 2560 (the source was 2720, so even "high quality" shrinks
# it), converts PNG to JPEG — lossless to lossy, with chroma subsampling — and
# compresses ~50x. That last part is why relaxing this rule for images was
# considered and rejected: try-on consumes the character image directly, and
# this repo's background-chroma measurements assume the colour was not
# subsampled on the way in.
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
        tg.send_message(chat_id, "❌ <b>No batch was ever recorded</b> for "
                                 f"<code>{_esc(manifest_path.name)}</code> — the "
                                 "drain failed before it started. The log is "
                                 f"<code>{_esc(manifest_path.stem)}.drain.log</code> "
                                 "on the box.", parse_mode=PARSE_HTML)
        return
    batch_dir = ROOT / "out" / batch_id
    failures = failed_job_ids(state)
    outputs = final_files(batch_dir)

    if outputs:
        tg.send_message(chat_id, f"✅ <b>Done</b> · {_esc(batch_id)} · "
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
        tg.send_message(chat_id, f"❌ <b>{_esc(run_id)} failed</b>{where}",
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
        tg.send_message(chat_id, f"⚠️ <b>Nothing to send</b> for "
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
        chat_id, Job(slots={}, probes={}, pipeline=_DEFAULT_PIPELINE))


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
    labels = [(f"{ROLE_ICON.get(r, '')} {r}" + (f" {ICON_OK}" if r in filled else ""),
               _CB_SLOT + r) for r in roles]
    rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    question = f"{describe(p)}\nWhich slot is this?"
    if filled & set(roles):
        # The ✅ on a role's button says it's filled, but not what tapping it
        # does — a user wanting to swap that file out has no other button to
        # reach for, so the answer has to live right next to the question
        # (2026-09-02, reported as "didn't tell me how to change outfit").
        question += f"\n({ICON_OK} = already set — tap it again to replace)"

    # With the picture, when there is one (2026-09-01). This question used to
    # be asked about an image the user could not see — the File rule that keeps
    # the bytes intact also means nothing is ever shown back, so the whole
    # prompt was "image 1536x2720, 4.9 MB / Which slot is this?" and the only
    # way to answer was to remember the upload order.
    shot = (slot_preview(path, is_video=False, into=_preview_dir(chat_id))
            if path else None)
    if shot is not None:
        try:
            tg.send_photo(chat_id, shot, caption=question, buttons=rows)
            return
        except TgError as exc:
            # Fall through to text. A preview is a courtesy and must never be
            # able to swallow the question itself.
            log(f"preview upload failed, asking without it: {exc}")
    tg.send_message(chat_id, question, buttons=rows)


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

    One asymmetry is load-bearing: the mailbox-already-occupied branch leaves
    `_LAST_VALIDATE` UNSET rather than setting it False. Unset means "never
    attempted", which is what /confirm keys off to retry later; False means
    "attempted and the manifest is bad", which /confirm must not retry.
    """
    live_path = _job_manifest_path(chat_id)
    running = drain_running(live_path)
    if running and mailbox_path(live_path).exists():
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
                         bump: bool = False) -> None:
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
    _show_panel(tg, chat_id, bump=bump)


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
    _STATE[chat_id] = Job(slots=slots, probes=probes, pipeline=pipeline)
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
                "⚠️ <b>The job I was holding could not be read.</b>\n"
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

        elif data == _CB_RUN_ASK:
            job = _STATE.get(chat_id)
            if job is None or missing_slots(job):
                tg.send_message(chat_id, "no complete job yet — send the "
                                         "required files first")
            else:
                # The second step. Deliberately a separate tap: the first one
                # is next to the manifest and easy to hit by accident.
                _offer_run_confirm(tg, chat_id)

        elif data.startswith(_CB_RUN_SWITCH):
            gpu_id = _GPU_BY_SHORT.get(data[len(_CB_RUN_SWITCH):])
            if gpu_id is None:
                tg.send_message(chat_id, "that button is from an older "
                                         "version of the bot; tap Run again")
            else:
                env_set(ROOT / ".env", "GPU", gpu_id)
                tg.send_message(chat_id, f"switched — GPU is now {_esc(gpu_id)}",
                                parse_mode=PARSE_HTML)
                _offer_run_confirm(tg, chat_id)

        elif data.startswith(_CB_RUN_GO):
            if data[len(_CB_RUN_GO):] != _run_token(chat_id):
                tg.send_message(chat_id,
                                "the job changed since that button was sent, so "
                                "nothing ran. Check the manifest above and "
                                "confirm again.")
            else:
                _do_confirm(tg, chat_id, dry_run=dry_run)

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
        f"⚙️ <b>Flow for{where}</b>\n"
        f"now: {_flow(job.pipeline)}  <i>({_esc(job.pipeline)})</i>",
        buttons=[[(" → ".join(PIPELINES[name]), _CB_PIPE + name)]
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
            f"⚙️ <b>Flow switched</b>\n"
            f"now: {_flow(job.pipeline)}  <i>({_esc(job.pipeline)})</i>",
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




# One icon per role, one per state. Ordinary emoji, not Telegram's animated
# custom emoji — measured 2026-09-01 against this bot on the real API, because
# the reason recorded here before ("a Premium feature, renders as a
# placeholder") was wrong about both the cause and the symptom: a
# `<tg-emoji emoji-id="...">` entity is accepted with ok:true and then STRIPPED,
# the message coming back with entities:null and only the fallback glyph. The
# Bot API grants custom emoji to bots that bought a username on Fragment, and
# this one has not. There is no error to catch and nothing renders wrong — the
# animation simply never exists, which is why reading the docs was never going
# to settle it. Animated .tgs stickers DO work (getStickerSet "AnimatedEmojies"
# has ⏳ ✅ ❌ 🎬), but a sticker message cannot be edited, so it can never
# carry state. Motion in this bot comes from re-editing text; see run._SPIN.
ROLE_ICON = {"character": "👤", "outfit": "👗", "driver": "🎬", "background": "🖼"}

# One square per job, in the same order as preview.ROW_ACCENTS and the same
# colours. Nothing can be written onto the contact sheet — `drawtext` is not
# compiled into the ffmpeg this runs against — so colour is the only legend
# that reads in the picture and in the text at once: the bar down the left of
# row 2 is the square printed beside entry 2 here.
ROW_MARK = ["🟦", "🟧", "🟩", "🟪", "🟥", "🟨"]


def _row_mark(index: int) -> str:
    return ROW_MARK[index % len(ROW_MARK)]
ICON_OK = "✅"
ICON_WARN = "⚠️"
ICON_EMPTY = "⬜"

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
    icon_role = ROLE_ICON.get(role, "")
    if role not in job.slots:
        tail = "" if role in required_roles(job.pipeline) else " — optional"
        return f"{ICON_EMPTY} {icon_role} {_esc(role)}{tail}"
    pr = job.probes.get(role)
    state = ICON_WARN if (pr and quality_warning(pr)) else ICON_OK
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
    labels = [("⚙️", _CB_PIPE_ASK)]
    labels += [(f"🔁{ROLE_ICON.get(role, '')}", _CB_REDO + role)
               for role in sorted(job.slots)]
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
        return (f"{head} · ready · ⏱ ~{minutes} min · 💸 $0.99/hour"
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
            line = f"{_row_mark(index)} {_flow(other.pipeline)}"
            if not with_pictures:
                line += "  <i>" + _esc(", ".join(Path(other.slots[role]).stem
                                                 for role in sorted(other.slots))) + "</i>"
            lines.append(line)
        if len(queued) > len(basket):
            # The job being edited is the LAST row of the sheet, so it takes
            # the next square. Without this the picture has one more coloured
            # bar than the list has squares and the mapping silently breaks.
            lines.append(f"▸{_row_mark(len(basket))} {_flow(job.pipeline)}"
                         "  <i>← open</i>")
        lines.append("")
    else:
        lines += [f"🎬 <b>{_esc(job.pipeline)}</b>", ""]

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
        run = [(f"▶️ Run {len(queued)} · $0.99/h" if len(queued) > 1
                else "▶️ Run · $0.99/h", _CB_RUN_ASK)]
        # Offered beside Run, not instead of it: one pod runs everything in the
        # manifest, so adding another job costs nothing but the render itself.
        if not missing_slots(job):
            run.append(("➕ Add", _CB_ADD))
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

    trash: list[tuple[str, str]] = []
    if len(queued) > 1:
        trash.append(("🗑 this job", _CB_JOB_HERE))
    if job.slots or basket:
        trash.append(("🗑 all", _CB_CLEAR_ASK))
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
               probes=dict(job.probes))


def _signature(job: Job) -> tuple:
    """What makes two runs the same run — pipeline plus material."""
    return (job.pipeline, tuple(sorted((r, str(p)) for r, p in job.slots.items())))


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
    columns = " · ".join(f"{ROLE_ICON.get(r, '')} {_esc(r)}"
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
    frozen = _panel_text(chat_id, job) + f"\n\n🚀 <b>{_esc(stamp)}</b>"
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


# Animation state, in memory on purpose: a frame number is cosmetic, and a
# restart that resets it to 0 costs one visual stutter. Putting it in the
# progress file would mean a disk write every 2 seconds for the length of a
# render, to persist something nobody would notice being wrong.
_FRAME: dict[int, int] = {}
_ANIM_PAUSE: dict[int, float] = {}      # chat_id -> time.time() to resume at

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
                    stages: list[str]) -> None:
    """Send the first progress message and record it for later edits."""
    text = progress_text(manifest_path, lease=lease_for(manifest_path),
                         stages=stages)
    message_id = tg.send_message(chat_id, text, parse_mode=PARSE_HTML)
    _progress_path(chat_id).write_text(json.dumps({
        "manifest": str(manifest_path), "message_id": message_id,
        "stages": stages}, indent=2), encoding="utf-8")


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
                                   stages=stages)
        tg.edit_message(chat_id, message_id, final_text, parse_mode=PARSE_HTML)
        path.unlink(missing_ok=True)
        _FRAME.pop(chat_id, None)
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
                            "✅ Finished — automatically continuing with your "
                            "queued job on the same pod, no extra rental.",
                            parse_mode=PARSE_HTML)
            _start_progress(tg, chat_id, picked_up, next_stages)
        else:
            tg.send_message(chat_id,
                            f"⚠️ Finished, but the job you queued next could not "
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

    frame = _FRAME[chat_id] = _FRAME.get(chat_id, 0) + 1
    text = progress_text(manifest_path, lease=lease_for(manifest_path),
                         stages=stages, frame=frame)
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
    _FRAME.pop(chat_id, None)
    _ANIM_PAUSE.pop(chat_id, None)
    tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)
    deliver_result(tg, chat_id, manifest_path)


def tick_migration_progress(tg: Tg, chat_id: int) -> None:
    """Re-render the migration progress message, same shape as tick_progress
    for a drain — one message, edited in place throughout, including its
    final done/failed state ("one last edit so the message ends on the
    truth" — tick_progress's own reasoning applies unchanged here; there is
    no migration equivalent of deliver_result's separate send of actual
    output files, so nothing should be sent as a substitute for editing the
    progress text itself). Called every poll tick alongside tick_progress;
    harmless no-op when no migration is running.
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
        "create": "🔄 <b>Migrating volume</b> — creating the destination volume…",
        "sync": "🔄 <b>Migrating volume</b> — copying data between temp pods…",
        "verify": "🔄 <b>Migrating volume</b> — verifying checksums…",
        "done": "✅ Migration done.",
        "failed": f"⚠️ Migration failed: {_esc(payload.get('reason', 'unknown error'))}",
    }.get(phase, f"🔄 <b>Migrating volume</b> — {_esc(phase)}")
    if phase == "done" and payload.get("warning"):
        text += f"\n{_esc(payload['warning'])}"

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


_LAST_SUFFIX = ".last.json"


def _dump_jobs(jobs: list[Job]) -> list[dict]:
    return [{"pipeline": j.pipeline,
             "slots": {r: str(v) for r, v in j.slots.items()},
             "probes": {r: asdict(pr) for r, pr in j.probes.items()}}
            for j in jobs]


def _load_jobs(payload: list) -> list[Job]:
    return [Job(pipeline=entry["pipeline"],
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
        buttons=[[("Yes, start over", _CB_CLEAR_GO), ("Keep it", _CB_CLEAR_NO)]])


def _clear_job(tg: Tg, chat_id: int) -> None:
    """Throw away the draft, the queue and the staged files for this chat."""
    if drain_running(_job_manifest_path(chat_id)):
        # The staged files ARE the running job's inputs — the manifest points
        # straight at them — so deleting them mid-drain breaks a run that is
        # already being paid for.
        tg.send_message(chat_id, "a drain is running for this job — clearing "
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
    done = (f"🗑 <b>Cleared.</b> {removed} staged file(s) deleted.\n"
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
        buttons=[[("Yes, wipe it", _CB_WIPE_GO), ("Keep it", _CB_WIPE_NO)]])


def _wipe_chat(tg: Tg, chat_id: int) -> None:
    """Delete every tracked message in this chat and the job being
    assembled, in one action (2026-09-02) — a chat clean enough to restart in.

    Shares /clear's drain guard rather than repeating it: the staged files
    ARE a running job's inputs, and its progress message is the one thing
    telling the user it is still going, so a live drain refuses the whole
    thing, not only the file half.
    """
    if drain_running(_job_manifest_path(chat_id)):
        tg.send_message(chat_id, "a drain is running for this chat's job — "
                                 "wiping now would delete the files it is "
                                 "reading, and the message that tells you "
                                 "when it's done. Wait for it, then /wipe.")
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
# observed as noticeably slower in real use.
_FALLBACK_GPU_IDS = ("NVIDIA GeForce RTX 4090", "NVIDIA RTX PRO 4500 Blackwell")

# callback_data stays short (Bot API caps it at 64 bytes) — a switch button
# carries one of these keys, never the full gpuId string.
_GPU_SHORT = {_PRIMARY_GPU_ID: "5090", "NVIDIA GeForce RTX 4090": "4090",
             "NVIDIA RTX PRO 4500 Blackwell": "pro4500"}
_GPU_BY_SHORT = {v: k for k, v in _GPU_SHORT.items()}

# runpodctl's own stock words, ranked best-first — used only to sort the
# "other regions" list so the most promising alternative surfaces first.
_STOCK_RANK = {"high": 0, "medium": 1, "low": 2, "none": 3}

# A coloured dot reads faster than the word next to it, on a phone screen
# glanced at before deciding whether to spend $0.99/h. Traffic-light order,
# extended one step for "none" — Telegram renders all four natively, no
# custom-emoji entitlement needed (unlike tgbot/run.py's own note on
# <tg-emoji> being silently stripped).
_STOCK_ICON = {"high": "🟢", "medium": "🟡", "low": "🟠", "none": "🔴"}


def _report_gpu_stock(tg: Tg, chat_id: int) -> None:
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
    """
    volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
    home_dc = volume_datacenter(volume_id)
    wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
    try:
        stock = stock_at(wanted)
    except RuntimeError as exc:
        tg.send_message(chat_id, f"couldn't reach runpodctl: {exc}")
        return

    lines = ["📦 <b>GPU stock</b>"]
    configured = env_get(ROOT / ".env", "GPU")
    if configured:
        lines.append(f"🖥 Currently selected: <b>{_esc(configured)}</b>")
    if home_dc:
        lines.append(f"📍 <b>{_esc(home_dc)}</b> (your volume)")
    else:
        lines.append("(volume datacenter unknown — showing every region)")

    other_lines: list[str] = []
    for wanted_id in wanted:
        entries = stock.get(wanted_id)
        if not entries:
            lines.append(f"  {_esc(wanted_id)}: not listed by runpodctl right now")
            continue
        price = f"💵 ${entries[0].price_per_hr:.2f}/h" if entries[0].price_per_hr else "?"
        # None, not "not offered here", when home_dc itself is unknown — that
        # reads as "checked and absent", which is a claim this branch has no
        # basis for.
        home = next((e for e in entries if home_dc and e.datacenter_id == home_dc),
                    None)
        if home is not None:
            icon = _STOCK_ICON.get(home.stock_status.lower(), "⬜")
            lines.append(f"  {_esc(entries[0].display_name)}: "
                         f"{icon} <b>{_esc(home.stock_status)}</b> · {price}")
        else:
            lines.append(f"  <b>{_esc(entries[0].display_name)}</b> · {price}")

        elsewhere = sorted(
            (e for e in entries if e is not home and e.stock_status.lower() != "none"),
            key=lambda e: _STOCK_RANK.get(e.stock_status.lower(), 9))
        for e in elsewhere[:2]:
            icon = _STOCK_ICON.get(e.stock_status.lower(), "⬜")
            other_lines.append(f"  {_esc(e.display_name)} — {_esc(e.datacenter_id)}: "
                               f"{icon} {_esc(e.stock_status)}")

    if other_lines:
        lines.append("\n<b>Other regions</b> (need the volume synced there "
                     f"first, {MIGRATE_DURATION_SHORT} — not an instant switch):")
        lines.extend(other_lines)

    tg.send_message(chat_id, "\n".join(lines), parse_mode=PARSE_HTML)


def _gpu_price(gpu_id: str, stock: dict) -> float:
    """The configured GPU's own $/h, from the stock check just made —
    never a second runpodctl round trip. $0.99 (the 5090's own price) is
    the fallback for when the check itself failed or the id is unlisted,
    matching what [Run] has always quoted rather than inventing a new
    default.
    """
    entries = stock.get(gpu_id)
    return entries[0].price_per_hr if entries and entries[0].price_per_hr else 0.99


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


def _offer_run_confirm(tg: Tg, chat_id: int) -> None:
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
    """
    configured = env_get(ROOT / ".env", "GPU") or _PRIMARY_GPU_ID
    volume_id = env_get(ROOT / ".env", "POD_VOLUME_ID")
    home_dc = volume_datacenter(volume_id)
    wanted = [_PRIMARY_GPU_ID, *_FALLBACK_GPU_IDS]
    try:
        stock = stock_at(wanted) if home_dc else {}
    except RuntimeError:
        stock = {}

    price = _gpu_price(configured, stock)
    if not stock or not home_dc:
        tg.send_message(
            chat_id,
            f"This rents a GPU pod at ${price:.2f}/hour and starts the job.\n"
            "Confirm?",
            buttons=[[(f"Yes, spend ${price:.2f}/h", _CB_RUN_GO + _run_token(chat_id)),
                      ("Cancel", _CB_RUN_NO)]])
        return

    # "Current" gets its own paragraph rather than an inline "(current)" tag
    # (2026-09-02) — on a phone, three bold lines that only differ by six
    # small letters at the end read as one undifferentiated list; a reader
    # reported not being able to tell which was already selected.
    lines = [f"🖥 <b>Choose GPU</b> — renting at {_esc(home_dc)}", ""]
    configured_home = next((e for e in (stock.get(configured) or [])
                            if e.datacenter_id == home_dc), None)
    if configured_home is not None:
        icon = _STOCK_ICON.get(configured_home.stock_status.lower(), "⬜")
        lines.append(f"Current: {icon} <b>{_esc(configured_home.display_name)}</b> — "
                     f"{_esc(configured_home.stock_status)} · "
                     f"💵 ${configured_home.price_per_hr:.2f}/h")
    else:
        lines.append(f"Current: <b>{_esc(configured)}</b> — "
                     f"not offered at {_esc(home_dc)}")

    alt_lines: list[str] = []
    switch_row: list[tuple[str, str]] = []
    # Every OTHER datacenter each GPU is stocked at, not just the home one
    # (2026-09-02) — "5090 is out, why not check other regions" was a fair
    # question: the GPU-TYPE switch buttons only ever move within the pinned
    # home datacenter, but that is not a reason to hide where else the 5090
    # itself is doing fine. Renting there means migrating the Network Volume
    # first, which is why each row also gets a MIGRATE button (Task 8) — a
    # destructive operation, so the button only opens the confirm screen,
    # never starts anything.
    other_lines: list[str] = []
    for gpu_id in wanted:
        entries = stock.get(gpu_id) or []
        home = next((e for e in entries if e.datacenter_id == home_dc), None)
        if gpu_id != configured:
            if home is None:
                alt_lines.append(f"  {_esc(gpu_id)}: not offered at {_esc(home_dc)}")
            else:
                icon = _STOCK_ICON.get(home.stock_status.lower(), "⬜")
                alt_lines.append(f"  {icon} <b>{_esc(home.display_name)}</b> — "
                                 f"{_esc(home.stock_status)} · 💵 ${home.price_per_hr:.2f}/h")
                short = _GPU_SHORT.get(gpu_id)
                if short:
                    switch_row.append((f"Switch to {home.display_name} "
                                      f"(${home.price_per_hr:.2f}/h)",
                                      _CB_RUN_SWITCH + short))
        elsewhere = sorted(
            (e for e in entries if e is not home and e.stock_status.lower() != "none"),
            key=lambda e: _STOCK_RANK.get(e.stock_status.lower(), 9))
        for e in elsewhere[:2]:
            icon = _STOCK_ICON.get(e.stock_status.lower(), "⬜")
            other_lines.append(f"  {icon} {_esc(e.display_name)} — "
                               f"{_esc(e.datacenter_id)}: {_esc(e.stock_status)}")
            # Only offered when nothing is already mid-copy (2026-09-02) — a
            # second migration racing the first would fight it for the same
            # temp pods; migration_running() is the same guard _ask_migrate
            # and _start_migration re-check themselves, so a stale button
            # tapped after a migration already started still fails safely.
            #
            # NOT gated on _GPU_SHORT any more. The payload only needs the
            # DATACENTER — a migration moves the volume and says nothing about
            # which GPU is rented afterwards — and requiring a short code to
            # build a prefix nobody read meant a GPU absent from that table
            # silently offered no migrate button at all.
            if not migration_running():
                switch_row.append((f"Switch to {e.display_name} ({e.datacenter_id})",
                                  _CB_MIGRATE_ASK + e.datacenter_id))
    if alt_lines:
        lines += ["", "Switch to:", *alt_lines]
    if other_lines:
        lines += ["", "Other regions (needs the volume migrated there first, "
                      f"{MIGRATE_DURATION_SHORT} — not an instant switch):",
                  *other_lines]
    elif not switch_row:
        # Nothing else at this datacenter, and no other region has it
        # either — the manual EU-CZ-1 runbook is the only remaining option.
        lines += ["", "No other GPU or region has better stock right now. "
                      "See docs/gpu-pod.md for the manual EU-CZ-1 runbook."]

    buttons = [switch_row[i:i + 2] for i in range(0, len(switch_row), 2)]
    buttons.append([(f"Yes, spend ${price:.2f}/h", _CB_RUN_GO + _run_token(chat_id)),
                    ("Cancel", _CB_RUN_NO)])
    tg.send_message(chat_id, "\n".join(lines), parse_mode=PARSE_HTML, buttons=buttons)


def _ask_kill(tg: Tg, chat_id: int) -> None:
    """The confirm step for /kill — mirrors [Run]'s Yes/Cancel, for the
    opposite reason: this one forfeits money already spent instead of
    committing new money.
    """
    manifest_path = _job_manifest_path(chat_id)
    if not drain_running(manifest_path):
        tg.send_message(chat_id, "nothing is running for this chat right now — "
                                 "there is no pod to kill")
        return
    lease = lease_for(manifest_path)
    spent = ""
    if lease is not None:
        mins = int((time.time() - lease.provisioned_at) / 60)
        spent = f" — already {mins} min (${mins / 60 * 0.99:.2f}) on the pod"
    tg.send_message(
        chat_id,
        f"⚠️ This destroys the pod right now{spent}. Whatever is mid-render "
        "is lost — no output, no resume. Are you sure?",
        buttons=[[("Yes, kill it", _CB_KILL_GO), ("Leave it running", _CB_KILL_NO)]])


def _do_kill(tg: Tg, chat_id: int) -> None:
    """The emergency stop (2026-09-02): destroy the pod right now, on request.

    Two layers, because neither alone is trustworthy. Terminating the Popen
    (only present when THIS bot process is the one that started the drain)
    stops batch_run.py from moving on to its next stage, but SIGTERM does not
    run drain.py's `finally: teardown()` — Python's default handler kills the
    process outright rather than raising something `finally` could catch — so
    `make gpu-destroy` is always run here directly afterwards too, exactly as
    pod_watchdog.py's tier 3 does not trust a runner to clean up after itself.
    `make gpu-destroy` already re-lists and verifies the pod is actually gone
    (Makefile:161-167) rather than trusting its own exit code, so this reuses
    that rather than re-deriving it.
    """
    manifest_path = _job_manifest_path(chat_id)
    proc = _RUNNING.get(manifest_path.resolve())
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    tg.send_message(chat_id, "🛑 destroying the pod…")
    try:
        result = subprocess.run(["make", "gpu-destroy"], cwd=_REPO_ROOT,
                                capture_output=True, text=True, timeout=180)
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
    _FRAME.pop(chat_id, None)
    _ANIM_PAUSE.pop(chat_id, None)

    if destroyed:
        tg.send_message(chat_id, "🛑 Killed. Pod destroyed and verified gone.")
    else:
        tg.send_message(chat_id,
                        f"⚠️ <b>gpu-destroy may not have worked</b> — check "
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
        buttons=[[("Yes, migrate", _CB_MIGRATE_GO + to_dc),
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

    tg.send_message(chat_id, f"🚀 Migration to {_esc(to_dc)} started — this will take "
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
    tg.send_message(chat_id, f"reusing the last batch — {len(jobs)} job(s). "
                             "/pipeline to change the flow, then Run.")
    _maybe_show_manifest(tg, chat_id, current)


def _do_confirm(tg: Tg, chat_id: int, *, dry_run: bool) -> None:
    """THE money gate. The ONLY function that may call start_drain.

    Extracted from the /confirm branch on 2026-08-31 when the Run button
    arrived. Two entry points must not mean two gates: every check below —
    completeness, the unanswered queue, the validation verdict, and
    drain_running — has to apply identically whether the user typed
    /confirm or tapped a button, and the way to guarantee that is one body
    with two callers rather than two bodies that agree today.

    `grep -rn "start_drain" scripts/tgbot/bot.py` must show exactly one
    call site, and it must be in here.
    """
    # Checked before anything else, including completeness — a migration mid-
    # copy is moving the Network Volume this pod would mount, so renting must
    # not be allowed to race it regardless of how complete the job is.
    if migration_running():
        tg.send_message(chat_id, "a volume migration is in progress for this pod's "
                                 "datacenter — wait for it to finish before renting")
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
    live_path = _job_manifest_path(chat_id)
    running = drain_running(live_path)
    manifest_path = mailbox_path(live_path) if running else live_path
    # Re-written unconditionally, even when `validated` was cached True: the
    # cache only remembers that the JOB CONTENT was valid, not which file it
    # was last written to. If a drain started in the seconds between the last
    # validate and this tap, `manifest_path` above just switched from the live
    # path to the mailbox, and the mailbox would otherwise sit empty — queued
    # in every OTHER sense but never actually written to disk. write_manifest
    # is a plain YAML dump, no subprocess, so redoing it here costs nothing.
    write_manifest(queued, manifest_path, now=time.strftime("%Y-%m-%d %H:%M:%S"))
    # Every stage any queued job will run, in pipeline order, de-duplicated.
    # The progress bar counts against this: a batch mixing two pipelines has to
    # show the union or the denominator would be wrong for half of it.
    stages: list[str] = []
    for other in queued:
        for stage in PIPELINES[other.pipeline]:
            if stage not in stages:
                stages.append(stage)
    # BEFORE start_drain and before the state clear: this is the last instant
    # the submitted job exists in memory, and freezing the panel here is what
    # leaves the exact inputs permanently in the transcript.
    _freeze_panel(tg, chat_id, f"submitted {time.strftime('%H:%M')}")
    if not running:
        # THE money gate (see docstring) — the only line that may rent a pod.
        # Queuing (the `running` branch below) never reaches this: the
        # mailbox file alone is drain.py's signal, claimed by the process
        # already running, on the pod already paid for.
        start_drain(manifest_path, dry_run=dry_run)
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
    if running:
        tg.send_message(chat_id,
                        f"📥 <b>Queued.</b> {submitted_count} job(s) will start "
                        "automatically on the same pod the moment the current "
                        "job finishes — no extra rental. /status shows both.",
                        parse_mode=PARSE_HTML)
        # No progress message yet — tick_progress starts one itself once
        # drain.py's handoff file says this was actually picked up. Sending
        # one now would claim progress on a job that has not started.
    else:
        tg.send_message(chat_id,
                        f"🚀 <b>Started.</b> {submitted_count} job(s) on one pod at "
                        "$0.99/hour.\nI will keep the message below updated and "
                        "send the results when it finishes — no need to ask.",
                        parse_mode=PARSE_HTML)
        _start_progress(tg, chat_id, manifest_path, stages)
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
    # $0.99/hour render; ignoring them leaves the user watching a chat that
    # never answers. See NON_FILE_MEDIA for the measurement.
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
        # Before the slow part, not after: ffprobe on a 25MB video plus the
        # staging copy is long enough that a silent bot reads as a stuck one.
        # A spinner rather than send_chat_action (2026-09-02): Telegram's own
        # "uploading" indicator disappears after ~5s and nothing here ever
        # refreshed it, so anything slower than that read as stuck again.
        try:
            with _spinner(tg, chat_id,
                         f"checking {doc.get('file_name') or 'the file'}"):
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
                _FIDELITY.setdefault(chat_id, {})[str(path)] = fidelity
        except (RuntimeError, TgError, KeyError, OSError) as exc:
            # ffprobe raises rather than guessing (ingest.probe's own
            # contract) — the file never enters a job, so it can never
            # silently pick a wrong preset or a wrong slot.
            tg.send_message(chat_id, f"that file was not accepted: {exc}")
            return

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
        prog = _progress_path(chat_id)
        if prog.exists():
            try:
                stages = json.loads(prog.read_text(encoding="utf-8")).get("stages")
            except ValueError:
                stages = None
        tg.send_message(chat_id,
                        progress_text(manifest_path,
                                      lease=lease_for(manifest_path),
                                      stages=stages),
                        parse_mode=PARSE_HTML)
        return

    if text.startswith("/confirm"):
        _do_confirm(tg, chat_id, dry_run=dry_run)
        return

    if text.startswith("/job"):
        if _STATE.get(chat_id) is None:
            tg.send_message(chat_id, NOTHING_ASSEMBLED, parse_mode=PARSE_HTML)
        else:
            # bump: /job is an explicit "show me now", and a silent edit to a
            # message somewhere above would look like the command did nothing.
            _show_panel(tg, chat_id, bump=True)
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

    if text.startswith("/start"):
        tg.send_message(
            chat_id,
            "📎 <b>File, not Photo</b> — picker → \"...\" → Send as File\n"
            "🎬 Videos are the driver. For images, I'll ask — just tap.\n\n"
            "✅ Full job shown before anything runs.\n"
            "💸 <b>Nothing spends money until you tap Run and confirm.</b>\n\n"
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
    ("again", "reuse the last job's files, e.g. with another pipeline"),
    ("clear", "throw away the job being assembled"),
    ("status", "progress of this chat's job"),
    ("confirm", "SPENDS MONEY - rents a GPU at $0.99/h and starts"),
    ("result", "the finished video, or the failure logs"),
    ("tryon", "just the try-on image, when the result looks wrong"),
    ("wipe", "delete every message in this chat, yours and mine"),
    ("gpu", "check RunPod 5090 stock before you rent — free"),
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
    global _DEFAULT_PIPELINE
    configured = env_get(ROOT / ".env", "TG_PIPELINE")
    if configured:
        if configured not in PIPELINES:
            print(f"TG_PIPELINE={configured!r} is not a known pipeline. "
                  f"Known: {', '.join(sorted(PIPELINES))}", file=sys.stderr)
            return 2
        _DEFAULT_PIPELINE = configured

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
            # The long-poll IS the animation timer (2026-09-01). While a drain
            # is running the poll shortens to 2s so the loop comes round often
            # enough to redraw a moving frame; the rest of the time it stays at
            # 50s, which costs one request per 50s and no timer of its own.
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
            tick_progress(tg, allowed_user_id)
            tick_migration_progress(tg, allowed_user_id)
        except TgError as exc:
            log(f"poll failed, continuing: {exc}")
            time.sleep(5)
        except Exception as exc:            # one bad update must not end the bot
            log(f"update failed, continuing: {exc!r}")
        if args.once:
            return 0


if __name__ == "__main__":
    sys.exit(main())
