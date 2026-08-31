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
import html
import json
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.config import env_get
from batchlib.manifest import load_state, state_path_for
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
                         to_png_if_heic)
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


def _askable_roles(pipeline: str) -> list[str]:
    """Slot names a user can be asked to name — every material role except
    `driver`, which is structural (job.slot_for never asks about a video)."""
    return sorted((required_roles(pipeline) | optional_roles(pipeline)) - {"driver"})


def _ask_about(tg: Tg, chat_id: int, p: Probe, pipeline: str, extra: str = "") -> None:
    """Ask which slot a parked, ambiguous file belongs to.

    One button per askable role (2026-08-31). Typing "character" on a phone is
    the friction the buttons remove; the typed reply still works, and
    _answer_slot is the single body both paths run.
    """
    roles = _askable_roles(pipeline)
    body = f"{describe(p)}\n{extra}" if extra else describe(p)
    tg.send_message(chat_id, f"{body}\nWhich slot is this?",
                    buttons=[[(r, _CB_SLOT + r) for r in roles]])


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
    # Said here as well as on the review screen: this is the moment the user is
    # looking at this particular file, and it is the last point where sending a
    # better one is cheap. See ingest.quality_warning for the survey behind it.
    warning = quality_warning(p)
    warn = f"\n\n{warning}" if warning else ""
    tg.send_message(chat_id, f"{prefix}{role}: {describe(p)}{suffix}{warn}")


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


