"""Helpers for focused worker entrypoints."""

from __future__ import annotations

import os


def set_default_job_types(types: str) -> None:
    """Set JOB_TYPES only when the caller did not configure it explicitly."""
    if not os.environ.get("JOB_TYPES"):
        os.environ["JOB_TYPES"] = types
