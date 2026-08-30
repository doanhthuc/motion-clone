"""The ingest quality gate: measure every file as it arrives, before any GPU
is rented.

Neither Telegram nor a browser upload can be trusted by faith — iOS Safari's
<input type=file> transcodes unpredictably, and a mis-tapped "send as Photo"
compresses silently. The answer is not to pick a channel we believe in but to
verify on arrival. This is the same principle as `make batch-validate` and
`make gpu-preflight`: free gates that fire before spending.

Deliberately imports nothing from Telegram: this module takes a Path and
returns facts about it, so it is testable without an account and reusable if
the transport ever changes.
"""
from __future__ import annotations

import json
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Probe:
    kind: str            # "image" or "video"
    width: int
    height: int
    duration_s: float
    bitrate_kbps: int
    size_bytes: int


# The ceiling is a duration LIMIT, not a frame count: the worker ffprobes the
# driver itself and overwrites params["frames"] regardless of what a manifest
# says (see scripts/batch-params.json's "why" note on drv-Ns, measured
# 18/08/2026). suggest_preset mirrors that same ceiling so the bot's proposal
# matches what the worker will actually pick.
PRESET_CEILINGS = [(5, "drv-5s"), (10, "drv-10s"), (15, "drv-15s"),
                   (20, "drv-20s"), (30, "drv-30s")]


def probe(path: Path) -> Probe:
    """Measure a file with ffprobe. Raises rather than guessing.

    An unprobeable file must never be silently accepted: the next thing that
    happens to it is a $0.99/hour render, and a wrong duration picks a wrong
    preset. Failing loudly here costs a message; failing quietly costs a batch.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60)
    except FileNotFoundError as exc:
        raise RuntimeError("ffprobe is not installed — see scripts/vps/README.md") from exc
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {path.name}: {out.stderr.strip()[:200]}")
    try:
        data = json.loads(out.stdout)
        streams = data["streams"]
        fmt = data.get("format", {})
        v = next(s for s in streams if s.get("codec_type") == "video")
        duration = float(fmt.get("duration") or v.get("duration") or 0.0)
        # A still image is a one-frame "video" stream to ffprobe. Distinguish by
        # the codec, not by the file extension: a .mov holding a live photo and
        # a .jpg both lie about themselves by name.
        kind = "image" if v.get("codec_name") in {"mjpeg", "png", "bmp", "webp"} \
            or duration == 0.0 else "video"
        return Probe(kind=kind,
                     width=int(v.get("width") or 0), height=int(v.get("height") or 0),
                     duration_s=duration,
                     bitrate_kbps=int(int(fmt.get("bit_rate") or 0) / 1000),
                     size_bytes=int(fmt.get("size") or path.stat().st_size))
    except (ValueError, KeyError, StopIteration) as exc:
        raise RuntimeError(f"ffprobe output for {path.name} was not usable: {exc!r}") from exc


def describe(p: Probe) -> str:
    """One-line summary: the numbers that reveal recompression.

    A 4K driver arriving as 720p at 1.1 Mbps must be visible in this one line,
    before any GPU is rented — width/height catch a silent downscale, bitrate
    and file size catch a silent re-encode at the same resolution.
    """
    size_mb = p.size_bytes / 1_000_000
    if p.kind == "video":
        return (f"video {p.width}x{p.height}, {p.duration_s:.1f}s, "
                f"{p.bitrate_kbps} kbps, {size_mb:.1f} MB")
    return f"image {p.width}x{p.height}, {size_mb:.1f} MB"


def suggest_preset(duration_s: float) -> str:
    """Map a driver's real duration to the smallest preset whose ceiling fits.

    docs/batch-runner.md previously required the user to run ffprobe by
    hand and type the preset; the bot measures, so it proposes. Anything
    longer than 30s falls through to drv-30s — there is no larger preset, and
    the job still runs: motion lowers fps and keeps the length, while
    character-swap trims the tail (see the 34.1s real-driver case in tests).
    """
    for ceiling, preset in PRESET_CEILINGS:
        if duration_s <= ceiling:
            return preset
    return "drv-30s"


def to_png_if_heic(path: Path) -> Path:
    """Convert HEIC to PNG in place next to the original; pass through anything else.

    iOS defaults to HEIC, and neither ComfyUI's image loaders nor Gemini's
    try-on provider are guaranteed to accept it. Returns the original path
    unchanged for non-HEIC input, so the caller can tell whether a conversion
    happened by comparing the returned path to the input.
    """
    if path.suffix.lower() not in (".heic", ".heif"):
        return path
    dest = path.with_suffix(".png")
    if platform.system() == "Darwin":
        subprocess.run(["sips", "-s", "format", "png", str(path), "--out", str(dest)],
                       capture_output=True, text=True, check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-i", str(path), str(dest)],
                       capture_output=True, text=True, check=True)
    return dest
