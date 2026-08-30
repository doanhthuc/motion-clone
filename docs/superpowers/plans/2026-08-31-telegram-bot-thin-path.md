# Telegram Bot — Thin Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send four files to a Telegram bot from a phone, confirm one screen, and get a finished video back — with the GPU pod rented and destroyed automatically in between.

**Architecture:** A long-polling bot on the same VPS as the watchdog, talking to a self-hosted `telegram-bot-api` server so results are not capped at 50MB on the way out and files arrive as paths on disk rather than downloads. It assembles one job, writes a manifest, shells out to the `make drain` built in Plan 1, and sends the result back. Zero third-party Python dependencies, matching `scripts/batchlib/`.

**Tech Stack:** Python 3 stdlib only (`urllib`, `json`, `hashlib`, `subprocess`), `unittest`, Docker (for `telegram-bot-api` only), systemd, `ffprobe`.

**Spec:** `docs/superpowers/specs/2026-08-30-telegram-batch-control-design.md`

This is **Plan 2A of the spec's Plan 2**, split because the whole of Plan 2 is ~11 tasks and its first half produces nothing runnable. 2A covers spec sections 4 (ingest and the quality gate), the single-job slice of 5, the confirmation half of 6, and 10 (delivery). **Plan 2B** covers what 2A deliberately omits: the multi-job basket, the live-editing card, named recipes, `/batches` and defer, and the stockout prompt.

## Depends on Plan 1 (merged as PR #18) — these are the real interfaces, verified 2026-08-31

```
make drain FILE=<path> [CONFIRM=yes] [RESUME=1]     # dry run without CONFIRM
scripts/drain.py         abs_max_min(manifest) -> int   ·  teardown(manifest_path)
scripts/batch_run.py     EXIT_NEEDS_POD = 3
scripts/batchlib_ext/lease.py      read_lease(path) -> Lease | None   (pod_id, provisioned_at, manifest, abs_max_min)
scripts/batchlib_ext/watchdog.py   DESTROYABLE_NAMES, GRACE_MIN = 10, decide(...), reconcile(...)
scripts/batchlib/manifest.py       load_manifest, load_state, state_path_for, validate_manifest
scripts/batchlib/pipelines.py      STAGES (timeout_min), PIPELINES
batch/pod-lease.json               gitignored; written by drain, read by the watchdog
```

`batch/pod-lease.json` existing is how the bot knows a pod is alive without asking RunPod.

## Global Constraints

- **Python 3 standard library only.** No `python-telegram-bot`, no `aiogram`, no `requests`. `scripts/batchlib/client.py` already speaks HTTP with `urllib` and multipart by hand; follow that file's idiom. The one exception is Docker, used solely to run the upstream `telegram-bot-api` binary.
- **Do not modify `scripts/batchlib/`.** Not one byte. Verify with `git status --short scripts/batchlib/`.
- **Do not modify `scripts/drain.py`, `scripts/pod_watchdog.py`, or `scripts/batchlib_ext/`.** Plan 1 shipped them under review; the bot is a caller, not an editor. If a task seems to need a change there, stop and escalate.
- **Comments in English.** Existing Vietnamese is legacy — do not translate or reflow it. No `# #region ALD <date>` markers.
- **Explain why, with the measured number and the date it was measured.** A citation must point at the line it claims. This branch's predecessor shipped four wrong ones and had to fix them.
- **Never print or log a bot token, an API key, or a chat id.** The repo is public and `motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit.
- **Money:** nothing in this plan may reach `pod-provision.sh` without an explicit human confirmation tap. `make drain` without `CONFIRM=yes` is a dry run and is the only form any test may invoke.
- Test file naming: `scripts/tests/test_batch_<module>.py`, discovered by `make batch-test`.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/tgbot/__init__.py` (create) | empty |
| `scripts/tgbot/tgclient.py` (create) | Telegram HTTP: `getUpdates`, `sendMessage`, `editMessageText`, `sendDocument`. Transport only, no bot logic. |
| `scripts/tgbot/ingest.py` (create) | What arrived, and is it usable? Photo rejection, HEIC conversion, `ffprobe` probe. No Telegram types. |
| `scripts/tgbot/job.py` (create) | Slot assignment and manifest generation for ONE job. Pure. |
| `scripts/tgbot/run.py` (create) | Invoke `make drain`, read the journal, format progress. |
| `scripts/tgbot/bot.py` (create) | The loop: dispatch updates, hold per-chat state, wire the modules. Thin. |
| `scripts/vps/telegram-bot-api.yml` (create) | docker-compose for the local Bot API server |
| `scripts/vps/motion-bot.service` (create) | systemd unit |
| `scripts/vps/README.md` (modify) | bot setup, the one-time `logOut`, and the new env vars |
| `Makefile` (modify) | `bot-dry` target |

**Why `tgclient` / `ingest` / `job` / `run` are separate from `bot`:** every one of them is testable without a Telegram account, and `bot.py` is the only file that cannot be. Plan 1's final review found three Critical defects in exactly the layer no test could reach; the smaller that layer is here, the less can hide in it.

### A deliberate departure from this repo's plan convention

Plan 1 gave every implementation step its complete code. Here, four steps (Task 3's
`to_png_if_heic`/`describe`/`suggest_preset`, and the implementation steps of Tasks 4, 5
and 6) give a precise specification and complete *tests* instead, without the
implementation written out.

