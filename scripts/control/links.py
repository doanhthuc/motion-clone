"""Material from a pasted link — today only TikTok, the one source the bot already trusts.

Telegram-free: the bot keeps its own chat-side handler (tgbot/bot.py), this is
the phone API's counterpart. It reuses tgbot/tiktok.py unchanged, so yt-dlp,
the tikwm fallback and their failure modes are the ones the bot has lived with
since 2026-09-04 (scripts/vps/README.md §yt-dlp).
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from control import materials
from tgbot import tiktok

# Per download path, not in total: tiktok.download() tries yt-dlp and then
# tikwm, each with this budget. The phone reaches us through Cloudflare, which
# cuts a proxied response at 100 s (its 524), so a single path has to finish
# well inside that. A TikTok clip is a few MB; the bot's downloads finish in
# seconds, so 40 s leaves room without letting a stuck yt-dlp hold a thread.
DOWNLOAD_TIMEOUT = 40

# One download at a time. yt-dlp is the heaviest thing this 1 GB droplet runs
# besides the bot (scripts/vps/README.md: "any spike (yt-dlp …)"), and the
# phone has no reason to fetch two links in parallel.
_SLOT = threading.Lock()


def import_link(url: object, staging_root: Path) -> dict:
    """Download the video at `url` and stage it as an app material.

    Returns the same shape as `uploads.complete`, `{"material", "probe"}`, so
    the phone handles both with one decoder. Not idempotent — each call is a
    fresh download under a fresh name, like pasting the link twice in the bot.
    """
    found = tiktok.find_url(url) if isinstance(url, str) else None
    if found is None:
        raise materials.MaterialError("bad_request", "not a TikTok link")
    if not _SLOT.acquire(blocking=False):
        raise materials.MaterialError("busy", "another link is already downloading")
    try:
        try:
            tmp = tiktok.download(found, timeout=DOWNLOAD_TIMEOUT)
        except (RuntimeError, OSError) as exc:
            raise materials.MaterialError("download_failed", f"couldn't download that TikTok video: {exc}") from exc
        try:
            # Copy, not move: yt-dlp's temp dir is usually on /tmp, and
            # os.replace cannot cross filesystems.
            staged = materials.stage_file(staging_root / materials.APP_OWNER, tmp,
                                          f"tiktok-{int(time.time())}.mp4")
        finally:
            # download() only cleans up after itself when it raises; on
            # success the directory is ours.
            shutil.rmtree(tmp.parent, ignore_errors=True)
        try:
            final, probe = materials.ingest(staged)
        except materials.MaterialError:
            # Same rule as uploads._ingest: a file that cannot be probed must
            # not sit in the list looking usable.
            staged.unlink(missing_ok=True)
            raise
        return {"material": materials.material_item(materials.APP_OWNER, final), "probe": probe}
    finally:
        _SLOT.release()
