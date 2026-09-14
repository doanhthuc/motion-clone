"""Why a pod rental failed, for the bot to show instead of a generic
"nothing to send, check the log" message.

Named from the manifest scripts/drain.py was invoked with, same shape as
batchlib_ext.handoff — so drain.py (the writer, from provision()) and
bot.py (the reader, from deliver_result) agree on where to look without
either importing the other. Cleared on the next successful provision, so
a stale failure never outlives the run it was about.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

FAILURE_SUFFIX = ".provision-failed.json"


def provision_failure_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(manifest_path.stem + FAILURE_SUFFIX)


@dataclass(frozen=True)
class ProvisionFailure:
    gpu: str
    datacenter: str | None
    stock_out: bool   # True only for pod-provision.sh's own "no instances
                      # available" classification — everything else (bad
                      # config, RunPod API error, ...) is not decidable from
                      # a fixed menu of buttons, so the bot falls back to
                      # today's generic "check the log" message for those.
    detail: str


def write_provision_failure(path: Path, failure: ProvisionFailure) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({
        "gpu": failure.gpu, "datacenter": failure.datacenter,
        "stock_out": failure.stock_out, "detail": failure.detail,
    }, indent=2), encoding="utf-8")
    tmp.replace(path)   # atomic: a reader never sees a half-written file


def read_provision_failure(path: Path) -> ProvisionFailure | None:
    """None means "nothing to report" — including a corrupt file. Same
    fail-quiet posture as batchlib_ext.handoff.read_handoff.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ProvisionFailure(gpu=str(raw["gpu"]), datacenter=raw.get("datacenter"),
                                stock_out=bool(raw["stock_out"]),
                                detail=str(raw.get("detail", "")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_provision_failure(path: Path) -> None:
    path.unlink(missing_ok=True)