That is a real reduction in the guarantee Plan 1 had, and it is a choice, not an
oversight. What justifies it: in every one of those cases the tests are complete and pin
the observable behaviour exactly, and the code is pure data-shaping with no external
seam. What does *not* get this treatment is anything that shells out or crosses a
process boundary — `probe()` below is written in full for exactly that reason, because
Plan 1's C1 (tier 3 never once executed) lived in a subprocess seam that seven task
reviews and 375 unit tests all missed.

If an implementer finds a spec here ambiguous enough to guess at, that is a defect in
this plan: stop and say so rather than picking an interpretation.

---

### Task 1: Telegram HTTP client

**Files:**
- Create: `scripts/tgbot/__init__.py` (empty), `scripts/tgbot/tgclient.py`
- Test: `scripts/tests/test_batch_tgclient.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `TgError(Exception)`, `Tg` class with `__init__(self, token: str, base_url: str)`, `call(self, method: str, **params) -> dict`, `send_message(chat_id: int, text: str) -> int` (returns `message_id`), `edit_message(chat_id: int, message_id: int, text: str) -> None`, `send_document(chat_id: int, path: Path, caption: str = "") -> None`, `get_updates(offset: int, timeout: int = 50) -> list[dict]`.

**Why a hand-rolled client:** `scripts/batchlib/client.py` already does exactly this shape against the pod API — `urllib.request`, a hand-built multipart body, `(status, bytes)` returned. Adding `python-telegram-bot` for five methods would be the first third-party dependency in a codebase that has none.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_tgclient.py
import json, sys, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.tgclient import Tg, TgError


class FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode()
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class TestTg(unittest.TestCase):
    def setUp(self):
        self.tg = Tg(token="TESTTOKEN", base_url="http://localhost:8081")

    def test_call_returns_the_result_field(self):
        with patch("urllib.request.urlopen",
                   return_value=FakeResponse({"ok": True, "result": {"message_id": 7}})):
            self.assertEqual(self.tg.call("sendMessage", chat_id=1, text="hi"),
                             {"message_id": 7})

    def test_not_ok_raises_with_the_description(self):
        # Telegram reports failure in the BODY with HTTP 200. Treating a 200 as
        # success would make every error silent.
        with patch("urllib.request.urlopen",
                   return_value=FakeResponse({"ok": False, "description": "chat not found"})):
            with self.assertRaises(TgError) as cm:
                self.tg.call("sendMessage", chat_id=1, text="hi")
        self.assertIn("chat not found", str(cm.exception))

    def test_token_never_appears_in_an_error_message(self):
        # The token is in the URL of every request. An exception that echoes the
        # URL would put it into logs, and this repo is public.
        with patch("urllib.request.urlopen",
                   return_value=FakeResponse({"ok": False, "description": "boom"})):
            with self.assertRaises(TgError) as cm:
                self.tg.call("sendMessage", chat_id=1)
        self.assertNotIn("TESTTOKEN", str(cm.exception))

    def test_send_message_returns_the_message_id(self):
        with patch("urllib.request.urlopen",
                   return_value=FakeResponse({"ok": True, "result": {"message_id": 42}})):
            self.assertEqual(self.tg.send_message(chat_id=1, text="hi"), 42)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_tgclient.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tgbot'`

- [ ] **Step 3: Implement**

```python
# scripts/tgbot/tgclient.py
"""Telegram Bot API over urllib. Transport only — no bot logic lives here.

Hand-rolled rather than python-telegram-bot because scripts/batchlib/ has zero
third-party dependencies and this needs five methods. scripts/batchlib/client.py
is the model: urllib.request, a hand-built multipart body, errors raised rather
than returned.
"""
from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class TgError(Exception):
    """A Telegram call failed. Never carries the token."""


class Tg:
    def __init__(self, token: str, base_url: str):
        self._token = token
        self._base = base_url.rstrip("/")

    def _url(self, method: str) -> str:
        return f"{self._base}/bot{self._token}/{method}"

    def _scrub(self, text: str) -> str:
        """Remove the token from anything on its way to a message or a log.

        Not belt-and-braces. The token is in the URL of every request, and
        exceptions raised by urllib quote that URL: a base_url missing its
        scheme makes Request() raise `ValueError: unknown url type: '<url>'`
        with the token inline. This repo is public, and a bot main loop that
        does `except Exception as e: log(e)` is the normal shape.
        """
        return text.replace(self._token, "<token>") if self._token else text

    def call(self, method: str, **params) -> dict:
        """POST a JSON call. Raises TgError on ok:false or on a non-2xx body.

        Telegram reports failures in the response BODY, and it ALSO sets a real
        HTTP status: 400 for "message is not modified", 401, 403, 409, 429 and
        so on. urlopen raises HTTPError on those before any body check runs, so
        a client that only handles URLError never sees the description at all.
        scripts/batchlib/client.py:103-105 already solves this — read the body
        off the HTTPError — and this mirrors it.
        """
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        try:
            req = urllib.request.Request(self._url(method), data=data,
                                         headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                raw = exc.read()        # Telegram's JSON error body lives here
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TgError(self._scrub(f"{method} failed: {exc!r}")) from exc
        try:
            body = json.loads(raw)
        except ValueError as exc:
            raise TgError(self._scrub(f"{method} returned non-JSON: {raw[:200]!r}")) from exc
        if not body.get("ok"):
            raise TgError(self._scrub(f"{method} rejected: {body.get('description', body)}"))
        return body.get("result")

    def send_message(self, chat_id: int, text: str) -> int:
        return int(self.call("sendMessage", chat_id=chat_id, text=text)["message_id"])

    def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=text)
        except TgError as exc:
            # Editing to identical text is an error in the Bot API and is
            # meaningless here — the progress loop re-renders on a timer.
            if "message is not modified" not in str(exc):
                raise

    def send_document(self, chat_id: int, path: Path, caption: str = "") -> None:
        """sendDocument, never sendVideo.

        sendVideo may let Telegram re-encode for streaming, and the quality work
        in this repo measures background chroma, skin exposure and hair jitter.
        A document still plays when tapped — it just plays the original bytes.
        """
        boundary = uuid.uuid4().hex
        fields = {"chat_id": str(chat_id)}
        if caption:
            fields["caption"] = caption
        body = bytearray()
        for key, value in fields.items():
            body += (f"--{boundary}\r\n"
                     f'content-disposition: form-data; name="{key}"\r\n\r\n'
                     f"{value}\r\n").encode()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body += (f"--{boundary}\r\n"
                 f'content-disposition: form-data; name="document"; filename="{path.name}"\r\n'
                 f"content-type: {ctype}\r\n\r\n").encode()
        body += path.read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            self._url("sendDocument"), data=bytes(body),
            headers={"content-type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                result = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TgError(f"sendDocument failed: {exc!r}") from exc
        if not result.get("ok"):
            raise TgError(f"sendDocument rejected: {result.get('description', result)}")

    def get_updates(self, offset: int, timeout: int = 50) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout) or []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_tgclient.py' -v`