def _manifest_summary(chat_id: int, job: Job) -> str:
    """What the user reads before spending — the review screen.

    Replaces echoing the raw YAML (2026-08-31). The trade is deliberate and
    worth naming: the rule this file works to is "nothing may spend $0.99/hour
    without the exact inputs it spent on being in the transcript", and a
    summary is weaker than the file itself. What keeps it honest is that the
    FILENAMES stay — and staged names are the user's own by design (see
    STAGING_DIR_NAME), chosen precisely so a manifest is readable on a phone.
    What goes is the repeated absolute path prefix, which is identical on every
    line and told the reader nothing. The full YAML is still on disk and still
    reachable with /result.
    """
    lines = [f"🎬 <b>{_esc(job.pipeline)}</b>", ""]
    for role in sorted(required_roles(job.pipeline) | optional_roles(job.pipeline)):
        lines.append(_role_line(role, job))
    # Any warning is repeated OUTSIDE the collapsed block here, unlike on /job:
    # this is the last thing read before $0.99/hour is committed, and something
    # that needs a tap to reveal is something that gets skipped.
    for role in sorted(job.slots):
        pr = job.probes.get(role)
        warning = quality_warning(pr) if pr else ""
        if warning:
            lines += ["", f"{ICON_WARN} <b>{_esc(role)}</b>: {_esc(warning)}"]
    lines += ["", _details_block(chat_id, job), "",
              f"⏱ ~{estimate_minutes(job)} min    💸 $0.99/hour",
              "<i>estimate measured once on one batch — not a promise</i>"]
    return "\n".join(lines)


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

    tg.send_message(chat_id, _manifest_summary(chat_id, job),
                    buttons=[[("▶️ Run · $0.99/h", _CB_RUN_ASK)]],
                    parse_mode=PARSE_HTML)


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
    if job is None and not pending:
        # /confirm clears the state after submitting; the draft must go with
        # it, or the next restart would resurrect a job already running.
        path.unlink(missing_ok=True)
        return
    payload = {
        "pipeline": job.pipeline if job else _DEFAULT_PIPELINE,
        "slots": {r: str(p) for r, p in (job.slots if job else {}).items()},
        "probes": {r: asdict(pr) for r, pr in (job.probes if job else {}).items()},
        "pending": [[str(p), asdict(pr)] for p, pr in pending],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_draft(chat_id: int) -> None:
    """Rehydrate one chat's draft. A corrupt file is reported, not obeyed."""
    path = _draft_path(chat_id)
    if not path.exists():
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pipeline = payload["pipeline"]
        if pipeline not in PIPELINES:
            raise ValueError(f"unknown pipeline {pipeline!r}")
        slots = {r: Path(v) for r, v in payload["slots"].items()}
        probes = {r: Probe(**d) for r, d in payload["probes"].items()}
        pending = [(Path(v), Probe(**d)) for v, d in payload["pending"]]
    except (ValueError, KeyError, TypeError) as exc:
        # Loud, and the staged files are still on disk: the user can re-send
        # or forward them. Silently starting fresh is what made the original
        # loss so confusing.
        log(f"draft for chat {chat_id} is unreadable, starting fresh: {exc!r}")
        return
    _STATE[chat_id] = Job(slots=slots, probes=probes, pipeline=pipeline)
    if pending:
        _PENDING[chat_id] = pending


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
    if chat_id not in _LOADED:
        _LOADED.add(chat_id)
        _load_draft(chat_id)
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
_CB_REDO = "redo:"
_CB_CLEAR_ASK = "clr:ask"
_CB_CLEAR_GO = "clr:go"
_CB_CLEAR_NO = "clr:no"


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
            if role not in _askable_roles(job.pipeline):
                # Reachable from a keyboard minted before a /pipeline switch.
                tg.send_message(chat_id,
                                f"{role} is not a slot for {job.pipeline}")
            elif not (_PENDING.get(chat_id) or []):
                tg.send_message(chat_id, "no file is waiting for a slot — that "
                                         "button is from an earlier question")
            else:
                _answer_slot(tg, chat_id, role)

        elif data.startswith(_CB_PIPE):
            _switch_pipeline_and_report(tg, chat_id, data[len(_CB_PIPE):])

        elif data == _CB_RUN_ASK:
            job = _STATE.get(chat_id)
            if job is None or missing_slots(job):
                tg.send_message(chat_id, "no complete job yet — send the "
                                         "required files first")
            else:
                # The second step. Deliberately a separate tap: the first one
                # is next to the manifest and easy to hit by accident.
                tg.send_message(
                    chat_id,
                    "This rents a GPU pod at $0.99/hour and starts the job.\n"
                    "Confirm?",
                    buttons=[[("Yes, spend $0.99/h", _CB_RUN_GO + _run_token(chat_id)),
                              ("Cancel", _CB_RUN_NO)]])

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

        elif data == _CB_CLEAR_ASK:
            _ask_to_clear(tg, chat_id)

        elif data == _CB_CLEAR_GO:
            _clear_job(tg, chat_id)

        elif data == _CB_CLEAR_NO:
            tg.send_message(chat_id, "kept — nothing deleted")

        else:
            tg.send_message(chat_id, "that button is from an older version of "
                                     "the bot; send /start for the commands")
    finally:
        tg.answer_callback_query(query.get("id") or "")


def _switch_pipeline_and_report(tg: Tg, chat_id: int, name: str) -> None:
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
    note = (f"\ndropped {', '.join(dropped)} — {name} has no stage that uses it"
            if dropped else "")
    still = sorted(missing_slots(job))
    tg.send_message(chat_id,
                    f"pipeline: {name}  ({' -> '.join(PIPELINES[name])}){note}\n" +
                    (f"still needed: {', '.join(still)}" if still
                     else "all slots filled — re-checking the manifest"))
    # Re-render against the new pipeline rather than leaving the manifest the
    # user last saw on screen: that file is what /confirm submits.
    _maybe_show_manifest(tg, chat_id, job)


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
        _, next_p = queue[0]
        _ask_about(tg, chat_id, next_p, job.pipeline)




# One icon per role, one per state. Ordinary emoji, not Telegram's custom
# emoji: those are a Premium feature and render as a placeholder for anyone
# without it, which would put meaning somewhere not everyone can see.
ROLE_ICON = {"character": "👤", "outfit": "👗", "driver": "🎬", "background": "🖼"}
ICON_OK = "✅"
ICON_WARN = "⚠️"
ICON_EMPTY = "⬜"

PARSE_HTML = "HTML"


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
    """Resolution and size only — the two numbers worth a glance.

    Duration and bitrate move into the expandable block. They matter (bitrate
    is how a re-compressed driver is caught) but they are diagnostic, and five
    figures per line is what made the first version of this screen a wall of
    text the user could not read.
    """
    return f"{p.width}×{p.height} · {p.size_bytes / 1_000_000:.1f} MB"


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
        warning = quality_warning(pr)
        if warning:
            lines.append(f"{ICON_WARN} {_esc(warning)}")
    lines += ["", f"manifest: {_esc(_job_manifest_path(chat_id).name)}"]
    return "<blockquote expandable>" + "\n".join(lines) + "</blockquote>"


def _fix_buttons(job: Job) -> list[list[tuple[str, str]]]:
    """Re-label buttons two per row, then Start over.

    Two per row, not one: four stacked full-width buttons pushed the message
    they belong to off the top of a phone screen.
    """
    labels = [(f"🔁 {r}", _CB_REDO + r) for r in sorted(job.slots)]
    rows = [labels[i:i + 2] for i in range(0, len(labels), 2)]
    if labels:
        rows.append([("🗑 clear", _CB_CLEAR_ASK)])
    return rows


_PROGRESS_SUFFIX = ".progress.json"


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

    text = progress_text(manifest_path, lease=lease_for(manifest_path),
                         stages=stages)
    if drain_running(manifest_path):
        tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)
        return

    # Finished — one last edit so the message ends on the truth, then the
    # files. The progress file goes first: if delivery raises, the next tick
    # must not deliver a second copy of everything.
    path.unlink(missing_ok=True)
    tg.edit_message(chat_id, message_id, text, parse_mode=PARSE_HTML)
    deliver_result(tg, chat_id, manifest_path)


