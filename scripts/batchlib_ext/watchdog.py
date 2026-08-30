"""Should this pod be destroyed right now? Pure functions, no I/O.

Everything here takes the clock as an argument so the tiers can be tested
without sleeping and without a real pod.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from batchlib.pipelines import STAGES

from .lease import Lease

# Longest declared stage timeout (enhance, 90 min as of 2026-08-30). Derived,
# never hardcoded: pipelines.py is the single place stage timeouts are declared.
LONGEST_STAGE_MIN = max(s.timeout_min for s in STAGES.values())


@dataclass(frozen=True)
class Verdict:
    kill: bool
    reason: str


def in_flight_stage(state: dict) -> str | None:
    """Name of the stage currently marked running, if any."""
    for entry in (state.get("runs") or {}).values():
        for name, stage in (entry.get("stages") or {}).items():
            if stage.get("status") == "running":
                return name
    return None


def decide(*, lease: Lease, state: dict, journal_mtime: float, now: float,
           slack_min: int = 15) -> Verdict:
    """Tier 1 (dead-man's switch) then tier 2 (absolute ceiling).

    Tier 1 leash is the CURRENT stage's own timeout plus slack. Anything
    shorter would kill a stage the runner still legitimately considers alive —
    enhance really does take up to 90 minutes with no journal write in between.
    Anything longer keeps paying $0.99/hour after a crash.
    """
    age_min = (now - lease.provisioned_at) / 60.0
    if age_min > lease.abs_max_min:
        return Verdict(True, f"absolute ceiling: alive {age_min:.0f} min "
                             f"> {lease.abs_max_min} min")

    stage = in_flight_stage(state)
    budget = (STAGES[stage].timeout_min if stage else LONGEST_STAGE_MIN) + slack_min
    silent_min = (now - journal_mtime) / 60.0
    if silent_min > budget:
        where = stage or "no stage running"
        return Verdict(True, f"journal silent {silent_min:.0f} min "
                             f"> {budget} min budget ({where})")

    return Verdict(False, "")
