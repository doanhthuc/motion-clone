"""Image Studio projects (spec 2026-09-26-image-studio-design.md).

Shaped like control/tryon_library.py: one JSON index per owner, read and
written fresh under control.LOCK on every call, rewritten atomically. Files
live under studio_dir/<owner>/<project_id>/{refs,img}/ — outside out/, so
batch-clean never touches them. References are COPIED in at submit time:
materials are pruned after 7 days and library entries can be deleted, and
neither may break a project's history or its Retry.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

import control
from control.paths import safe_child

TITLE_MAX = 80
_UNFINISHED = ("queued", "running")


class StudioError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _suffix_for(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _aggregate(slots: list[dict]) -> str:
    if any(s["status"] in _UNFINISHED for s in slots):
        return "running"
    return "done" if any(s["status"] == "done" for s in slots) else "error"


def _spent(project: dict) -> float:
    return round(sum(g.get("unit_price_usd", 0.0) * sum(s["status"] == "done" for s in g["slots"])
                     for g in project["generations"]), 4)


class StudioStore:
    def __init__(self, studio_dir: Path, owner: str):
        self.studio_dir, self.owner = studio_dir, owner
        self._index_path = studio_dir / f"{owner}.json"
        self._files_dir = studio_dir / owner

    # -- persistence -------------------------------------------------------

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, projects: list[dict]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_name(f"{self._index_path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(projects, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._index_path)

    @staticmethod
    def _find(projects: list[dict], pid: str) -> dict:
        project = next((p for p in projects if p.get("id") == pid), None)
        if project is None:
            raise StudioError("not_found", "no such studio project")
        return project

    def _dir(self, pid: str) -> Path | None:
        return safe_child(self._files_dir, pid)

    @staticmethod
    def _view(project: dict) -> dict:
        view = json.loads(json.dumps(project))
        for gen in view["generations"]:
            gen["status"] = _aggregate(gen["slots"])
            gen["est_cost_usd"] = round(gen["unit_price_usd"] * gen["count"], 4)
        view["spent_usd"] = _spent(project)
        return view

    # -- projects ------------------------------------------------------------

    def list_projects(self) -> list[dict]:
        with control.LOCK:
            projects = self._load()
        out = []
        for p in projects:
            images = [s["image"] for g in p["generations"] for s in g["slots"] if s.get("image")]
            out.append({"id": p["id"], "title": p["title"], "created_at": p["created_at"],
                        "updated_at": p["updated_at"], "cover": images[-1] if images else None,
                        "spent_usd": _spent(p), "image_count": len(images)})
        return sorted(out, key=lambda p: p["updated_at"], reverse=True)

    def create_project(self, title: str = "") -> dict:
        now = time.time()
        project = {"id": uuid.uuid4().hex[:12], "title": str(title or "").strip()[:TITLE_MAX],
                   "created_at": now, "updated_at": now, "generations": []}
        with control.LOCK:
            projects = self._load()
            projects.append(project)
            self._save(projects)
        return self._view(project)

    def get_project(self, pid: str) -> dict:
        with control.LOCK:
            return self._view(self._find(self._load(), pid))

    def rename(self, pid: str, title: str) -> dict:
        with control.LOCK:
            projects = self._load()
            project = self._find(projects, pid)
            project["title"] = str(title or "").strip()[:TITLE_MAX]
            project["updated_at"] = time.time()
            self._save(projects)
            return self._view(project)

    def delete_project(self, pid: str) -> None:
        with control.LOCK:
            projects = self._load()
            self._find(projects, pid)
            self._save([p for p in projects if p["id"] != pid])
        directory = self._dir(pid)
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    # -- generations ---------------------------------------------------------

    def add_generation(self, pid: str, *, prompt: str, model: str, aspect: str, count: int,
                       refs: list[tuple[dict, Path]], unit_price_usd: float,
                       copy=shutil.copyfile) -> dict:
        gid = uuid.uuid4().hex[:10]
        directory = self._dir(pid)
        if directory is None:
            raise StudioError("not_found", "no such studio project")
        ref_dir = directory / "refs"
        ref_dir.mkdir(parents=True, exist_ok=True)
        snapshots = []
        for i, (ref, src) in enumerate(refs):        # slow copies stay outside the lock
            # Only PNG/JPEG snapshots: mime_of() derives the MIME type from the suffix, and both
            # providers take these two. `copy` (fit_image) converts anything else.
            suffix = ".jpg" if src.suffix.lower() in (".jpg", ".jpeg") else ".png"
            dest = ref_dir / f"{gid}-{i}{suffix}"
            copy(src, dest)
            snapshots.append({"kind": ref["kind"], "id": ref["id"], "file": dest.name})
        now = time.time()
        gen = {"id": gid, "created_at": now, "prompt": prompt, "model": model, "aspect": aspect,
               "count": count, "refs": snapshots, "unit_price_usd": unit_price_usd,
               "slots": [{"status": "queued"} for _ in range(count)]}
        with control.LOCK:
            projects = self._load()
            project = self._find(projects, pid)
            project["generations"].append(gen)
            project["updated_at"] = now
            self._save(projects)
            return self._view(project)["generations"][-1]

    def generation(self, pid: str, gid: str) -> dict:
        project = self.get_project(pid)
        gen = next((g for g in project["generations"] if g["id"] == gid), None)
        if gen is None:
            raise StudioError("not_found", "no such generation")
        return gen

    def set_slot(self, pid: str, gid: str, slot: int, *, status: str,
                 image: bytes | None = None, error: str | None = None) -> None:
        image_name = None
        if image is not None:
            directory = self._dir(pid)
            if directory is None or not directory.is_dir():
                raise StudioError("not_found", "no such studio project")
            (directory / "img").mkdir(exist_ok=True)
            image_name = f"{gid}-{slot}{_suffix_for(image)}"
            (directory / "img" / image_name).write_bytes(image)
        with control.LOCK:
            projects = self._load()
            project = self._find(projects, pid)
            gen = next((g for g in project["generations"] if g["id"] == gid), None)
            if gen is None or not 0 <= slot < len(gen["slots"]):
                raise StudioError("not_found", "no such generation slot")
            entry = {"status": status}
            if image_name:
                entry["image"] = Path(image_name).stem
            if error:
                entry["error"] = error[:500]
            gen["slots"][slot] = entry
            project["updated_at"] = time.time()
            self._save(projects)

    def resolve_image(self, pid: str, image_id: str) -> Path | None:
        directory = self._dir(pid)
        img_dir = directory / "img" if directory is not None else None
        if img_dir is None or not img_dir.is_dir() or safe_child(img_dir, image_id) is None:
            return None
        return next((p for p in img_dir.iterdir() if p.stem == image_id and p.is_file()), None)

    def resolve_ref(self, pid: str, file: str) -> Path | None:
        directory = self._dir(pid)
        path = safe_child(directory / "refs", file) if directory is not None else None
        return path if path is not None and path.is_file() else None

    def recover_interrupted(self) -> int:
        """Called once at bot start: the threads that owned unfinished slots are gone."""
        changed = 0
        with control.LOCK:
            projects = self._load()
            for p in projects:
                for g in p["generations"]:
                    for i, s in enumerate(g["slots"]):
                        if s["status"] in _UNFINISHED:
                            g["slots"][i] = {"status": "error", "error": "interrupted"}
                            changed += 1
            if changed:
                self._save(projects)
        return changed
