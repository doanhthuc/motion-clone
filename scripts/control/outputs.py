"""Finished outputs: `out/<batch>/_final/*` only.

`runs/<run>/NN-stage.*` are per-stage intermediates; the runner promotes only
a run's LAST stage into `_final/` (runner.py `_finalize`). Serving an
intermediate as if it were the result is worse than serving nothing — the
user cannot tell them apart (same reasoning as tgbot.run.final_files).
"""
from __future__ import annotations

from pathlib import Path

from control.paths import safe_child

OUTPUT_SUFFIXES = frozenset({".mp4", ".mov", ".png", ".jpg", ".jpeg", ".webp"})


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
                           "updated_at": final.stat().st_mtime,
                           "files": [{"name": p.name, "bytes": p.stat().st_size} for p in files]})
    return sorted(listed, key=lambda b: b["updated_at"], reverse=True)


def resolve_output(out_dir: Path, batch: str, name: str) -> Path | None:
    final = _final_dir(out_dir, batch)
    if final is None:
        return None
    path = safe_child(final, name)
    if path is None or not path.is_file() or path.suffix.lower() not in OUTPUT_SUFFIXES:
        return None
    return path
