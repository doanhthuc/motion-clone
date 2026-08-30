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

    def call(self, method: str, **params) -> dict:
        """POST a JSON call. Raises TgError on ok:false.

        Telegram reports failures in the response BODY with HTTP 200, so the
        status code alone proves nothing.
        """
        data = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        req = urllib.request.Request(self._url(method), data=data,
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            # Deliberately does not include the URL: the token is in it.
            raise TgError(f"{method} failed: {exc!r}") from exc
        if not body.get("ok"):
            raise TgError(f"{method} rejected: {body.get('description', body)}")
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
