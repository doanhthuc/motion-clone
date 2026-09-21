"""Idempotency-Key records for the money-spending routes (spec §5.5).

A phone retries. A record is written as "pending" before the action runs and
replaced by the response after it, so a retry replays the answer instead of
acting twice — and a retry of a call that died midway gets "outcome unknown",
never a second attempt at renting a pod.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from pathlib import Path

MAX_KEY_LEN = 200
_LOCK = threading.Lock()


class IdempotencyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class IdempotencyStore:
    def __init__(self, root: Path, ttl_sec: int = 24 * 3600):
        self.root, self.ttl_sec = root, ttl_sec

    def _path(self, scope: str, key) -> Path:
        if not isinstance(key, str) or not key or len(key) > MAX_KEY_LEN:
            raise IdempotencyError("bad_request",
                                   f"Idempotency-Key must be 1-{MAX_KEY_LEN} characters")
        digest = hashlib.sha256(f"{scope}\0{key}".encode()).hexdigest()[:40]
        return self.root / f"{scope}-{digest}.json"

    def _write(self, path: Path, record: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(record), encoding="utf-8")
        tmp.replace(path)

    def begin(self, scope: str, key) -> tuple[int, dict] | None:
        """None: first time — a pending record now exists, go ahead.
        Otherwise the (status, body) to answer with instead of acting."""
        path = self._path(scope, key)
        with _LOCK:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._write(path, {"state": "pending", "key_hint": key[:40]})
                return None
            except (OSError, ValueError):
                record = {"state": "pending"}
        if record.get("state") == "done":
            return record["status"], record["body"]
        return 409, {"error": {"code": "outcome_unknown",
                               "message": "an earlier request with this Idempotency-Key did not "
                                          "finish; read the run's status before trying again"}}

    def finish(self, scope: str, key: str, status: int, body: dict) -> None:
        with _LOCK:
            self._write(self._path(scope, key), {"state": "done", "status": status, "body": body,
                                                  "key_hint": key[:40]})

    def forget(self, scope: str, key: str) -> None:
        with _LOCK:
            self._path(scope, key).unlink(missing_ok=True)

    def prune(self, now: float) -> int:
        removed = 0
        for path in self.root.glob("*.json") if self.root.is_dir() else []:
            try:
                if now - path.stat().st_mtime > self.ttl_sec:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return removed
