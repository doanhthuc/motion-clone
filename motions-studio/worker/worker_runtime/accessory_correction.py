"""Pure planning and input gates for mask-guided accessory correction."""


def validate_reference(*, area_ratio: float, sharpness: float,
                       min_area_ratio: float = 0.005,
                       min_sharpness: float = 32.0) -> bool:
    if area_ratio < min_area_ratio or sharpness < min_sharpness:
        raise ValueError(
            "reference quality is insufficient: provide a clearer accessory image"
        )
    return True


def choose_reference_frames(*, source_frames, generated_frames, sharpness,
                            minimum_sharpness):
    """Return source-only frames ordered by measured sharpness.

    Generated frames are deliberately excluded so a hallucinated accessory can
    never become the next correction reference.
    """
    return sorted(
        (frame for frame in source_frames if sharpness.get(frame, 0) >= minimum_sharpness),
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