Expected: 4 tests PASS

- [ ] **Step 5: Confirm nothing protected was touched**

Run: `git status --short scripts/batchlib/ scripts/batchlib_ext/ scripts/drain.py scripts/pod_watchdog.py`
Expected: no output

- [ ] **Step 6: Commit**

```bash
git add scripts/tgbot/__init__.py scripts/tgbot/tgclient.py scripts/tests/test_batch_tgclient.py
git commit -m "Telegram client over urllib, no new dependencies

Five methods do not justify the first third-party dependency in a
codebase that has none; batchlib/client.py is the model.

Telegram reports failures in the response body with HTTP 200, so ok:false
is checked explicitly — and TgError never includes the URL, because the
token is in it and this repo is public."
```

---

### Task 2: Local Bot API server, the bot skeleton, and the byte-fidelity measurement

**Files:**
- Create: `scripts/vps/telegram-bot-api.yml`, `scripts/vps/motion-bot.service`, `scripts/tgbot/bot.py`
- Modify: `scripts/vps/README.md`, `Makefile`
- Test: `scripts/tests/test_batch_bot.py`

**Interfaces:**
- Consumes: `Tg`, `TgError` from Task 1.
- Produces: `allowed(update: dict, allowed_user_id: int) -> bool`, `handle(tg, update: dict, *, allowed_user_id: int, state: dict) -> None`, and the executable `python3 scripts/tgbot/bot.py [--once] [--dry-run]`.

**This task carries the measurement the whole design rests on.** Spec section 13's first unknown: does Telegram preserve bytes for a `.MP4` sent as File? Everything in spec section 4 assumes yes. The `/sha` behaviour below answers it through the real channel rather than in a lab, and it is not throwaway — it is the skeleton the later tasks extend.

**Why a local Bot API server is still right, for a corrected reason:** the plan first justified it by claiming this user's drivers are 20-30MB against a 20MB download cap. Measured 2026-08-31, that was false — every driver in `~/Desktop/materials/drivers/` is 1.3-15MB and would have passed. The cap that binds is the **50MB upload** on the way back: the largest delivered video in `out/` is **41MB**, an 18% margin, and `targetRes: 2k` crosses it. Independently of any cap, a local server makes `getFile` return an **absolute path on disk**, so the bot reads what the client uploaded instead of downloading it back.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_bot.py
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.bot import allowed

ME = 12345


def update_from(user_id: int) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": user_id}, "text": "/start"}}


class TestAllowed(unittest.TestCase):
    def test_the_owner_is_allowed(self):
        self.assertTrue(allowed(update_from(ME), ME))

    def test_anyone_else_is_refused(self):
        # This whitelist is the only thing between a stranger and a button that
        # rents a $0.99/hour GPU. It must be an allowlist, never a blocklist.
        self.assertFalse(allowed(update_from(99999), ME))

    def test_an_update_with_no_sender_is_refused(self):
        # channel_post, edited_channel_post and service updates have no
        # message.from. Defaulting those to allowed would open the door.
        self.assertFalse(allowed({"channel_post": {"chat": {"id": ME}}}, ME))

    def test_a_malformed_update_is_refused_not_crashed(self):
        self.assertFalse(allowed({}, ME))
        self.assertFalse(allowed({"message": {}}, ME))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tgbot.bot'`

- [ ] **Step 3: Write `scripts/tgbot/bot.py`**

```python
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
```

- [ ] **Step 4: Write the compose file**

