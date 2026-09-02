"""Mailbox + status protocol for chaining a queued batch onto a pod that is
already rented, instead of destroying it and renting a fresh one.

Two files per chain, named from the ORIGINAL manifest path — the one
scripts/drain.py was invoked with — so both drain.py (the writer, once it
decides whether to chain or destroy) and the bot (the reader, to keep the
progress message honest) agree on where to look without either importing
the other:

  <name>.next.yaml     the mailbox. bot.py writes a validated manifest here
                        while a drain is running on <name>.yaml. drain.py
                        claims it (renames it out, to a permanent name) the
                        moment it is ready to pick up the next link — that
                        rename is what frees the mailbox for a THIRD job to
                        be queued while the second is running.
  <name>.handoff.json   the outcome of the CURRENT chain attempt: "starting"
                        (about to run — transient, nobody needs to act on
                        it), "running" (picked up, now the live link), or
                        "failed" (pickup did not work; the pod was still
                        destroyed as usual, nothing extra was billed).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

MAILBOX_SUFFIX = ".next.yaml"
HANDOFF_SUFFIX = ".handoff.json"


def mailbox_path(original_manifest: Path) -> Path:
    return original_manifest.with_name(original_manifest.stem + MAILBOX_SUFFIX)


def handoff_path(original_manifest: Path) -> Path:
    return original_manifest.with_name(original_manifest.stem + HANDOFF_SUFFIX)


@dataclass(frozen=True)
class Handoff:
    status: str          # "starting" | "running" | "failed"
    manifest: str         # the chained manifest this status is about
    reason: str | None = None


def write_handoff(path: Path, handoff: Handoff) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"status": handoff.status, "manifest": handoff.manifest,
                               "reason": handoff.reason}, indent=2), encoding="utf-8")
    tmp.replace(path)   # atomic: a reader never sees a half-written handoff


def read_handoff(path: Path) -> Handoff | None:
    """None means "no handoff attempted" — including a corrupt file. A tick
    that cannot tell what happened must stay quiet rather than guess, the
    same failure-open posture as batchlib_ext.lease.read_lease.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Handoff(status=str(raw["status"]), manifest=str(raw["manifest"]),
                       reason=raw.get("reason"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def claim_mailbox(original_manifest: Path) -> Path | None:
    """Take whatever is queued next and give it a permanent name, freeing the
    mailbox immediately — so a THIRD job can be queued while this one runs,
    rather than the queue being a one-shot handoff. None if nothing is queued,
    or the claim itself failed (a missing mailbox and a failed rename look
    the same to the caller: nothing to chain onto, destroy as usual).
    """
    mailbox = mailbox_path(original_manifest)
    if not mailbox.exists():
        return None
    working = original_manifest.with_name(
        f"{original_manifest.stem}-{int(time.time())}.yaml")
    try:
        mailbox.rename(working)
    except OSError:
        return None
    return working
