"""Path guards shared by every adapter that turns a user-supplied name into a file."""
from __future__ import annotations

from pathlib import Path


def safe_child(root: Path, name: str) -> Path | None:
    """Resolve `name` as a single path component directly under `root`, or None.

    The allowlist (`allowed()`) restricts WHO can message the bot, not WHAT
    the one allowed user types or pastes — a mistyped or copy-pasted path is
    enough to turn `/result`/`/tryon` into a probe for arbitrary files this
    process can see. `name` is meant to be a bare filename or a bare batch
    id, never a path, so an absolute argument, a literal ".." anywhere, or
    any path separator at all is refused outright — three independent
    reasons the same mistake would be caught. Then the joined path is
    resolved (symlinks and any remaining "." collapsed) and re-checked with
    `is_relative_to` against the resolved root, a second, independent check
    after the first.
    """
    name = name.strip()
    if not name or Path(name).is_absolute() or ".." in name or "/" in name or "\\" in name:
        return None
    root_resolved = root.resolve()
    candidate = (root_resolved / name).resolve()
    if not candidate.is_relative_to(root_resolved):
        return None
    return candidate