```yaml
# scripts/vps/telegram-bot-api.yml
# Measured 2026-08-31: this user's drivers are 1.3-15MB, so the public API's
# 20MB DOWNLOAD cap would not have bitten — an earlier claim that it would was
# never measured. The cap that binds is the 50MB UPLOAD on the way back: the
# largest video delivered so far is 41MB, and targetRes: 2k crosses it.
# A local server lifts both to 2GB AND makes getFile return an absolute path on
# disk, so the bot reads the file instead of downloading it back.
#
# TELEGRAM_API_ID / TELEGRAM_API_HASH come from https://my.telegram.org (an app
# registration, not the bot token). Put them in scripts/vps/bot-api.env, which
# is gitignored.
services:
  telegram-bot-api:
    image: ghcr.io/gramiojs/telegram-bot-api:latest
    restart: unless-stopped
    env_file: bot-api.env
    environment:
      TELEGRAM_LOCAL: "1"
    ports:
      - "127.0.0.1:8081:8081"
    volumes:
      - ./telegram-bot-api-data:/var/lib/telegram-bot-api
```

- [ ] **Step 5: Write the systemd unit**

```ini
# scripts/vps/motion-bot.service
# Install: cp to /etc/systemd/system/, then
#   systemctl daemon-reload && systemctl enable --now motion-bot
[Unit]
Description=Motion Telegram bot
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/motion-clone
ExecStart=/usr/bin/python3 /opt/motion-clone/scripts/tgbot/bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 6: Add the Makefile target and gitignore the compose secrets**

```makefile
bot-dry: ## One polling round against the local Bot API, invoking no jobs
	@python3 scripts/tgbot/bot.py --once --dry-run
```

Add `bot-dry` to `.PHONY` at `Makefile:2` by **appending** — `watchdog-dry` and `drain` are already there and must survive. Add to `.gitignore`: `scripts/vps/bot-api.env` and `scripts/vps/telegram-bot-api-data/`.

- [ ] **Step 7: Run the tests and the gates**

```bash
python3 -m unittest discover -s scripts/tests -p 'test_batch_bot.py' -v
make batch-test
motions-studio/setup/scrub-secrets.sh --check
git status --short scripts/batchlib/ scripts/batchlib_ext/ scripts/drain.py scripts/pod_watchdog.py
```
Expected: all green; the last command prints nothing.

- [ ] **Step 8: THE MEASUREMENT — run it and record the result**

This is the step the plan exists to reach, and it needs a real phone. Do not mark this task complete without pasting the output.

1. `docker compose -f scripts/vps/telegram-bot-api.yml up -d`, then confirm `curl -s http://127.0.0.1:8081/bot<token>/getMe` returns `"ok":true`.
2. **One-time and irreversible in one direction:** call `logOut` against `api.telegram.org` for this bot *before* pointing it at the local server, or it will silently stop receiving updates. `curl -s "https://api.telegram.org/bot<token>/logOut"`.
3. Start the bot. From the iPhone, send **the same file twice**: once via the Photos tab, once via `paperclip -> File`.
   - The Photos one must be refused with the compression message.
   - The File one must come back with a `sha256` and a byte count.
4. On the Mac, `shasum -a 256 <the original file>` and `stat -f %z <the original file>`.
5. Do this for **an image and a ~25MB `.mp4`**.

Record in the report: the two digests per file, whether they match, and the byte counts.

**If the digests match:** the design's central assumption holds; continue to Task 3.
**If they do not:** STOP and report. Spec section 4 is invalidated and the ingest channel has to change before anything else is built — the rest of this plan would be built on sand. Do not work around it.

- [ ] **Step 9: Commit**

```bash
git add scripts/tgbot/bot.py scripts/vps/telegram-bot-api.yml scripts/vps/motion-bot.service scripts/vps/README.md Makefile .gitignore scripts/tests/test_batch_bot.py
git commit -m "Bot skeleton, local Bot API server, and the byte-fidelity probe

Measured 2026-08-31: the drivers are 1.3-15MB, so the 20MB download cap would
not have bitten — the earlier claim that it would was never measured. What
binds is the 50MB upload cap on the way back, against a largest-delivered 41MB.
A local server lifts both to 2GB and makes getFile return a path on disk.

/sha answers spec section 13's first unknown through the real channel
rather than in a lab: does Telegram preserve bytes for a .MP4 sent as
File? Everything in spec section 4 assumes yes. The probe is not
throwaway — it is the skeleton the remaining tasks extend.

The allowlist is one user id and refuses updates with no sender:
channel_post carries no message.from, and defaulting that to allowed
would let anyone who can post where the bot can see spend money."
```

---

### Task 3: The ingest quality gate

