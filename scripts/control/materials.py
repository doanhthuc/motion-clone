"""Material on the VPS: naming, staging, and (Task 2) listing and previews.

Telegram-free: shared by the bot (tgbot/bot.py wraps these) and the HTTP API.
Directories are parameters, never derived here, so the bot can keep deriving
them from its own ROOT (which its tests patch).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import unicodedata
from pathlib import Path

from control.paths import safe_child
import tgbot.run as run_mod
from tgbot import ingest as ingest_mod

# Filenames are re-spelled into this alphabet before they are written or put
# into a manifest. job.py's render_manifest emits `      <slot>: <path>` as a
# PLAIN (unquoted) YAML scalar, so a space or a ": " anywhere in that line
# would produce a manifest that parses wrong or not at all — and job.py is
# protected by this branch's constraints, so quoting cannot be added there.
#
# What this actually guarantees is narrower than "the emitted line is always
# valid plain YAML" (finding E, 2026-08-31). The line job.py emits is the whole
# staged path — ROOT/batch/tg-staging/<chat_id>/<name> — and only the last
# component passes through here. The rest is the checkout directory, an
# unchecked assumption: clone this repo into "~/My Projects/motion clone" and
# every manifest this bot writes breaks, with nothing in this file to catch it.
# Sanitising only the filename is still the right split — ROOT is
# developer-chosen and inspected once, the filename is user-chosen, arbitrary
# and arrives at $0.99/hour — but the assumption is an assumption, not a proof.
#
# Restricting the alphabet also means a staged name can never contain a path
# separator or "..", the same property _safe_child() checks for.
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# `đ` (U+0111) is a distinct letter, not `d` plus a combining mark, so NFD
# leaves it whole and the filter below would delete it. It is the only such
# case in Vietnamese: ă â ê ô ơ ư and every tone mark do decompose, so this
# one pair is the whole table rather than the start of one.
_TRANSLIT = {"đ": "d", "Đ": "D", "ð": "d", "Ð": "D"}


def fold_diacritics(text: str) -> str:
    """Drop accents while keeping the letter under them.

    Without this, `_SAFE_NAME_RE` turned `áo dài.jpg` into `o_d_i.jpg`
    (measured 2026-08-31) — the accented letters are outside `[A-Za-z0-9._-]`,
    so they were deleted rather than folded. That matters beyond tidiness: the
    manifest is what the user reads on a phone before confirming a $0.99/hour
    render, and a file they cannot recognise is a file they cannot check is the
    right one. Staging exists partly to make those names readable, and this is
    what makes it true for the Vietnamese names this repo's material actually
    uses.

    NFD splits a base letter from its combining marks; dropping the marks keeps
    the letter. `unicodedata` is stdlib, so this adds no dependency.

    **NFD, never NFKD, and that is a security choice rather than a stylistic
    one.** NFKD also applies compatibility mappings, which turn fullwidth forms
    into their ASCII equivalents — measured 2026-08-31: `unicodedata.normalize
    ("NFKD", "／")` is `"/"` and `("NFKD", "．")` is `"."`. Folding with NFKD
    would therefore MANUFACTURE path separators and dots out of input that
    contained none, upstream of `_SAFE_NAME_RE` and of every reason
    `_safe_child()` gives for refusing them. Under NFD those characters are
    left alone and the filter replaces them with "_", which is the whole point.
    Do not "improve" this to NFKD for better folding.
    """
    text = "".join(_TRANSLIT.get(ch, ch) for ch in text)
    return "".join(ch for ch in unicodedata.normalize("NFD", text)
                   if not unicodedata.combining(ch))


def safe_name(name: str) -> str:
    """A user-supplied filename, re-spelled into `_SAFE_NAME_RE`'s alphabet.

    Stem and extension are re-spelled SEPARATELY (finding C, 2026-08-31).
    Doing the whole basename in one pass and then stripping "._-" off the ends
    destroyed the extension whenever the stem re-spelled to nothing:
    `_safe_name('写真.heic')` returned `'heic'`, a name with no suffix at all,
    so `to_png_if_heic` never fired and `probe` then rejected the file. This
    user's material comes from a Vietnamese-language workflow, so a non-Latin
    stem is the ordinary case; the extension is the part downstream code
    actually dispatches on, so it is the part that must survive.
    """
    base = fold_diacritics(Path(name).name)
    stem = _SAFE_NAME_RE.sub("_", Path(base).stem).strip("._-")
    # lstrip(".") first so the separating dot is re-added below rather than
    # stripped away with the rest — ".heic" -> "heic" -> ".heic".
    suffix = _SAFE_NAME_RE.sub("_", Path(base).suffix.lstrip(".")).strip("._-")
    return f"{stem or 'file'}.{suffix}" if suffix else (stem or "file")


# Name reservation and the write that claims the name must be one step: the
# bot's poll loop and the HTTP thread both stage files now, and two threads
# that each see "photo.png is free" would both write it.
_STAGE_LOCK = threading.Lock()


def stage_file(dest_dir: Path, src: Path, file_name: str | None, *,
               move: bool = False) -> Path:
    """Copy (or, with `move=True`, move) `src` into `dest_dir` and return that path.

    Copy, not reference, by default: the caller's own copy of the source is
    what everything downstream reads, never a path that belongs to something
    else (e.g. the Telegram Bot API server's own storage). `move=True` renames
    instead of copying — used for an assembled upload that already sits on the
    same filesystem, so a 2 GiB file is not written twice.

    Never overwrites: two files can share a name, and silently replacing the
    first would lose a file the user believes they sent.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_name(file_name or src.name)

    # HEIC/HEIF is converted the moment it lands, and ingest.to_png_if_heic
    # writes `path.with_suffix(".png")` with no collision check of its own
    # (ingest.py:145). So an incoming `photo.heic` claims TWO names, and both
    # have to be reserved here (finding A, 2026-08-31). Reserving them in one
    # place rather than giving to_png_if_heic its own counter is deliberate:
    # this function already owns the staging directory and the never-overwrite
    # rule, while ingest.py is written to know nothing about Telegram or
    # staging (its module docstring) and returns a name the caller predicts.
    # Two counters would be two owners of "which names are taken", which is
    # how the hole opened in the first place.
    #
    # The hole it closes: send photo.png, answer "character", then send
    # photo.heic. The .heic staged cleanly under its own name, converted, and
    # overwrote the BYTES of the already-assigned photo.png. Job.slots and the
    # manifest were unchanged and no message was sent, so the render used the
    # wrong image inside a paid job. Created by staging itself — before it,
    # conversion ran against Telegram's unique file_N.heic names.
    derived_suffix = ".png" if Path(stem).suffix.lower() in (".heic", ".heif") else None

    def taken(candidate: Path) -> bool:
        if candidate.exists():
            return True
        return derived_suffix is not None and candidate.with_suffix(derived_suffix).exists()

    with _STAGE_LOCK:
        dest = dest_dir / stem
        counter = 1
        while taken(dest):
            # Never overwrite: two files can share a name, and silently replacing
            # the first would lose a file the user believes they sent.
            dest = dest_dir / f"{Path(stem).stem}-{counter}{Path(stem).suffix}"
            counter += 1
        if move:
            os.replace(src, dest)
        else:
            shutil.copyfile(src, dest)
    return dest


def prune_staged(staging_root: Path, max_age_days: int, now: float) -> list[Path]:
    """Delete files under <staging_root>/*/ older than `max_age_days`.

    Age-based and blind to which owner or job a file belongs to — the
    directory carries no other record of that once a job is cleared or
    confirmed. Returns what it removed, so the caller can log it.
    """
    cutoff = now - max_age_days * 86400
    removed: list[Path] = []
    if not staging_root.is_dir():
        return removed
    for owner_dir in staging_root.iterdir():
        if not owner_dir.is_dir():
            continue
        for path in owner_dir.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path)
    return removed