_LAST_SUFFIX = ".last.json"


def _last_path(chat_id: int) -> Path:
    """The job most recently submitted, kept for /again.

    A separate file from the draft on purpose. _load_draft reads only
    `.draft.json`, so a submitted job can never be rehydrated into _STATE by a
    restart and re-confirmed by accident — the property /confirm's clear exists
    to protect. /again is an explicit request to copy it back.
    """
    return ROOT / "batch" / f"tg-{chat_id}{_LAST_SUFFIX}"


def _job_status(chat_id: int) -> tuple[str, list[list[tuple[str, str]]]]:
    """What is assembled, what is missing, and the buttons to fix it.

    Added 2026-08-31. Until this existed there was no way to ask the bot what
    it was holding: when a slot label went missing, the only way to find out
    what state the job was in was a screenshot of the chat. The draft was on
    disk and the answer was always knowable — nothing exposed it.
    """
    job = _STATE.get(chat_id)
    if job is None:
        return ("📎 <b>Nothing assembled yet.</b>\nSend a file as a "
                "<b>File</b> and I will measure it.", [])
    lines = [f"🎬 <b>{_esc(job.pipeline)}</b>", ""]
    # Every role of the pipeline, filled or not — an empty required slot is the
    # thing the user most needs to see, and listing only what is present hides
    # exactly that.
    for role in sorted(required_roles(job.pipeline) | optional_roles(job.pipeline)):
        lines.append(_role_line(role, job))
    queued = _PENDING.get(chat_id) or []
    if queued:
        lines += ["", f"⏳ waiting for a label: "
                      f"{_esc(', '.join(q[0].name for q in queued))}"]
    if job.slots:
        lines += ["", _details_block(chat_id, job)]
    if not missing_slots(job):
        lines += ["", f"⏱ ~{estimate_minutes(job)} min    💸 $0.99/h"]
    return "\n".join(lines), _fix_buttons(job)


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
    tg.send_message(chat_id, f"{path.name} is out of {role}")
    _ask_about(tg, chat_id, pr, job.pipeline)


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
    _STATE.pop(chat_id, None)
    _PENDING.pop(chat_id, None)
    _LAST_VALIDATE.pop(chat_id, None)
    _CONFIRM_WARNED.discard(chat_id)
    # handle()'s finally calls _save_draft, which deletes the draft file itself
    # now that there is no state left to write.
    tg.send_message(chat_id, f"cleared — {removed} staged file(s) deleted. "
                             "Send a file to start again.")


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
        pipeline = payload["pipeline"]
        if pipeline not in PIPELINES:
            raise ValueError(f"unknown pipeline {pipeline!r}")
        slots = {r: Path(v) for r, v in payload["slots"].items()}
        probes = {r: Probe(**d) for r, d in payload["probes"].items()}
    except (ValueError, KeyError, TypeError) as exc:
        log(f"last-job file for chat {chat_id} is unreadable: {exc!r}")
        tg.send_message(chat_id, "the last job's record is unreadable — send "
                                 "the files again")
        return
    # The staged copies may have been swept by `make batch-clean` or /clear
    # since. Named individually: "some files are missing" is not actionable.
    gone = sorted(r for r, sp in slots.items() if not sp.is_file())
    if gone:
        tg.send_message(chat_id,
                        "cannot repeat that job — these files are no longer on "
                        f"disk: {', '.join(f'{r} ({slots[r].name})' for r in gone)}")
        return
    _STATE[chat_id] = Job(slots=slots, probes=probes, pipeline=pipeline)
    tg.send_message(chat_id, f"reusing the last job's {len(slots)} file(s). "
                             "/pipeline to change the flow, then Run.")
    _maybe_show_manifest(tg, chat_id, _STATE[chat_id])


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
    # `dry_run` is threaded from the caller (main()'s --dry-run; False for real
    # usage and for every call in this file's own tests) all the way to the one
    # start_drain below. The CLI flag has to actually reach that line, or
    # "--dry-run: never invokes drain" in this module's docstring would be
    # false — and the button path has to thread it just as far as the typed one.
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
    stages = list(PIPELINES[job.pipeline])
    start_drain(manifest_path, dry_run=dry_run)
    # Clear in-memory state so the next file starts a fresh job rather
    # than mutating one already handed to a running drain. The manifest
    # itself, and the drain's own journal, stay on disk regardless.
    dropped = len(_PENDING.pop(chat_id, []) or [])
    # Copied to `.last.json` BEFORE the clear, so /again can rebuild it.
    # Deliberately not left in `.draft.json`: _load_draft reads that file, so a
    # restart would resurrect a job already handed to a running drain.
    submitted = _STATE.get(chat_id)
    if submitted is not None:
        _last_path(chat_id).write_text(json.dumps({
            "pipeline": submitted.pipeline,
            "slots": {r: str(v) for r, v in submitted.slots.items()},
            "probes": {r: asdict(pr) for r, pr in submitted.probes.items()},
        }, indent=2), encoding="utf-8")
    _STATE.pop(chat_id, None)
    _LAST_VALIDATE.pop(chat_id, None)
    _CONFIRM_WARNED.discard(chat_id)
    if dropped:
        tg.send_message(chat_id, f"running without {dropped} unassigned file(s)")
    tg.send_message(chat_id, "🚀 <b>Started.</b> Renting a GPU pod at "
                             "$0.99/hour now.\nI will keep the message below "
                             "updated and send the result when it finishes — "
                             "no need to ask.", parse_mode=PARSE_HTML)
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
        tg.send_chat_action(chat_id, "upload_document")
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
            # Not a dead end (2026-08-31). "nothing started" is true but
            # useless while a job is being assembled, which is most of the
            # time /status gets asked. Answer the question actually being put.
            body, buttons = _job_status(chat_id)
            tg.send_message(chat_id, f"💤 <b>Nothing running.</b>\n\n{body}",
                            buttons=buttons or None, parse_mode=PARSE_HTML)
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
        body, buttons = _job_status(chat_id)
        tg.send_message(chat_id, body, buttons=buttons or None,
                        parse_mode=PARSE_HTML)
        return

    if text.startswith("/clear"):
        if _STATE.get(chat_id) is None and not (_PENDING.get(chat_id) or []):
            tg.send_message(chat_id, "nothing to clear")
            return
        _ask_to_clear(tg, chat_id)
        return

    if text.startswith("/again"):
        _again(tg, chat_id)
        return

    if text.startswith("/pipeline"):
        job = _job_for(chat_id)
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            # Listing beats guessing: the names are close enough to each other
            # that "swap-character-enhance" — what the user actually asked for
            # on 2026-08-31 — is not any of them.
            # One button per pipeline: these are the names the user could not
            # guess (they asked for "swap-character-enhance", which is none of
            # them), so tapping beats typing.
            tg.send_message(
                chat_id,
                f"pipeline: {job.pipeline}\n\n" +
                "\n".join(f"{'* ' if n == job.pipeline else '  '}{n}"
                          f"  ({' -> '.join(PIPELINES[n])})"
                          for n in sorted(PIPELINES)),
                buttons=[[(n, _CB_PIPE + n)] for n in sorted(PIPELINES)
                         if n != job.pipeline])
            return
        _switch_pipeline_and_report(tg, chat_id, parts[1].strip())
        return

    if text.startswith("/start"):
        tg.send_message(
            chat_id,
            "Send each file as a File (in the picker: \"...\" -> Send as "
            "File). Videos are the driver; for an image I ask which slot, "
            "and you tap the answer.\n\n"
            "When every slot is filled I show the job and a Run button. "
            "Nothing spends money until you tap Run and confirm.\n\n"
            "/pipeline switch flow · /status progress · "
            "/result <name>.yaml · /tryon <batch-id>")
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

    tg = Tg(token=token, base_url=base)
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
            for update in tg.get_updates(offset):
                offset = update["update_id"] + 1
                handle(tg, update, allowed_user_id=allowed_user_id,
                       dry_run=args.dry_run)
            # After the updates, not instead of them: get_updates long-polls
            # for 50s, so this runs on that cadence with no timer of its own.
            # One chat, because the allowlist is one user (spec section 2).
            tick_progress(tg, allowed_user_id)
        except TgError as exc:
            log(f"poll failed, continuing: {exc}")
            time.sleep(5)
        except Exception as exc:            # one bad update must not end the bot
            log(f"update failed, continuing: {exc!r}")
        if args.once:
            return 0


if __name__ == "__main__":
    sys.exit(main())
