"""Detect and download a TikTok video. Knows nothing about Telegram — the
caller (bot.py's _handle()) turns `on_progress` calls into a message the
user sees. Mirrors ingest.py's own split: this owns "how the file gets onto
disk", bot.py owns everything about what happens to it afterwards.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

# www./m. cover the ordinary site, vm./vt. cover the two share-link shapes
# TikTok's own app generates. No other domain is treated as a download
# request — a link the user does not expect this bot to act on must not
# silently start a subprocess.
URL_RE = re.compile(r"https?://(?:www\.|m\.|vm\.|vt\.)?tiktok\.com/\S+", re.IGNORECASE)

# yt-dlp's own progress line, one per update when run with --newline (without
# it, progress overwrites in place with \r and a line-based reader never sees
# more than the final one).
_PROGRESS_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")

# How many trailing output lines to keep for an error message — enough for
# yt-dlp's own multi-line ERROR block, small enough not to flood the chat.
_TAIL_LINES = 40


def find_url(text: str) -> str | None:
    """The first TikTok URL in `text`, or None."""
    match = URL_RE.search(text)
    return match.group(0) if match else None


def download(url: str, *, on_progress: Callable[[float], None] | None = None,
            timeout: float = 180) -> Path:
    """Download `url` at the best available quality into a fresh temp dir.

    Raises RuntimeError for every failure mode (missing binary, non-zero
    exit, timeout, or a zero-file result) so the caller has one exception
    type to catch, matching ingest.probe()'s own contract.

    Caller owns cleanup of the returned path's parent directory — this
    function only cleans up after itself when it raises.
    """
    if shutil.which("yt-dlp") is None:
        raise RuntimeError(
            "yt-dlp is not installed on this box — see scripts/vps/README.md")

    tmp_dir = Path(tempfile.mkdtemp(prefix="tiktok-"))
    out_template = tmp_dir / "video.%(ext)s"
    cmd = ["yt-dlp", "--newline", "-f", "bv*+ba/b",
          "--merge-output-format", "mp4", "--no-playlist",
          "-o", str(out_template), url]

    deadline = time.monotonic() + timeout
    tail: list[str] = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            tail.append(line)
            tail[:] = tail[-_TAIL_LINES:]
            match = _PROGRESS_RE.search(line)
            if match and on_progress:
                on_progress(float(match.group(1)))
            if time.monotonic() > deadline:
                proc.kill()
                raise RuntimeError(f"download timed out after {timeout:.0f}s")
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()

    if proc.returncode != 0:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("yt-dlp failed: " + "".join(tail).strip()[-500:])

    files = sorted(tmp_dir.glob("video.*"))
    if not files:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("yt-dlp reported success but produced no file")
    return files[0]
