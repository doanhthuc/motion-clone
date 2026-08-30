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
# Absolute, NOT `from .tgclient import ...`. This file runs as
# `python3 scripts/tgbot/bot.py`, i.e. as __main__, where a relative import
# raises ImportError regardless of sys.path. The insert above puts scripts/ on
# the path, which is what makes the absolute form work from either entry point.
from tgbot.tgclient import Tg, TgError

ROOT = Path(__file__).resolve().parents[2]


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

    if (msg.get("text") or "").startswith("/start"):
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
