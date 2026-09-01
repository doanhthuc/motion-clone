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

# An allowlist, so anything unrecognised is treated as video and gets the
# duration check below. The inverse — treating unrecognised codecs as images —
# would let a video skip that check silently.
IMAGE_CODECS = frozenset({"mjpeg", "png", "bmp", "webp"})


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
    except subprocess.TimeoutExpired as exc:
        # timeout=60 above raises TimeoutExpired, which is NOT a RuntimeError —
        # so before 2026-08-31 it escaped every caller's `except RuntimeError`,
        # reached bot.main()'s blanket handler and the user got no reply at all.
        # Same contract as every other failure here: raise something that names
        # the file and that the caller already catches.
        raise RuntimeError(
            f"ffprobe timed out after 60s on {path.name} — the upload may be "
            f"truncated or the file may not be media; send it again.") from exc
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
        #
        # An earlier version also treated `duration == 0.0` as evidence of an
        # image. Measured 2026-08-31 with a real 2-second raw h264 stream: some
        # containers carry no duration at all, so that clause classified a
        # genuine video as an image — and describe() then omits duration and
        # bitrate, hiding the anomaly from the confirmation screen instead of
        # showing it. Codec alone decides the kind now.
        kind = "image" if v.get("codec_name") in IMAGE_CODECS else "video"
        if kind == "video" and duration <= 0.0:
            # Loud, not a guess. suggest_preset() would silently return drv-5s
            # for an unmeasured driver and truncate a 30-second one, after the
            # pod was already rented. A truncated upload is the likely cause.
            raise RuntimeError(
                f"{path.name}: ffprobe reported no duration for a "
                f"{v.get('codec_name')} video stream. A preset cannot be chosen "
                f"without it — the file may be truncated; send it again.")
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
    on_darwin = platform.system() == "Darwin"
    tool = "sips" if on_darwin else "ffmpeg"
    cmd = (["sips", "-s", "format", "png", str(path), "--out", str(dest)] if on_darwin
           else ["ffmpeg", "-y", "-i", str(path), str(dest)])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        # Same shape as probe()'s missing-ffprobe message: name what to
        # install rather than leak a bare FileNotFoundError to the caller.
        raise RuntimeError(f"{tool} is not installed — cannot convert {path.name} "
                          f"from HEIC") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{tool} timed out after 120s converting {path.name} "
                           f"from HEIC") from exc
    if out.returncode != 0:
        # Was `check=True`, which raises CalledProcessError — not a
        # RuntimeError, so it escaped bot.handle()'s ingest guard entirely and
        # the user got silence (finding I1, 2026-08-31). Every other failure in
        # this module raises RuntimeError naming the file; this one now does
        # too, and carries the converter's own stderr so the reason is visible.
        raise RuntimeError(f"{tool} could not convert {path.name} from HEIC: "
                           f"{(out.stderr or '').strip()[:200]}")
    if not dest.exists() or dest.stat().st_size == 0:
        # A converter that exits 0 having written nothing must not hand back a
        # path to a missing/zero-byte file straight into a paid render.
        raise RuntimeError(f"{tool} reported success but wrote no file for {path.name}")
    return dest

# Below this, a video is almost certainly a re-compressed copy rather than
# original material. Units are kbps per megapixel, which makes a 576x1024
# driver comparable with a 1440x1920 one.
#
# Chosen from a survey of all 64 videos on this user's machine (material,
# batch/tg-staging and out/), 2026-08-31:
#
#   417   IMG_6783.MP4        the known-bad case: sent as a File, so Telegram
#                             preserved it exactly, but it was already a
#                             re-compressed copy before it was sent
#   1397  s1.mp4              the LOWEST legitimate driver in the set
#   1900  s2.mp4
#   3227  median of the 64
#   8421  nhanvat3__dandong8  highest
#
# 1000 sits between the two that matter, with 1.4x headroom below the lowest
# real driver and 2.4x above the known-bad one. An earlier attempt at 1500 was
# rejected by this same survey: it flags s1.mp4, which is material the user
# actually uses, and a warning that cries wolf on real files gets ignored on
# the one that matters.
#
# This is a HEURISTIC from one person's 64 files, not a universal rule — hence
# a warning that never blocks. The user kept a 417 driver deliberately on
# 2026-08-31 after being shown the number, which is exactly the outcome the
# design should allow.
LOW_BITRATE_KBPS_PER_MPX = 1000


