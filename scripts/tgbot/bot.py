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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.config import env_get
from batchlib.manifest import load_state, state_path_for
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
from tgbot.run import final_files, summary_text

ROOT = Path(__file__).resolve().parents[2]

# Every manifest this bot renders (tgbot/job.py:render_manifest) hardcodes a
# single run id, "job" — Plan 2A is the single-job slice, multi-job baskets
# are Plan 2B. Hardcoding it here matches that, rather than inventing a run
# selector for a run that never has more than one name.
SOLE_RUN_ID = "job"


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


def deliver_result(tg: Tg, chat_id: int, manifest_path: Path) -> None:
    """Send the finished video(s) back, or the failure diagnostics already on disk.

    Reached on request via the `/result` command in `handle()`, not by an
    automatic drain-completion callback: no earlier task gives bot.py a way to
    know which manifest is "the active one" for a chat (Task 5 shipped only
    the functions in tgbot/run.py — it never wired a /run or /confirm handler
    into bot.py), so there is nothing to hang an automatic trigger off yet.
    This is the reachable "close the loop" hook until that tracking exists.

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


def handle(tg: Tg, update: dict, *, allowed_user_id: int, state: dict) -> None:
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
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        size = path.stat().st_size
        tg.send_message(chat_id,
                        f"{doc.get('file_name', '?')}\n"
                        f"{size} bytes\nsha256 {digest}")
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
        batch_id = parts[1].strip()
        # SOLE_RUN_ID: Plan 2A's manifests only ever have one run, "job".
        path = ROOT / "out" / batch_id / "runs" / SOLE_RUN_ID / "01-tryon.png"
        if not path.exists():
            tg.send_message(chat_id, f"no try-on image found for {batch_id}")
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
        manifest_path = Path(parts[1].strip())
        if not manifest_path.is_absolute():
            manifest_path = ROOT / "batch" / manifest_path
        if not manifest_path.exists():
            tg.send_message(chat_id, f"no manifest at {manifest_path.name}")
            return
        deliver_result(tg, chat_id, manifest_path)
        return

    if text.startswith("/start"):
        tg.send_message(chat_id, "Ready. Send a file with the paperclip -> File.")


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
                handle(tg, update, allowed_user_id=allowed_user_id, state=state)
        except TgError as exc:
            log(f"poll failed, continuing: {exc}")
            time.sleep(5)
        except Exception as exc:            # one bad update must not end the bot
            log(f"update failed, continuing: {exc!r}")
        if args.once:
            return 0


if __name__ == "__main__":
    sys.exit(main())
