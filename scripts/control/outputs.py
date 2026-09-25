"""Finished outputs: `out/<batch>/_final/*` only.

`runs/<run>/NN-stage.*` are per-stage intermediates; the runner promotes only
a run's LAST stage into `_final/` (runner.py `_finalize`). Serving an
intermediate as if it were the result is worse than serving nothing — the
user cannot tell them apart (same reasoning as tgbot.run.final_files).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from control.materials import MaterialError, render_frame
from control.paths import safe_child

OUTPUT_SUFFIXES = frozenset({".mp4", ".mov", ".png", ".jpg", ".jpeg", ".webp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov"})
# Posters live beside the files they show, in `_final/.posters/`, so removing
# a batch directory removes its posters with it; prune_posters covers a single
# file deleted by hand. A subdirectory, never loose files in `_final/`: a
# `<name>.jpg` there would be listed as an output of its own.
POSTER_DIR = ".posters"
# The app's grid is 3 columns on a ~400pt-wide phone at 3x, so ~400 px a tile.
POSTER_WIDTH = 480


def _final_dir(out_dir: Path, batch: str) -> Path | None:
    batch_dir = safe_child(out_dir, batch)
    if batch_dir is None:
        return None
    # safe_child resolves symlinks while validating the target stays inside
    # out_dir, so by this point batch_dir already points PAST a symlink like
    # out/latest (runner.py ~371-374 keeps that one pointed at the newest
    # batch). Check the un-resolved entry directly: a symlinked batch dir is
    # excluded outright, not just de-duplicated, so a batch never gets served
    # under two different names.
    if (out_dir / batch.strip()).is_symlink():
        return None
    final = batch_dir / "_final"
    return final if final.is_dir() else None


def _final_files(final: Path) -> list[Path]:
    return sorted(p for p in final.iterdir()
                  if p.is_file() and p.suffix.lower() in OUTPUT_SUFFIXES)


def final_names(out_dir: Path, batch: str) -> list[str]:
    final = _final_dir(out_dir, batch)
    return [p.name for p in _final_files(final)] if final else []


def list_outputs(out_dir: Path) -> list[dict]:
    listed = []
    for batch_dir in out_dir.iterdir() if out_dir.is_dir() else []:
        if batch_dir.is_symlink():
            # out/latest -> newest batch dir (runner.py ~371-374). is_dir()
            # below follows symlinks, so without this check "latest" was
            # listed as a second, duplicate entry for the newest batch.
            continue
        final = batch_dir / "_final"
        if not batch_dir.is_dir() or not final.is_dir():
            continue
        files = _final_files(final)
        if files:
            listed.append({"batch": batch_dir.name,
                           # The newest file, not `_final/`'s own mtime: creating
                           # `.posters/` in an old batch touches the directory and
                           # would jump that batch to the top of the list.
                           "updated_at": max(p.stat().st_mtime for p in files),
                           "files": [_file_entry(p) for p in files]})
    return sorted(listed, key=lambda b: b["updated_at"], reverse=True)


def resolve_output(out_dir: Path, batch: str, name: str) -> Path | None:
    final = _final_dir(out_dir, batch)
    if final is None:
        return None
    path = safe_child(final, name)
    if path is None or not path.is_file() or path.suffix.lower() not in OUTPUT_SUFFIXES:
        return None
    return path


def _file_entry(path: Path) -> dict:
    entry = {"name": path.name, "bytes": path.stat().st_size}
    if path.suffix.lower() in VIDEO_SUFFIXES:
        entry["duration"] = _duration(path)
    return entry


def _duration(path: Path) -> float | None:
    """Seconds, from ffprobe once per file version, then from a sidecar.

    ffprobe reads only the container header of a local file, but the outputs
    list is fetched on every pull-to-refresh, so the answer is kept in
    `.posters/<name>.json` keyed by the file's mtime.
    """
    sidecar = path.parent / POSTER_DIR / f"{path.name}.json"
    mtime = path.stat().st_mtime
    try:
        cached = json.loads(sidecar.read_text())
        if cached.get("mtime") == mtime:
            return cached.get("duration")
    except (OSError, ValueError, AttributeError):
        pass
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None                      # not cached: a later call may succeed
    try:
        duration = float(out.stdout.strip()) if out.returncode == 0 else None
    except ValueError:
        duration = None
    try:
        sidecar.parent.mkdir(exist_ok=True)
        tmp = sidecar.with_name(sidecar.name + ".tmp")
        tmp.write_text(json.dumps({"mtime": mtime, "duration": duration}))
        os.replace(tmp, sidecar)
    except OSError:
        pass
    return duration


def poster(out_dir: Path, batch: str, name: str) -> Path:
    """A JPEG frame of one output, made on first request and kept until the file changes."""
    src = resolve_output(out_dir, batch.strip(), name.strip())
    if src is None:
        raise MaterialError("not_found", "no such output")
    dest = src.parent / POSTER_DIR / f"{src.name}.jpg"
    if dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
        return dest
    if not render_frame(src, dest, POSTER_WIDTH, video=src.suffix.lower() in VIDEO_SUFFIXES):
        raise MaterialError("unprobeable", f"no poster for {src.name}")
    return dest


def prune_posters(out_dir: Path) -> list[Path]:
    """Posters and duration sidecars whose output file is gone."""
    removed = []
    for posters in out_dir.glob(f"*/_final/{POSTER_DIR}") if out_dir.is_dir() else []:
        if posters.parent.parent.is_symlink() or not posters.is_dir():
            continue
        for item in posters.iterdir():
            source = item.name.removesuffix(".jpg").removesuffix(".json")
            if item.suffix in (".jpg", ".json") and not (posters.parent / source).is_file():
                item.unlink(missing_ok=True)
                removed.append(item)
    return removed