# The survey figures above, as they are shown to the user. Named rather than
# inlined so the message can never drift from the threshold it explains.
REAL_DRIVER_LOW_PER_MPX = 1400      # s1.mp4, the lowest legitimate driver
REAL_DRIVER_HIGH_PER_MPX = 8400     # nhanvat3__dandong8, the highest


def _per_megapixel(p: Probe) -> float | None:
    """kbps per megapixel, or None when the check does not apply.

    The whole judgment lives here so the two renderings below cannot disagree
    about whether a file is bad, or by how much.
    """
    if p.kind != "video" or p.width <= 0 or p.height <= 0:
        return None
    mpx = p.width * p.height / 1_000_000
    if not mpx:
        return None
    return p.bitrate_kbps / mpx


def quality_warning(p: Probe) -> str:
    """A note when a video's bitrate is too low to be original material.

    Empty string when nothing is wrong, so callers can concatenate it. Plain
    text: this is the form for logs and for any caller not sending HTML.
    `quality_warning_html` is what the bot shows.

    Why this exists: spec section 4.3 is "measure on arrival, do not trust the
    channel", and describe() did the measuring — but nothing judged the result.
    On 2026-08-31 a driver arrived at 865 kbps for 1080x1920, 15x below the
    file it was standing in for, and the bot printed the number and accepted it
    without comment. The File rule guarantees Telegram did not damage the
    bytes; it cannot guarantee the bytes were good before they were sent.
    """
    per_mpx = _per_megapixel(p)
    if per_mpx is None or per_mpx >= LOW_BITRATE_KBPS_PER_MPX:
        return ""
    return (f"low bitrate: {per_mpx:.0f} per megapixel, where real drivers here "
            f"measure {REAL_DRIVER_LOW_PER_MPX}-{REAL_DRIVER_HIGH_PER_MPX} "
            f"({p.bitrate_kbps} kbps at {p.width}x{p.height}). Probably an "
            f"already-compressed copy. It will still run.")


def quality_warning_html(p: Probe) -> str:
    """The same judgment, laid out so the two numbers can be compared.

    Returns HTML — the caller must NOT escape it — or an empty string.

    The layout was chosen by the user from four candidates rendered on their
    own phone (2026-09-01). Building it exposed a real defect in the sentence
    it replaces: that one read "865 kbps (417 per megapixel). Real drivers here
    measure 1400-8400", placing a RAW kbps figure beside a PER-MEGAPIXEL range,
    so the comparison a reader naturally makes — 865 against 1400 — is between
    two different units and means nothing. Every figure in the survey behind
    LOW_BITRATE_KBPS_PER_MPX is per megapixel. Aligning them into columns would
    have made that worse rather than better, because alignment is itself a
    claim that two numbers are comparable. So the unit moved into a heading and
    only like-for-like figures sit in the table; the raw kbps stays, below, as
    context rather than as a comparison.
    """
    per_mpx = _per_megapixel(p)
    if per_mpx is None or per_mpx >= LOW_BITRATE_KBPS_PER_MPX:
        return ""
    return (f"low bitrate\n"
            f"<pre>bitrate per megapixel\n"
            f"  this file  {per_mpx:>9.0f}\n"
            f"  normal     {REAL_DRIVER_LOW_PER_MPX}-{REAL_DRIVER_HIGH_PER_MPX}</pre>"
            f"<i>{p.bitrate_kbps} kbps at {p.width}x{p.height} · probably an "
            f"already-compressed copy · it will still run</i>")
