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


# The contact sheet. Every number below was chosen by the user from variants
# rendered on their own phone (2026-09-01) — none of it is taste asserted here.
TILE_W, TILE_H = 190, 250     # one slot; uniform, so columns line up

# Tiles are CROPPED to fill, not letterboxed onto a blurred copy of themselves.
# Both were rendered side by side on the user's phone and this one was chosen:
# twelve blur-filled cells read as twelve smudges, while a cropped grid is
# crisp to every edge. The trade is real and was made knowingly — a crop can
# remove the hem or the shoes, which is sometimes the very thing that tells two
# outfits apart. It is accepted here because the sheet is a glance across jobs;
# the slot question, where the whole point is identifying ONE file, keeps the
# full frame and its blurred fill for exactly that reason.
#
# Biased upward: a standing figure keeps its head rather than its shoes.
TILE_CROP_BIAS = 0.30

# One per job row. Chosen to match the coloured squares the panel puts beside
# each entry — the caption cannot number the rows (no drawtext, see `sheet`),
# so colour is the only legend that works in both places at once.
ROW_ACCENTS = ["0x5b9dd9", "0xd9a05b", "0x77c47f",
               "0x9b8ad9", "0xd97a7a", "0xd9cf5b"]
ACCENT_BAR_W = 6
TILE_RADIUS = 14
CARD_RADIUS = 20
COL_GAP, ROW_GAP, CARD_PAD = 8, 14, 12
SHEET_BG = "0x0f1216"
CARD_BG = "0x1b2027"
SHADOW_BG = "0x05070a"
EMPTY_BG = "0x161b21"
TILE_RIM = "0x3a444f@0.85"
EMPTY_RIM = "0x2a323b@0.9"

# Tiles keep ONE size no matter how many jobs are queued — the user's choice
# when shown a six-job sheet both ways. Squashing them to hold the aspect keeps
# the image short but shows less of each picture, and a batch that has to be
# scrolled was preferred to a batch that cannot be seen. Measured: three jobs
# come to 808x872 (0.93), six to 808x1730 (0.47).


def _rounded(radius: int) -> str:
    """An alpha mask that rounds the corners of whatever precedes it.

    geq evaluates per pixel, which sounds expensive and is not: measured
    2026-09-01 at 0.038s per tile against 0.043s without it — the difference is
    inside the noise, and a six-job sheet builds in under a second. That was
    worth measuring rather than assuming, because this runs inside the bot's
    synchronous poll loop.
    """
    r = radius
    return ("format=rgba,geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='if(lt(min(X\\,W-1-X)\\,{r})*lt(min(Y\\,H-1-Y)\\,{r}),"
            f"if(lte(pow({r}-min(X\\,W-1-X)\\,2)+pow({r}-min(Y\\,H-1-Y)\\,2)\\,"
            f"{r * r})\\,255\\,0),255)'")


def _tile(src: Path | None, dst: Path, *, is_video: bool) -> Path | None:
    """One cell. `None` src means the job does not use this slot.

    An empty cell rather than a shorter row: the columns are roles, so a job
    without a background has to leave that column blank or every cell to its
    right shifts and the sheet stops being readable down a column. The blank
    also says "this slot is empty", which a missing cell does not.
    """
    if src is None:
        vf = (f"drawbox=x=0:y=0:w={TILE_W}:h={TILE_H}:color={EMPTY_RIM}:t=1,"
              + _rounded(TILE_RADIUS))
        return _ffmpeg(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=c={EMPTY_BG}:s={TILE_W}x{TILE_H}",
                        "-frames:v", "1", "-vf", vf, str(dst)], dst)
    fill = (f"scale={TILE_W}:{TILE_H}:force_original_aspect_ratio=increase,"
            f"crop={TILE_W}:{TILE_H}:(iw-{TILE_W})/2:(ih-{TILE_H})*{TILE_CROP_BIAS},"
            f"drawbox=x=0:y=0:w={TILE_W}:h={TILE_H}:color={TILE_RIM}:t=1,"
            + _rounded(TILE_RADIUS))
    for seek in ([str(VIDEO_SEEK_SEC), "0"] if is_video else [None]):
        argv = ["ffmpeg", "-v", "error", "-y"]
        if seek is not None:
            argv += ["-ss", seek]
        argv += ["-i", str(src), "-frames:v", "1", "-filter_complex", fill, str(dst)]
        made = _ffmpeg(argv, dst)
        if made is not None:
            return made
    return None


