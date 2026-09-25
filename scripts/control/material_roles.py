"""Which role a material plays — driver, character, outfit or background.

A file on disk does not say: an image is equally an outfit or a character
(tgbot/job.py's slot_for asks rather than guesses). The phone groups its
material library by role (2026-09-25), so the answer comes from, in order:

1. a tag — written when the material is put into a draft slot, or set by
   hand from the phone (`PUT /v1/materials/{id}/role`);
2. history — the role it filled in a batch manifest the bot rendered
   (`batch/*.yaml`, newest wins), which covers every Telegram-era job;
3. structure — a video can only ever be the driver (slot_for again).

Otherwise None, and the phone shows it as unsorted until someone says.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

ROLES = ("driver", "character", "outfit", "background")

# job.py's render_manifest writes `      <role>: <path>` as a plain scalar.
_MANIFEST_SLOT = re.compile(r"^\s*(driver|character|outfit|background):\s*(\S+)\s*$")


class MaterialRoleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class MaterialRoles:
    def __init__(self, path: Path, batch_dir: Path, staging_root: Path):
        self.path, self.batch_dir = path, batch_dir
        self.staging_root = staging_root.resolve()
        self._lock = threading.Lock()

    # -- tags ---------------------------------------------------------------
    def _tags(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if v in ROLES} if isinstance(data, dict) else {}

    def _write(self, tags: dict[str, str]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tags, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)

    def set(self, material_id: str, role: str | None) -> None:
        """Tag by hand (role) or drop the tag (None) so history decides again."""
        if role is not None and role not in ROLES:
            raise MaterialRoleError("bad_request", f"role must be one of {', '.join(ROLES)} or null")
        with self._lock:
            tags = self._tags()
            if role is None:
                tags.pop(material_id, None)
            else:
                tags[material_id] = role
            self._write(tags)

    def remember(self, material_id: str, role: str) -> None:
        """A draft slot just took this material: that is what it is now."""
        if role in ROLES:
            self.set(material_id, role)

    def forget(self, material_id: str) -> None:
        with self._lock:
            tags = self._tags()
            if tags.pop(material_id, None) is not None:
                self._write(tags)

    # -- reading ------------------------------------------------------------
    def _history(self) -> dict[str, str]:
        """material id -> role from rendered manifests, newest file last so it wins."""
        found: dict[str, str] = {}
        manifests = sorted(self.batch_dir.glob("*.yaml"), key=lambda p: p.stat().st_mtime)
        for manifest in manifests:
            try:
                lines = manifest.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for line in lines:
                match = _MANIFEST_SLOT.match(line)
                if not match:
                    continue
                try:
                    rel = Path(match.group(2)).resolve().relative_to(self.staging_root)
                except (ValueError, OSError):
                    continue          # ../.smoke/... and anything else outside staging
                if len(rel.parts) == 2:
                    found["/".join(rel.parts)] = match.group(1)
        return found

    def annotate(self, items: list[dict]) -> list[dict]:
        """Add `role` to each `list_materials` item."""
        tags, history = self._tags(), self._history()
        for item in items:
            item["role"] = (tags.get(item["id"]) or history.get(item["id"])
                            or ("driver" if item.get("kind") == "video" else None))
        return items