**Files:**
- Create: `scripts/tgbot/ingest.py`
- Test: `scripts/tests/test_batch_ingest.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (deliberately — no Telegram types here).
- Produces: `Probe` (frozen dataclass: `kind: str` — `"image"` or `"video"`, `width: int`, `height: int`, `duration_s: float`, `bitrate_kbps: int`, `size_bytes: int`), `probe(path: Path) -> Probe`, `describe(p: Probe) -> str`, `suggest_preset(duration_s: float) -> str`, `to_png_if_heic(path: Path) -> Path`.

**Why measure at all:** neither Telegram nor a browser upload can be trusted by faith — iOS Safari's `<input type=file>` transcodes unpredictably, and a mis-tapped Photos send compresses silently. The answer is not to pick a "safe" channel but to verify on arrival, before any GPU is rented. This is the same principle as `make batch-validate` and `make gpu-preflight`: free gates that fire before spending.

**A second thing falls out of measuring.** `docs/batch-runner.md` currently warns that "preset is chosen from each driver's *real* `ffprobe`, make does not measure it" — the user runs `ffprobe` by hand and types the preset. The bot measures, so it proposes.

`suggest_preset` maps duration to the valid values in `scripts/batch-params.json`: `drv-5s` `drv-10s` `drv-15s` `drv-20s` `drv-30s`. Pick the smallest preset whose ceiling is >= duration; anything longer than 30s gets `drv-30s` and a warning, because motion lowers fps and keeps the length while character-swap trims the tail.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_ingest.py
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.ingest import Probe, describe, suggest_preset


class TestSuggestPreset(unittest.TestCase):
    def test_picks_the_smallest_preset_that_fits(self):
        self.assertEqual(suggest_preset(4.2), "drv-5s")
        self.assertEqual(suggest_preset(5.0), "drv-5s")
        self.assertEqual(suggest_preset(14.8), "drv-15s")
        self.assertEqual(suggest_preset(15.0), "drv-15s")

    def test_over_thirty_seconds_still_maps_to_the_ceiling(self):
        # 34.1s is a real driver from batch/2026-08-28-lanczos-6cap.yaml. There
        # is no larger preset, and the job still runs: motion lowers fps and
        # keeps the length, character-swap trims the tail.
        self.assertEqual(suggest_preset(34.1), "drv-30s")

    def test_boundary_is_inclusive_at_the_ceiling(self):
        self.assertEqual(suggest_preset(30.0), "drv-30s")
        self.assertEqual(suggest_preset(30.1), "drv-30s")


class TestDescribe(unittest.TestCase):
    def test_video_line_carries_the_numbers_that_reveal_recompression(self):
        line = describe(Probe(kind="video", width=1080, height=1920, duration_s=30.1,
                              bitrate_kbps=12400, size_bytes=22_800_000))
        for token in ("1080x1920", "30.1", "12400", "22.8"):
            self.assertIn(token, line)

    def test_image_line_omits_duration_and_bitrate(self):
        line = describe(Probe(kind="image", width=1024, height=1536, duration_s=0.0,
                              bitrate_kbps=0, size_bytes=412_000))
        self.assertIn("1024x1536", line)
        self.assertNotIn("kbps", line)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_ingest.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tgbot.ingest'`

- [ ] **Step 3: Implement `scripts/tgbot/ingest.py`**

`probe()` is the one function here that talks to an external binary, so it gets written out in full — that seam is exactly where Plan 1's C1 hid for seven task reviews and 375 tests:

```python
PRESET_CEILINGS = [(5, "drv-5s"), (10, "drv-10s"), (15, "drv-15s"),
                   (20, "drv-20s"), (30, "drv-30s")]

# An allowlist, so anything unrecognised is treated as video and gets the
# duration check below. The inverse — treating unrecognised codecs as images —
# would let a video skip that check silently.
IMAGE_CODECS = frozenset({"mjpeg", "png", "bmp", "webp"})


def probe(path: Path) -> Probe:
    """Measure a file with ffprobe. Raises rather than guessing.

    An unprobeable file must never be silently accepted: the next thing that
    happens to it is a $0.99/hour render, and a wrong duration picks a wrong
    preset. Failing loudly here costs a message; failing quietly costs a batch.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe is not installed — see scripts/vps/README.md") from exc
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {path.name}: {out.stderr.strip()[:200]}")
    try:
        data = json.loads(out.stdout)
        streams = data["streams"]
        fmt = data.get("format", {})
        v = next(s for s in streams if s.get("codec_type") == "video")
        duration = float(fmt.get("duration") or v.get("duration") or 0.0)
        # A still image is a one-frame "video" stream to ffprobe. Distinguish by
        # the codec, not by the file extension: a .mov holding a live photo and
        # a .jpg both lie about themselves by name.
        #
        # An earlier version also treated `duration == 0.0` as evidence of an
        # image. Measured 2026-08-31 with a real 2-second raw h264 stream: some
        # containers carry no duration at all, so that clause classified a
        # genuine video as an image — and describe() then omits duration and
        # bitrate, hiding the anomaly from the confirmation screen instead of
        # showing it. Codec alone decides the kind now.
        kind = "image" if v.get("codec_name") in IMAGE_CODECS else "video"
        if kind == "video" and duration <= 0.0:
            # Loud, not a guess. suggest_preset() would silently return drv-5s
            # for an unmeasured driver and truncate a 30-second one, after the
            # pod was already rented. A truncated upload is the likely cause.
            raise RuntimeError(
                f"{path.name}: ffprobe reported no duration for a "
                f"{v.get('codec_name')} video stream. A preset cannot be chosen "
                f"without it — the file may be truncated; send it again.")
        return Probe(kind=kind,
                     width=int(v.get("width") or 0), height=int(v.get("height") or 0),
                     duration_s=duration,
                     bitrate_kbps=int(int(fmt.get("bit_rate") or 0) / 1000),
                     size_bytes=int(fmt.get("size") or path.stat().st_size))
    except (ValueError, KeyError, StopIteration) as exc:
        raise RuntimeError(f"ffprobe output for {path.name} was not usable: {exc!r}") from exc
```

`to_png_if_heic` shells to `sips -s format png` on macOS or `ffmpeg` on Linux, returns the original path unchanged for non-HEIC input, and the caller records in the job card that a conversion happened. `describe` renders the one-line summary the tests pin. `suggest_preset` walks `PRESET_CEILINGS` and falls through to `drv-30s`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_ingest.py' -v`
Expected: 6 tests PASS