APP_OWNER = "app"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv"})
THUMB_WIDTH = 320


class MaterialError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    return "image" if suffix in IMAGE_SUFFIXES else "video" if suffix in VIDEO_SUFFIXES else "other"


def list_materials(staging_root: Path) -> list[dict]:
    found = []
    for owner_dir in staging_root.iterdir() if staging_root.is_dir() else []:
        if owner_dir.is_symlink() or not owner_dir.is_dir():
            continue
        for path in owner_dir.iterdir():
            if path.is_symlink() or not path.is_file():
                continue
            try:
                st = path.stat()
            except FileNotFoundError:        # pruned or deleted mid-listing
                continue
            found.append({"id": f"{owner_dir.name}/{path.name}", "owner": owner_dir.name,
                          "name": path.name, "bytes": st.st_size, "updated_at": st.st_mtime,
                          "kind": _kind(path)})
    return sorted(found, key=lambda m: m["updated_at"], reverse=True)


def resolve_material(staging_root: Path, owner: str, name: str) -> Path | None:
    owner_dir = safe_child(staging_root, owner)
    if owner_dir is None or (staging_root / owner.strip()).is_symlink() or not owner_dir.is_dir():
        return None
    path = safe_child(owner_dir, name)
    if path is None or (owner_dir / name.strip()).is_symlink() or not path.is_file():
        return None
    return path


