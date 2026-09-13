"""Pure planning and input gates for mask-guided accessory correction."""
import math
from pathlib import Path
from PIL import Image, ImageFilter, ImageStat


def validate_reference(*, area_ratio: float, sharpness: float,
                       min_area_ratio: float = 0.005,
                       min_sharpness: float = 32.0) -> bool:
    if (not math.isfinite(area_ratio) or not math.isfinite(sharpness)
            or area_ratio < min_area_ratio or sharpness < min_sharpness):
        raise ValueError(
            "reference quality is insufficient: provide a clearer accessory image"
        )
    return True


def visible_intervals(visible):
    result = []
    start = None
    for index, value in enumerate(visible + [False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            result.append((start, index))
            start = None
    return result


def prepare_reference(source: Path, destination: Path, box=None):
    with Image.open(source) as image:
        if box is not None:
            if (not isinstance(box, (tuple, list)) or len(box) != 4
                    or any(type(v) is not int for v in box)
                    or not (0 <= box[0] < box[2] <= image.width and 0 <= box[1] < box[3] <= image.height)):
                raise ValueError('referenceBox must be integer pixel coordinates inside the source image')
        crop = image.convert('RGB').crop(box) if box else image.convert('RGB')
        if min(crop.size) < 64:
            raise ValueError('reference quality: crop needs at least 64 native pixels on each edge')
        gray = crop.convert('L')
        blurred = gray.filter(ImageFilter.GaussianBlur(2))
        sharpness = ImageStat.Stat(gray).var[0] - ImageStat.Stat(blurred).var[0]
        area_ratio = (crop.width * crop.height) / (image.width * image.height)
        validate_reference(area_ratio=area_ratio, sharpness=sharpness)
        destination.parent.mkdir(parents=True, exist_ok=True)
        crop.save(destination, format='PNG')
        return {'native_size': [crop.width, crop.height], 'area_ratio': area_ratio,
                'sharpness': sharpness}


def composite_region(original, corrected, mask, box):
    corrected = corrected.resize((box[2] - box[0], box[3] - box[1]), Image.Resampling.LANCZOS)
    original.paste(corrected, (box[0], box[1]), mask.crop(box))
    return original


def choose_reference_frames(*, source_frames, generated_frames, sharpness,
                            minimum_sharpness):
    """Return source-only frames ordered by measured sharpness.

    Generated frames are deliberately excluded so a hallucinated accessory can
    never become the next correction reference.
    """
    generated = set(generated_frames)
    return sorted(
        (frame for frame in source_frames if frame not in generated
         and sharpness.get(frame, 0) >= minimum_sharpness),
        key=lambda frame: sharpness[frame], reverse=True,
    )


def chunk_visible_intervals(intervals, *, chunk_size: int, overlap: int):
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("invalid chunk size or overlap")
    result = []
    for start, end in intervals:
        if end <= start:
            continue
        cursor = start
        while cursor < end:
            stop = min(end, cursor + chunk_size)
            result.append((cursor, stop))
            if stop == end:
                break
            cursor = stop - overlap
    return result