def _panel(dst: Path, width: int, height: int, colour: str, radius: int) -> Path | None:
    return _ffmpeg(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={colour}:s={width}x{height}", "-frames:v", "1",
                    "-vf", _rounded(radius), str(dst)], dst)


def sheet(rows: list[list[tuple[Path, bool] | None]], *,
          into: Path | None = None) -> Path | None:
    """One image of a whole batch: a row per job, a column per slot.

    `rows` is already column-aligned by the caller — this module knows nothing
    about roles or Telegram (see the module docstring), so a `None` entry is
    simply an empty cell and the caller decides what that means.

    Every job sits on its own rounded card, and the gap between rows is wider
    than the gap between columns. That is not decoration: with even gaps the
    eye reads twelve separate pictures, and with these it reads three jobs of
    four slots, which is what the sheet is for. Each tile gets a thin rim and a
    small shadow so it lifts off the card instead of merging into it.

    Nothing is drawn ONTO the image. `drawtext` is not compiled into the
    ffmpeg this runs against (checked 2026-09-01 — the earlier note about font
    paths differing per platform was a weaker reason than the real one), so
    the row order has to be carried by the caption instead.

    None if any tile fails: a sheet missing a cell would be read as a job
    missing that slot, which is a lie about what will run.
    """
    if not rows:
        return None
    into = _tmpdir(into)
    columns = max(len(row) for row in rows)
    tiles: list[Path] = []
    for r, row in enumerate(rows):
        for c in range(columns):
            entry = row[c] if c < len(row) else None
            src, is_video = entry if entry is not None else (None, False)
            made = _tile(src, into / f"t{r}-{c}.png", is_video=is_video)
            if made is None:
                return None
            tiles.append(made)

    lead = ACCENT_BAR_W + 10
    width = lead + columns * TILE_W + (columns + 1) * COL_GAP + 2 * CARD_PAD
    card_h = TILE_H + 2 * CARD_PAD
    height = len(rows) * card_h + (len(rows) + 1) * ROW_GAP
    card = _panel(into / "card.png", width, card_h, CARD_BG, CARD_RADIUS)
    shadow = _panel(into / "shadow.png", TILE_W, TILE_H, SHADOW_BG, TILE_RADIUS)
    if card is None or shadow is None:
        return None

    argv = ["ffmpeg", "-v", "error", "-y", "-i", str(card), "-i", str(shadow)]
    for tile_path in tiles:
        argv += ["-i", str(tile_path)]
    graph = [f"color=c={SHEET_BG}:s={width}x{height}[c0]"]
    prev = "c0"
    for r in range(len(rows)):
        y = ROW_GAP + r * (card_h + ROW_GAP)
        graph.append(f"[{prev}][0:v]overlay=0:{y}[k{r}]")
        prev = f"k{r}"
    for index in range(len(tiles)):
        r, c = divmod(index, columns)
        x = lead + COL_GAP + CARD_PAD + c * (TILE_W + COL_GAP)
        y = ROW_GAP + CARD_PAD + r * (card_h + ROW_GAP)
        graph.append(f"[{prev}][1:v]overlay={x + 2}:{y + 3}[s{index}]")
        graph.append(f"[s{index}][{2 + index}:v]overlay={x}:{y}[o{index}]")
        prev = f"o{index}"
    # One coloured bar per job, matching the square the panel prints beside
    # that job. Nothing can be written on the image, so this is the only
    # legend that reads in the picture and in the text at the same time.
    bars = [f"drawbox=x={CARD_PAD}:y={ROW_GAP + r * (card_h + ROW_GAP) + CARD_PAD}:"
            f"w={ACCENT_BAR_W}:h={TILE_H}:"
            f"color={ROW_ACCENTS[r % len(ROW_ACCENTS)]}@0.95:t=fill"
            for r in range(len(rows))]
    graph.append(f"[{prev}]" + ",".join(bars) + "[sheet]")
    dst = into / "sheet.jpg"
    return _ffmpeg(argv + ["-filter_complex", ";".join(graph), "-map", "[sheet]",
                           "-frames:v", "1", "-q:v", "4", str(dst)], dst)
