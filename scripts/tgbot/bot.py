#!/usr/bin/env python3
"""The bot loop. Thin: dispatch, per-chat state, and wiring — no logic.

    python3 scripts/tgbot/bot.py            # long-poll forever
    python3 scripts/tgbot/bot.py --once     # one getUpdates round, for testing
    python3 scripts/tgbot/bot.py --dry-run  # never invokes drain

Reads TG_BOT_TOKEN, TG_ALLOWED_USER_ID and TG_API_BASE from the root .env.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
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
from tgbot.run import drain_running, estimate_minutes, final_files, start_drain, summary_text

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
# The one file parked while its slot is ambiguous (an image — a video is
# never ambiguous, see job.slot_for). Keyed by chat_id, same as _STATE.
_PENDING_SLOT: dict[int, tuple[Path, Probe]] = {}


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} bot: {msg}", flush=True)


def allowed(update: dict, allowed_user_id: int) -> bool:
    """Allowlist of exactly one user id.

    Absence of a sender means refuse. channel_post and service updates carry no
    message.from, and defaulting them to allowed would let anyone who can post
    where this bot can see spend money.
    """
    msg = update.get("message") or {}
    sender = msg.get("from") or {}
    return sender.get("id") == allowed_user_id


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
    manifest_path = _job_manifest_path(chat_id)
    write_manifest(job, manifest_path, now=time.strftime("%Y-%m-%d %H:%M:%S"))

    result = subprocess.run(
        ["make", "batch-validate", f"FILE={manifest_path}"],
        cwd=_REPO_ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        tg.send_message(chat_id,
                        "manifest failed validation — nothing will run:\n"
                        f"{(result.stdout + result.stderr).strip()}")
        return

    minutes = estimate_minutes(job)
    tg.send_message(
        chat_id,
        manifest_path.read_text(encoding="utf-8") +
        f"\nestimated {minutes} min (measured once on one batch — not a promise)\n"
        "This will rent a GPU pod at $0.99/hour. Reply /confirm to spend money "
        "and start, or nothing yet costs anything.")


def handle(tg: Tg, update: dict, *, allowed_user_id: int, state: dict,
           dry_run: bool = False) -> None:
    if not allowed(update, allowed_user_id):
        return                              # silent: do not confirm the bot exists
    msg = update["message"]
    chat_id = msg["chat"]["id"]

    if msg.get("photo"):
        # Telegram re-encodes anything sent through the photo path. Accepting it
        # would put a silently degraded image into a $0.99/hour render.
        tg.send_message(chat_id,
                        "That was sent as a photo, so Telegram already compressed it.\n"
                        "Send it again with the paperclip -> File.")
        return

    doc = msg.get("document")
    if doc:
        path = Path(tg.call("getFile", file_id=doc["file_id"])["file_path"])
        path = to_png_if_heic(path)
        try:
            p = probe(path)
        except RuntimeError as exc:
            # ffprobe raises rather than guessing (ingest.probe's own
            # contract) — the file never enters a job, so it can never
            # silently pick a wrong preset or a wrong slot.
            tg.send_message(chat_id, str(exc))
            return

        job = _STATE.setdefault(chat_id, Job(slots={}, probes={}, pipeline=JOB_PIPELINE))
        role = slot_for(p, job)
        if role is None:
            # Ambiguous (an image): job.slot_for already refuses to guess —
            # park it and ask, rather than reintroducing a filename heuristic
            # here. The answer arrives as a later plain-text message.
            _PENDING_SLOT[chat_id] = (path, p)
            options = " / ".join(_askable_roles(job.pipeline))
            tg.send_message(chat_id, f"{describe(p)}\nWhich slot is this? Reply: {options}")
            return

        # A video: structural, always `driver` — no question needed.
        job.slots[role] = path
        job.probes[role] = p
        tg.send_message(chat_id, f"{role}: {describe(p)}")
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
        _STATE.pop(chat_id, None)
        _PENDING_SLOT.pop(chat_id, None)
        tg.send_message(chat_id,
                        "started — renting a GPU pod at $0.99/hour now.\n"
                        f"Check back with /result {manifest_path.name} once it's done.")
        return

    if text.startswith("/start"):
        tg.send_message(chat_id, "Ready. Send a file with the paperclip -> File.")
        return

    # A plain-text reply, meant to answer the question asked when the last
    # file was an ambiguous image. Anything not a recognised slot name is
    # re-asked, never guessed — mirrors job.slot_for's own refusal to guess.
    pending = _PENDING_SLOT.get(chat_id)
    if pending is not None:
        job = _STATE.setdefault(chat_id, Job(slots={}, probes={}, pipeline=JOB_PIPELINE))
        options = _askable_roles(job.pipeline)
        answer = text.strip().lower()
        if answer in options:
            path, p = pending
            job.slots[answer] = path
            job.probes[answer] = p
            del _PENDING_SLOT[chat_id]
            tg.send_message(chat_id, f"{answer}: {describe(p)}")
            _maybe_show_manifest(tg, chat_id, job)
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
    state: dict = {}
    offset = 0
    log(f"started, api={base}, dry_run={args.dry_run}")
    while True:
        try:
            for update in tg.get_updates(offset):
                offset = update["update_id"] + 1
                handle(tg, update, allowed_user_id=allowed_user_id, state=state,
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