def _in_use(batch_dir: Path, path: Path) -> bool:
    """A busy run's manifest names this file. Only busy runs block: a finished
    manifest keeps naming its inputs forever, and would make nothing deletable."""
    needle = str(path)
    for manifest in batch_dir.glob("*.yaml"):
        try:
            if needle in manifest.read_text(encoding="utf-8", errors="replace") \
                    and run_mod.busy(manifest):
                return True
        except OSError:
            continue
    return False


def delete_material(staging_root: Path, batch_dir: Path, owner: str, name: str) -> None:
    path = resolve_material(staging_root, owner, name)
    if path is None:
        raise MaterialError("not_found", "no such material")
    if owner != APP_OWNER:
        # Telegram's /clear and /wipe own the chat directories; deleting a file
        # a Telegram draft points at would break that draft with no message.
        raise MaterialError("forbidden", "only material uploaded from the app can be deleted here")
    if _in_use(batch_dir, path):
        raise MaterialError("in_use", "a running batch uses this file")
    path.unlink(missing_ok=True)


def thumbnail(staging_root: Path, thumbs_root: Path, owner: str, name: str) -> Path:
    src = resolve_material(staging_root, owner, name)
    if src is None:
        raise MaterialError("not_found", "no such material")
    dest = thumbs_root / owner / f"{name}.jpg"
    if dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp.jpg")
    # -ss before -i seeks cheaply; 0.5 s skips the black first frame many phone
    # videos start with. For a still image ffmpeg ignores the seek.
    seek = ["-ss", "0.5"] if _kind(src) == "video" else []
    cmd = ["ffmpeg", "-v", "error", "-y", *seek, "-i", str(src), "-frames:v", "1",
           "-vf", f"scale={THUMB_WIDTH}:-2", str(tmp)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise MaterialError("unprobeable", f"no preview for {name}: {exc}") from exc
    if out.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise MaterialError("unprobeable", f"no preview for {name}")
    os.replace(tmp, dest)
    return dest


def ingest(path: Path) -> tuple[Path, dict]:
    """HEIC→PNG, then measure — the same arrival gate the bot applies (ingest.py)."""
    try:
        path = ingest_mod.to_png_if_heic(path)
        p = ingest_mod.probe(path)
    except RuntimeError as exc:
        raise MaterialError("unprobeable", str(exc)) from exc
    return path, {"kind": p.kind, "width": p.width, "height": p.height,
                  "duration_s": p.duration_s, "bitrate_kbps": p.bitrate_kbps,
                  "size_bytes": p.size_bytes, "warning": ingest_mod.quality_warning(p)}
