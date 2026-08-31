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
        """POST a JSON call. Raises TgError on ok:false.

        Telegram reports failures in the response BODY with HTTP 200, so the
        status code alone proves nothing. Telegram also returns real HTTP
        statuses (400, 401, 403, 429) with the error JSON in the body, so
        HTTPError.read() must be checked before raising.
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

    @staticmethod
    def keyboard(rows: list[list[tuple[str, str]]]) -> dict:
        """An inline keyboard from [[(label, callback_data), ...], ...].

        callback_data is capped at 64 BYTES by the Bot API, and a button that
        exceeds it makes the whole sendMessage fail — so the caller's data
        scheme has to stay short. Asserted here rather than trusted: the
        failure is a rejected message, i.e. the user sees nothing at all.
        """
        for row in rows:
            for label, data in row:
                encoded = data.encode()
                if len(encoded) > 64:
                    raise ValueError(
                        f"callback_data for {label!r} is {len(encoded)} bytes, "
                        "max 64")
        return {"inline_keyboard": [[{"text": label, "callback_data": data}
                                     for label, data in row] for row in rows]}

    def send_message(self, chat_id: int, text: str, *,
                     buttons: list[list[tuple[str, str]]] | None = None,
                     parse_mode: str | None = None) -> int:
        """Send text, optionally with an inline keyboard and formatting.

        parse_mode="HTML" is opt-in per call, and HTML rather than MarkdownV2
        on purpose: MarkdownV2 requires escaping fifteen characters including
        `.`, `-` and `!`, all of which occur in ordinary filenames and
        measurements. An unescaped one makes Telegram reject the WHOLE message,
        so the user sees nothing at all. HTML needs only &, < and > escaped.
        Callers must still escape every dynamic value they interpolate.
        """
        markup = self.keyboard(buttons) if buttons else None
        return int(self.call("sendMessage", chat_id=chat_id, text=text,
                             reply_markup=markup,
                             parse_mode=parse_mode)["message_id"])

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        """Show "typing…"/"uploading…" while something slow happens.

        Best-effort: this is feedback, not work. ffprobe on a 25MB video and
        `make batch-validate` both take long enough that a silent bot looks
        stuck, and this is the only in-band way to say "still here" without
        sending a message that has to be scrolled past afterwards.
        """
        try:
            self.call("sendChatAction", chat_id=chat_id, action=action)
        except TgError:
            pass

    def answer_callback_query(self, callback_id: str, text: str = "") -> None:
        """Acknowledge a button press.

        Not optional: until this is called the client shows a spinner on the
        button and eventually reports the bot as unresponsive, even when the
        work succeeded. Errors are swallowed on purpose — the acknowledgement
        is cosmetic, and a callback id expires after ~15 minutes, so a failure
        here must not abort the handler that already did the real work.
        """
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id,
                      text=text or None)
        except TgError:
            pass

    def edit_message(self, chat_id: int, message_id: int, text: str, *,
                     parse_mode: str | None = None) -> None:
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id,
                      text=text, parse_mode=parse_mode)
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
        try:
            req = urllib.request.Request(
                self._url("sendDocument"), data=bytes(body),
                headers={"content-type": f"multipart/form-data; boundary={boundary}"})
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            with exc:
                raw = exc.read()        # Telegram's JSON error body lives here
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TgError(self._scrub(f"sendDocument failed: {exc!r}")) from exc
        try:
            result = json.loads(raw)
        except ValueError as exc:
            raise TgError(self._scrub(f"sendDocument returned non-JSON: {raw[:200]!r}")) from exc
        if not result.get("ok"):
            raise TgError(self._scrub(f"sendDocument rejected: {result.get('description', result)}"))

    def get_updates(self, offset: int, timeout: int = 50) -> list[dict]:
        return self.call("getUpdates", offset=offset, timeout=timeout) or []