- [ ] **Step 5: Probe two real files and paste the output**

Run `probe()` against one real image and one real video from `~/Desktop/materials/` and paste the resulting `describe()` lines into your report. A unit test with synthetic `Probe` values proves the formatting, not the parsing — and the parsing is the half that talks to `ffprobe`.

- [ ] **Step 6: Commit**

```bash
git add scripts/tgbot/ingest.py scripts/tests/test_batch_ingest.py
git commit -m "Ingest gate: measure on arrival, never trust the channel

Neither Telegram nor a browser upload can be trusted by faith, so verify
rather than choose a 'safe' channel: a 4K driver arriving as 720p at
1.1 Mbps is visible before any GPU is rented. Same principle as
batch-validate and gpu-preflight — free gates that fire before spending.

Measuring also retires a manual step: docs/batch-runner.md says the
preset is chosen from each driver's real ffprobe and make does not
measure it. The bot measures, so it proposes."
```

---

### Task 4: One job, one manifest

**Files:**
- Create: `scripts/tgbot/job.py`
- Test: `scripts/tests/test_batch_job.py`

**Interfaces:**
- Consumes: `Probe`, `suggest_preset` from Task 3.
- Produces: `Job` (dataclass: `slots: dict[str, Path]`, `probes: dict[str, Probe]`, `pipeline: str`), `slot_for(probe: Probe, job: Job) -> str | None`, `missing_slots(job: Job) -> list[str]`, `render_manifest(job: Job, *, now) -> str`, `write_manifest(job: Job, path: Path, *, now) -> None`.

**Slot inference, and where it must stop.** A video is always the `driver` — that is structural, not a guess, because `driver` is the only slot that takes video. An image is ambiguous between `character`, `outfit` and `background`, so `slot_for` returns `None` and the caller asks with buttons. Guessing an image slot from a filename would burn a real $1 batch on a mislabeled outfit.

**The manifest the bot writes must be byte-identical to what the user approved**, and must carry the `ffprobe` numbers as comments with the date — matching what the user writes by hand today, so the file is still self-explanatory six weeks later. The runner still never writes it; only the bot does, once, before the run.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_job.py
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tgbot.ingest import Probe
from tgbot.job import Job, missing_slots, render_manifest, slot_for

VIDEO = Probe(kind="video", width=1080, height=1920, duration_s=14.8,
              bitrate_kbps=12400, size_bytes=22_000_000)
IMAGE = Probe(kind="image", width=1024, height=1536, duration_s=0.0,
              bitrate_kbps=0, size_bytes=412_000)


class TestSlotInference(unittest.TestCase):
    def test_a_video_is_always_the_driver(self):
        # Structural, not a guess: driver is the only slot that takes video.
        self.assertEqual(slot_for(VIDEO, Job(slots={}, probes={}, pipeline="motion-enhance")), "driver")

    def test_an_image_is_ambiguous_and_must_be_asked(self):
        # Guessing character vs outfit vs background from a filename would burn
        # a real $1 batch on a mislabeled outfit.
        self.assertIsNone(slot_for(IMAGE, Job(slots={}, probes={}, pipeline="tryon-motion-enhance")))


class TestMissingSlots(unittest.TestCase):
    def test_motion_enhance_needs_character_and_driver(self):
        job = Job(slots={}, probes={}, pipeline="motion-enhance")
        self.assertEqual(sorted(missing_slots(job)), ["character", "driver"])

    def test_background_is_optional_for_tryon(self):
        job = Job(slots={"character": Path("/c.png"), "outfit": Path("/o.png"),
                         "driver": Path("/d.mp4")},
                  probes={}, pipeline="tryon-motion-enhance")
        self.assertEqual(missing_slots(job), [])


