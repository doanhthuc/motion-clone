"""Detect and download a TikTok video. Knows nothing about Telegram — the
caller (bot.py's _handle()) turns `on_progress` calls into a message the
user sees. Mirrors ingest.py's own split: this owns "how the file gets onto
disk", bot.py owns everything about what happens to it afterwards.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
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

# yt-dlp's own audio-only extensions, for detecting a video-less download
# (see the check in _download_via_ytdlp() below).
_AUDIO_EXTS = frozenset({"mp3", "m4a", "aac", "opus", "wav", "flac", "ogg"})

# Public, unofficial API — replicates TikTok's signed mobile-app request, so
# it gets a real CDN url in cases where yt-dlp's web-based request gets an
# empty one (confirmed 2026-09-06 by inspecting the raw TikTok response for a
# video that came back audio-only: privateItem=false, downloadSetting=0, but
# playAddr/downloadAddr were both ""  — TikTok withholds the url from
# non-app-signed traffic, it isn't actually a per-video restriction).
# No SLA, no docs, could disappear or change shape without notice — that is
# exactly why this is a fallback tried only after yt-dlp itself has failed,
# never the primary path.
_TIKWM_API = "https://www.tikwm.com/api/"
_USER_AGENT = "Mozilla/5.0 (compatible; motion-bot/1.0)"


def find_url(text: str) -> str | None:
    """The first TikTok URL in `text`, or None."""
    match = URL_RE.search(text)
    return match.group(0) if match else None


def download(url: str, *, on_progress: Callable[[float], None] | None = None,
            timeout: float = 180) -> Path:
    """Download `url` at the best available quality into a fresh temp dir.

    Tries yt-dlp first — it is the well-tested, general-purpose path. If
    that raises for any reason, falls back to the tikwm.com API (see its
    docstring below) before giving up, since the two fail independently:
    yt-dlp can be blocked by TikTok while tikwm still works, or vice versa.
    Raises RuntimeError naming both failures if neither succeeds, so the
    caller has one exception type to catch, matching ingest.probe()'s own
    contract.

    Caller owns cleanup of the returned path's parent directory — this
    function only cleans up after itself when it raises.
    """
    try:
        return _download_via_ytdlp(url, on_progress=on_progress, timeout=timeout)
    except RuntimeError as primary_exc:
        tmp_dir = Path(tempfile.mkdtemp(prefix="tiktok-"))
        try:
            return _download_via_tikwm(url, tmp_dir, timeout=timeout)
        except RuntimeError as fallback_exc:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise RuntimeError(
                f"{primary_exc} — fallback also failed: {fallback_exc}") from fallback_exc


def _download_via_ytdlp(url: str, *, on_progress: Callable[[float], None] | None,
                        timeout: float) -> Path:
    """The primary download path — see download()'s own docstring for the
    fallback this feeds into.
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
    # Some TikTok videos expose only an audio format to yt-dlp — observed
    # 2026-09-06 on a specific clip. Without this check the file reaches
    # ingest.probe() as a fake ".mp4" and fails there with an opaque
    # "StopIteration" (no video stream found) instead of a clear reason, and
    # download()'s tikwm fallback never gets a chance to try instead.
    if files[0].suffix.lstrip(".") in _AUDIO_EXTS:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("yt-dlp got no video track for this url, audio only")
    return files[0]


# tikwm's free tier throttles rather than hard-fails — a burst of retries
# right after a 429-shaped response tends to succeed once the window rolls
# over, so a couple of short-backoff retries turn "hit the limit once" into
# a non-event instead of a failed download. Not aimed at any other failure
# mode: a genuinely missing/private video returns the same "no video url"
# every time, so retrying it would only add latency for nothing.
_TIKWM_LOOKUP_ATTEMPTS = 3
_TIKWM_RETRY_DELAY_S = 1.5


def _tikwm_lookup(url: str, *, timeout: float) -> dict:
    """The JSON lookup call, with retries for tikwm's own rate limiting.
    Split out from _download_via_tikwm() so the (slow, sleep-based) retry
    loop is easy to disable in tests without touching the video-download half.
    """
    api_url = _TIKWM_API + "?" + urllib.parse.urlencode({"url": url})
    req = urllib.request.Request(api_url, headers={"User-Agent": _USER_AGENT})
    last_exc: Exception | None = None
    for attempt in range(_TIKWM_LOOKUP_ATTEMPTS):
        if attempt:
            time.sleep(_TIKWM_RETRY_DELAY_S * attempt)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_exc = exc
            continue
        if payload.get("code") == 0:
            return payload
        last_exc = RuntimeError(payload.get("msg", "unknown error"))
    raise RuntimeError(f"tikwm lookup failed after {_TIKWM_LOOKUP_ATTEMPTS} "
                       f"attempts: {last_exc}") from last_exc


def _download_via_tikwm(url: str, tmp_dir: Path, *, timeout: float) -> Path:
    """Fallback download via tikwm.com's public API — see the module-level
    comment on _TIKWM_API for why this exists and how it differs from the
    yt-dlp path. No progress reporting: unlike yt-dlp's own subprocess, a
    single HTTP GET has nothing to report progress on.
    """
    payload = _tikwm_lookup(url, timeout=timeout)
    play_url = (payload.get("data") or {}).get("play")
    if not play_url:
        raise RuntimeError("tikwm returned no video url")

    dest = tmp_dir / "video.mp4"
    try:
        req = urllib.request.Request(play_url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
    except urllib.error.URLError as exc:
        raise RuntimeError(f"tikwm video download failed: {exc}") from exc
    if dest.stat().st_size == 0:
        raise RuntimeError("tikwm produced an empty file")
    return dest
