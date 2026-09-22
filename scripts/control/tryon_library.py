"""Try-on images that outlive one draft/manifest (spec §5.10, slice 6).

Opt-in: an entry exists only because POST /v1/tryon-library was called —
never written automatically on every Phase A preview. Storage is
deliberately outside out/, so batch-clean and _final pruning can never
remove a saved entry. Shaped like control/drafts.py's DraftStore: one JSON
index per owner, read and written fresh on every call under control.LOCK
rather than cached in memory.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

import control
from control.paths import safe_child


class TryonLibraryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class TryonLibrary:
    def __init__(self, library_dir: Path, owner: str):
        self.library_dir, self.owner = library_dir, owner
        self._index_path = library_dir / f"{owner}.json"
        self._image_dir = library_dir / owner

    # -- persistence -------------------------------------------------------

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, entries: list[dict]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_name(f"{self._index_path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        tmp.replace(self._index_path)

    # -- reads ---------------------------------------------------------------

    def list(self) -> list[dict]:
        with control.LOCK:
            # public copies only — callers never see the internal image_name field
            return [{k: v for k, v in e.items() if k != "image_name"} for e in self._load()]

    def resolve_image(self, entry_id: str) -> Path | None:
        with control.LOCK:
            record = next((e for e in self._load() if e.get("id") == entry_id), None)
        if record is None:
            return None
        path = safe_child(self._image_dir, str(record.get("image_name") or ""))
        if path is None or not path.is_file():
            return None
        return path

    # -- mutations -------------------------------------------------------

    def save(self, *, image: Path, material_ids: dict, provider: str) -> dict:
        """Copy `image` into the library and record it. A copy, not a move
        or an alias: the source is whatever `tryon_image` handed back — a
        file inside a chat's `out/` tree that a later batch-clean can prune
        — and this entry must outlive that (§5.10's whole point)."""
        entry_id = uuid.uuid4().hex[:12]
        with control.LOCK:
            self._image_dir.mkdir(parents=True, exist_ok=True)
            dest = self._image_dir / f"{entry_id}{image.suffix or '.png'}"
            shutil.copy2(image, dest)
            record = {"id": entry_id, "owner": self.owner, "material_ids": dict(material_ids),
                      "provider": provider, "saved_at": time.time(), "image_name": dest.name}
            entries = self._load()
            entries.append(record)
            self._save(entries)
        return {k: v for k, v in record.items() if k != "image_name"}

    def delete(self, entry_id: str) -> None:
        with control.LOCK:
            entries = self._load()
            record = next((e for e in entries if e.get("id") == entry_id), None)
            if record is None:
                raise TryonLibraryError("not_found", "no such try-on library entry")
            self._save([e for e in entries if e.get("id") != entry_id])
            image_name = str(record.get("image_name") or "")
            if image_name:
                path = safe_child(self._image_dir, image_name)
                if path is not None:
                    path.unlink(missing_ok=True)
