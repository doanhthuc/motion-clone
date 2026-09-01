"""Small previews of staged material, so a question can be answered by eye.

The bot's ingest rule is that material arrives as a Document, because
Telegram's Photo path destroys it (§4.1: a 1536x2720 PNG came back a 1445x2560
JPEG with 98% of its bytes gone). The cost of that rule is that nothing the
user sends is ever *shown* back: a Document renders as a filename and a size.
So the bot was asking "Which slot is this?" about an image nobody could see,
and offering to spend $0.99/hour on a list of resolutions.

Every preview here is a downscaled, re-encoded throwaway. The job still runs on
the untouched staged bytes and nothing built here is ever an input to anything.

Three measurements shaped this, all 2026-09-01:

1. A `file_id` belonging to a Document CANNOT be re-sent as a Photo
   ("can't use file of type Document as Photo"), so echoing Telegram's own copy
   back is not available. A preview costs one small upload.
2. **Shrinking the image does not shrink the preview.** Telegram scales a photo
   to the chat bubble's width whatever its pixel size, so 512px, 320px and
   220px versions all rendered the same height — only blurrier. Reported by the
   user looking at all four: "cả 4 đều không có giúp thu nhỏ preview mà còn làm
   preview mở hơn". The lever is ASPECT RATIO: a 1536x2720 portrait is 1.8x
   taller than it is wide and therefore always tall in the chat; a 2:1 landscape
   is half its own width. That is why both shapes below are wide ones.
3. Generating a preview costs 0.03-0.05s and 19-35KB via ffmpeg — small enough
   to sit in the synchronous poll loop without the bot going quiet.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

# 2:1. Chosen by the user from four shapes shown on their own phone, after the
# black letterbox of a plain pad was rejected as wasteful ("nền đen thừa nhiều
# quá") and the two cropped alternatives were passed over: a crop cannot know
# whether the hem, the shoes or the face is the part that distinguishes this
# outfit from the next one, and the whole purpose is telling them apart.
SLOT_W, SLOT_H = 640, 320

# Height of each panel in the composite strip. Three of these side by side make
# a ~2:1 image, which is the same reason as above.
STRIP_H = 320

# For a video, the frame at 1s rather than at 0. A driver's first frame is
# routinely black, a slate, or the subject not yet in position — none of which
# answer "is this the right clip". Cheap: -ss before -i seeks rather than
# decodes up to the mark.
VIDEO_SEEK_SEC = 1

# Fill the wide canvas with a blurred copy of the picture itself instead of
# flat colour. Same reason the shape is wide at all — the space either carries
# something or reads as waste.
_BLUR_FILL = (
    "split=2[a][b];"
    f"[a]scale={SLOT_W}:{SLOT_H}:force_original_aspect_ratio=increase,"
    f"crop={SLOT_W}:{SLOT_H},gblur=sigma=28[bg];"
    f"[b]scale={SLOT_W}:{SLOT_H}:force_original_aspect_ratio=decrease[fg];"
    "[bg][fg]overlay=(W-w)/2:(H-h)/2")


def _tmpdir(into: Path | None) -> Path:
    into = into or Path(tempfile.mkdtemp(prefix="tgpreview-"))
    into.mkdir(parents=True, exist_ok=True)
    return into


def _ffmpeg(argv: list[str], dst: Path) -> Path | None:
    """Run ffmpeg and return `dst` only if it really wrote something.

    None rather than an exception, and every caller treats it as "no picture
    this time". A preview is a courtesy: a missing ffmpeg, an exotic codec or a
    truncated upload must never stop a job being assembled. The measurements
    the bot DECIDES on come from `ingest.probe`, which does raise, and which
    has already run by the time anything here is called.

    The size check is not belt-and-braces: an ffmpeg told to seek past the end
    of a short clip exits 0 on some builds having written nothing at all.
    """
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        return None
    return dst


def slot_preview(src: Path, *, is_video: bool, into: Path | None = None) -> Path | None:
    """One wide, blur-filled picture of `src` — the shape used to ask about a slot.

    Retries a video at frame 0 if the 1s seek found nothing: a 13-second driver
    always has a frame there, a 0.4s clip does not, and giving up would drop the
    picture from the one question that most needs it.
    """
    into = _tmpdir(into)
    dst = into / f"{src.stem}-slot.jpg"
    for seek in ([str(VIDEO_SEEK_SEC), "0"] if is_video else [None]):
        argv = ["ffmpeg", "-v", "error", "-y"]
        if seek is not None:
            argv += ["-ss", seek]
        argv += ["-i", str(src), "-frames:v", "1",
                 "-filter_complex", _BLUR_FILL, "-q:v", "4", str(dst)]
        shot = _ffmpeg(argv, dst)
        if shot is not None:
            return shot
    return None


def strip(sources: list[tuple[Path, bool]], *, into: Path | None = None) -> Path | None:
    """All of a job's material as ONE wide image, left to right.

    Chosen over an album of separate photos for the confirmation screen: an
    album of portraits is a tall grid, and the point of the shape is height.
    The trade the user accepted is that each picture gets a third of the width
    and so appears smaller than it would in an album.

    A caller MUST caption this with the roles in the same order — nothing is
    drawn onto the image, because burning labels in needs a font file whose
    path differs per platform, and a preview that fails on the VPS because
    DejaVu moved is worse than one that relies on its caption.

    None if any panel could not be built: a strip missing a slot would be
    read as the job missing that slot, which is a lie about what will run.
    """
    if not sources:
        return None
    into = _tmpdir(into)
    panels: list[Path] = []
    for index, (src, is_video) in enumerate(sources):
        dst = into / f"strip-{index}-{src.stem}.jpg"
        made = None
        for seek in ([str(VIDEO_SEEK_SEC), "0"] if is_video else [None]):
            argv = ["ffmpeg", "-v", "error", "-y"]
            if seek is not None:
                argv += ["-ss", seek]
            argv += ["-i", str(src), "-frames:v", "1",
                     "-vf", f"scale=-2:{STRIP_H}", "-q:v", "3", str(dst)]
            made = _ffmpeg(argv, dst)
            if made is not None:
                break
        if made is None:
            return None
        panels.append(made)

    if len(panels) == 1:
        # hstack with one input is legal but pointless, and it would leave a
        # lone portrait — the tall shape this exists to avoid. Give the single
        # slot the same wide treatment the slot question uses.
        return slot_preview(sources[0][0], is_video=sources[0][1], into=into)

    dst = into / "strip.jpg"
    argv = ["ffmpeg", "-v", "error", "-y"]
    for panel in panels:
        argv += ["-i", str(panel)]
    inputs = "".join(f"[{i}:v]" for i in range(len(panels)))
    argv += ["-filter_complex", f"{inputs}hstack=inputs={len(panels)}",
             "-q:v", "4", str(dst)]
    return _ffmpeg(argv, dst)