class TestRenderManifest(unittest.TestCase):
    def test_carries_the_measured_numbers_as_comments(self):
        job = Job(slots={"character": Path("/c.png"), "driver": Path("/d.mp4")},
                  probes={"driver": VIDEO}, pipeline="motion-enhance")
        text = render_manifest(job, now="2026-08-31 21:40")
        self.assertIn("2026-08-31 21:40", text)
        self.assertIn("14.8", text)          # the measurement, not a guess
        self.assertIn("drv-15s", text)       # the preset it implies

    def test_output_is_valid_yaml_the_existing_loader_accepts(self):
        import tempfile, yaml
        job = Job(slots={"character": Path("/c.png"), "driver": Path("/d.mp4")},
                  probes={"driver": VIDEO}, pipeline="motion-enhance")
        data = yaml.safe_load(render_manifest(job, now="2026-08-31 21:40"))
        self.assertEqual(data["runs"][0]["pipeline"], "motion-enhance")
        self.assertIn("character", data["runs"][0]["inputs"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest discover -s scripts/tests -p 'test_batch_job.py' -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tgbot.job'`

- [ ] **Step 3: Implement**

`missing_slots` derives required slots from `PIPELINES[job.pipeline]` and `STAGES[...].inputs` in `scripts/batchlib/pipelines.py` — import them, never hardcode a slot list. A `material:x?` entry is optional. `render_manifest` emits the header comment block (generation time, and one line per probed input with its measured numbers and the preset they imply), then `runs:` with the one job.

- [ ] **Step 4: Run tests to verify they pass**

Expected: 6 tests PASS.

- [ ] **Step 5: Prove the generated manifest survives the real validator**

Write a rendered manifest to a temp path and run `make batch-validate FILE=<that path>`. It must exit 0. A manifest that only `yaml.safe_load` accepts is not proof — `validate_manifest` is what stands between a typo and a paid run, and it is the gate the bot will rely on.

- [ ] **Step 6: Commit**

```bash
git add scripts/tgbot/job.py scripts/tests/test_batch_job.py
git commit -m "Assemble one job and render its manifest

A video is always the driver — structural, not a guess, since driver is
the only slot that takes video. An image stays ambiguous and is asked
about: guessing outfit from a filename would burn a real \$1 batch.

Required slots derive from PIPELINES and STAGES rather than a hardcoded
list, so a new pipeline cannot silently acquire the wrong slots.

The manifest carries the ffprobe numbers as dated comments, matching what
the user writes by hand today so the file still explains itself later."
```

---

### Task 5: Confirm, drain, and report progress

**Files:**
- Create: `scripts/tgbot/run.py`
- Test: `scripts/tests/test_batch_tgrun.py`

**Interfaces:**
- Consumes: `Job` from Task 4; `load_state`, `state_path_for` from `batchlib.manifest`; `read_lease` from `batchlib_ext.lease`.
- Produces: `estimate_minutes(job) -> int`, `progress_text(manifest_path: Path, *, lease) -> str`, `start_drain(manifest_path: Path, *, dry_run: bool) -> subprocess.Popen`, `drain_running(manifest_path: Path) -> bool`.

**The money gate lives here, and it is a human tap.** `[Run]` does not rent anything: it renders the manifest, runs `make batch-validate` (free), and shows the result with an estimate. Only `[Confirm]` invokes `make drain … CONFIRM=yes`. Everything after that is automatic — which is exactly what makes the tap safe to be the only gate.

**Progress is one edited message**, re-rendered about every 30 seconds. Telegram rate-limits edits, and a message per stage would bury the batch in scrollback.

**`progress_text` reads the journal, not the pod.** `load_state` is the same source `batch_status` uses, so it stays correct after the pod is destroyed.

- [ ] **Step 1: Write the failing test**

```python
# scripts/tests/test_batch_tgrun.py
import sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.manifest import state_path_for
from tgbot.run import drain_running, progress_text

STATE = {"batch": "2026-08-31-2140",
         "runs": {"job": {"status": "running",
                          "stages": {"motion": {"status": "done", "sec": 247},
                                     "enhance": {"status": "running"}}}}}


class TestProgressText(unittest.TestCase):
    def setUp(self):
        self.manifest = Path(tempfile.mkdtemp()) / "m.yaml"
        self.manifest.write_text("runs: []", encoding="utf-8")
        import json
        state_path_for(self.manifest).write_text(json.dumps(STATE), encoding="utf-8")

    def test_names_every_stage_and_its_status(self):
        text = progress_text(self.manifest, lease=None)
        self.assertIn("motion", text)
        self.assertIn("enhance", text)

    def test_reads_the_journal_so_it_still_works_after_the_pod_is_gone(self):
        # lease=None means no pod. The journal is the source of truth, exactly
        # as batch_status uses it, so progress stays reportable after destroy.
        text = progress_text(self.manifest, lease=None)
        self.assertIn("2026-08-31-2140", text)

    def test_no_state_file_is_reported_not_crashed(self):
        empty = Path(tempfile.mkdtemp()) / "none.yaml"
        empty.write_text("runs: []", encoding="utf-8")
        self.assertIsInstance(progress_text(empty, lease=None), str)


class TestDrainRunning(unittest.TestCase):
    def test_false_when_no_lease_and_no_process(self):
        m = Path(tempfile.mkdtemp()) / "m.yaml"
        m.write_text("runs: []", encoding="utf-8")
        self.assertFalse(drain_running(m))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with `ModuleNotFoundError: No module named 'tgbot.run'`

- [ ] **Step 3: Implement**

`start_drain` builds `["make", "drain", f"FILE={manifest_path}"]` and appends `CONFIRM=yes` **only** when `dry_run` is False, then `subprocess.Popen` with output redirected to a log file beside the manifest. `estimate_minutes` sums `STAGES[s].timeout_min`-independent measured figures — use the numbers recorded in `docs/batch-runner.md` section 7 (tryon 351s, motion 247s, enhance 114s for a 15s driver) and say in a comment that they are measured medians, not ceilings.

- [ ] **Step 4: Run tests to verify they pass**

Expected: 4 tests PASS.

- [ ] **Step 5: Prove the dry-run path cannot spend money**

Run `start_drain(<a temp manifest>, dry_run=True)`, wait for it, and paste the output: it must print the `DRY RUN` line from `drain.py` and must not contain `CONFIRM`. Then `grep -rn "CONFIRM" scripts/tgbot/` and confirm the only occurrence is guarded by `if not dry_run`.

- [ ] **Step 6: Commit**

```bash
git add scripts/tgbot/run.py scripts/tests/test_batch_tgrun.py
git commit -m "Confirm gate, drain invocation, and one edited progress message

[Run] rents nothing: it renders the manifest, runs batch-validate free,
and shows an estimate. Only [Confirm] passes CONFIRM=yes. Putting the
whole money decision on one tap is what makes fully automatic execution
after it acceptable.

Progress reads the journal rather than the pod — the same source
batch_status uses — so it stays correct after the pod is destroyed."
```

---

### Task 6: Delivery and failure reporting

**Files:**
- Modify: `scripts/tgbot/run.py`, `scripts/tgbot/bot.py`
- Test: `scripts/tests/test_batch_tgrun.py` (append)

**Interfaces:**
- Consumes: `failed_job_ids` from `scripts/drain.py` (import it; do not reimplement).
- Produces: `final_files(batch_dir: Path) -> list[Path]`, `summary_text(batch_dir: Path) -> str`.

Both take the **batch output directory** (`out/<batch-id>/`), not the manifest path — the
tests pass `batch` directly. Task 5's `progress_text` takes a manifest path instead because
it derives the journal through `state_path_for`; these two read files that already live
under the batch directory, so making them re-derive it would add a lookup for nothing.

**Results go out with `sendDocument`, never `sendVideo`.** `sendVideo` may let Telegram re-encode for streaming, and this repo's quality work measures background chroma, skin exposure and hair jitter. Nobody may judge quality on a re-encode. A document still plays when tapped — it plays the original bytes.

**On failure, the pod-side logs are already local.** Plan 1's `teardown` pulls `GET /jobs/:id/logs` and the worker log into `out/<batch>/runs/<run>/` *before* destroying the pod, so the bot only has to attach what is already on disk. It must not try to reach the pod.

- [ ] **Step 1: Write the failing test**

```python
# append to scripts/tests/test_batch_tgrun.py
from tgbot.run import final_files, summary_text


class TestDelivery(unittest.TestCase):
    def test_final_files_lists_only_the_final_directory(self):
        root = Path(tempfile.mkdtemp())
        batch = root / "out" / "2026-08-31-2140"
        (batch / "_final").mkdir(parents=True)
        (batch / "runs" / "job").mkdir(parents=True)
        (batch / "_final" / "job.mp4").write_bytes(b"x" * 200_000)
        (batch / "runs" / "job" / "02-motion.mp4").write_bytes(b"y" * 200_000)
        found = final_files(batch)
        self.assertEqual([p.name for p in found], ["job.mp4"])

    def test_summary_names_the_failed_run_and_its_local_log(self):
        # teardown already pulled the pod logs down before destroying the pod,
        # so the bot attaches what is on disk and never reaches for the pod.
        root = Path(tempfile.mkdtemp())
        batch = root / "out" / "b"
        (batch / "runs" / "job").mkdir(parents=True)
        (batch / "runs" / "job" / "pod-job.log").write_text("boom", encoding="utf-8")
        text = summary_text(batch)
        self.assertIn("job", text)
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL with `ImportError: cannot import name 'final_files'`

- [ ] **Step 3: Implement, and wire delivery into `bot.py`**

`final_files` globs `<batch>/_final/*.mp4` only — never `runs/`, which holds intermediates. `summary_text` reads `_index.tsv` for per-stage seconds and bytes. Send each final file with `send_document`, then the summary. On failure, also send `pod-job.log` and `run.log` as documents.

Add a `/tryon` command that sends `runs/<run>/01-tryon.png` on request rather than by default: when the final video looks wrong the first question is always what try-on produced, and that should be one tap rather than an SSH session — but sending it every time is noise.

- [ ] **Step 4: Run every gate**

```bash
make batch-test
make check-job-types && make check-comfy-nodes && make check-batch-params
motions-studio/setup/scrub-secrets.sh --check
git status --short scripts/batchlib/ scripts/batchlib_ext/ scripts/drain.py scripts/pod_watchdog.py
```
Expected: all green; the last prints nothing.

- [ ] **Step 5: Commit**

```bash
git add scripts/tgbot/run.py scripts/tgbot/bot.py scripts/tests/test_batch_tgrun.py
git commit -m "Deliver results as documents, never as video

sendVideo may let Telegram re-encode for streaming, and this repo's
quality work measures background chroma, skin exposure and hair jitter.
Nobody may judge quality on a re-encode; a document still plays when
tapped, it just plays the original bytes.

On failure the pod logs are already local — Plan 1's teardown pulls them
before destroying the pod — so the bot attaches what is on disk and never
reaches for a pod that is gone."
```

---

## Acceptance — needs a phone, and one of them costs money

- [ ] **A1.** Task 2 Step 8's `sha256` measurement, for an image and a ~25MB video. **Everything else is built on this.**
- [ ] **A2.** A file sent via the Photos tab is refused with the compression message.
- [ ] **A3.** A full single-job run end to end: four files in, confirm, video back. This rents a pod — expect roughly the measured `docs/batch-runner.md` section 7 figures.
- [ ] **A4.** During A3, confirm `batch/pod-lease.json` appears and then disappears, and that `make watchdog-dry` reports the pod while it is alive.
- [ ] **A5.** Send a message from a second Telegram account and confirm the bot ignores it completely — no reply, no log entry naming it.
- [ ] **A6.** Compare the delivered file's `sha256` against `out/<batch>/_final/<run>.mp4` on the VPS. They must match; if they do not, `sendDocument` is not doing what this plan claims.

A6 is the mirror of A1 and is the one most likely to be skipped. The whole delivery argument rests on it.

---

## Notes for Plan 2B

2B adds, on top of these interfaces: the multi-job basket, the live-editing job card (one message edited in place rather than a transcript), named recipes in `batch/recipes/*.yaml`, `/batches` with defer-and-resume, and the RTX 5090 stockout prompt with its four buttons.

2B is not blocked on anything 2A does not already answer.
