"""Small JPEG previews of staged material, so a question can be answered by eye.

The bot's whole ingest rule is that material arrives as a Document, because
Telegram's Photo path destroys it (§4.1: a 1536x2720 PNG came back a 1445x2560
JPEG with 98% of its bytes gone). The cost of that rule is that nothing the user
sends is ever *shown* back to them — a Document renders as a filename and a
size. So the bot was asking "Which slot is this?" about an image the user could
not see, and offering to spend $0.99/hour on a list of resolutions.

These previews close that gap without touching the rule. Every one is a
downscaled, re-encoded throwaway: the job still runs on the untouched staged
bytes, and nothing here is ever an input to anything.

Two measurements this design rests on, both 2026-09-01:

- A `file_id` belonging to a Document CANNOT be re-sent as a Photo
  ("can't use file of type Document as Photo"), so the cheap trick of echoing
  Telegram's own copy back is not available. A preview costs one small upload.
- Generating one costs 0.03-0.05s and 19-35 KB via ffmpeg, and an album of two
  uploads in 0.50s. That is small enough to sit in the synchronous poll loop
  without the bot going quiet.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

# 512px wide is enough to recognise a person, a garment and a pose on a phone,
# and small enough that the upload is not the slow part. Not configurable: a
# knob here would only ever be turned to make previews slower.
PREVIEW_WIDTH = 512

# For a video, the frame at 1s rather than at 0. A driver's first frame is
# routinely black, a slate, or the subject not yet in position — none of which
# answer "is this the right clip". Cheap to seek to with -ss before -i.
VIDEO_SEEK_SEC = 1


def thumbnail(src: Path, *, is_video: bool, into: Path | None = None) -> Path | None:
    """One small JPEG of `src`, or None if it could not be made.

    None rather than raising, and every caller treats it as "no picture this
    time". A preview is a courtesy: a broken ffmpeg, an exotic codec or a
    zero-byte file must never be able to stop a job being assembled. The
    measurements the bot actually decides on come from `ingest.probe`, which
    does raise, and which has already run by the time anything here is called.
    """
    into = into or Path(tempfile.mkdtemp(prefix="tgpreview-"))
    into.mkdir(parents=True, exist_ok=True)
    dst = into / f"{src.stem}.jpg"
    argv = ["ffmpeg", "-v", "error", "-y"]
    if is_video:
        # Before -i: this makes ffmpeg seek rather than decode up to the mark.
        argv += ["-ss", str(VIDEO_SEEK_SEC)]
    argv += ["-i", str(src), "-frames:v", "1",
             # -2 keeps the height even, which some encoders require; scale
             # never enlarges a source narrower than PREVIEW_WIDTH because the
             # upload would then be bigger than the thing it stands for.
             "-vf", f"scale='min({PREVIEW_WIDTH},iw)':-2", "-q:v", "4", str(dst)]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        return None
    return dst


def for_video_fallback(src: Path, into: Path | None = None) -> Path | None:
    """A video whose 1s mark does not decode — try frame 0 before giving up.

    A 13-second driver always has a frame at 1s, but a 0.5s clip does not, and
    an ffmpeg that seeks past the end writes nothing and exits 0 on some builds
    (hence the size check in `thumbnail`). One retry is worth it; two would be
    chasing a courtesy feature into a corner.
    """
    into = into or Path(tempfile.mkdtemp(prefix="tgpreview-"))
    into.mkdir(parents=True, exist_ok=True)
    dst = into / f"{src.stem}-0.jpg"
    argv = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1",
            "-vf", f"scale='min({PREVIEW_WIDTH},iw)':-2", "-q:v", "4", str(dst)]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        return None
    return dst


def make(src: Path, *, is_video: bool, into: Path | None = None) -> Path | None:
    """`thumbnail`, with the frame-0 retry for video. The one entry point."""
    shot = thumbnail(src, is_video=is_video, into=into)
    if shot is None and is_video:
        return for_video_fallback(src, into)
    return shot
