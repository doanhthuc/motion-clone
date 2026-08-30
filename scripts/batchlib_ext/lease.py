"""Who currently owns a rented pod, on disk.

Storage only — no policy. The decision to kill a pod lives in watchdog.py, so
it can be unit-tested without touching a filesystem or RunPod.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Lease:
    pod_id: str
    provisioned_at: float   # unix seconds, set once at provision time
    manifest: str           # path to the manifest this pod was rented for
    abs_max_min: int        # tier-2 ceiling, computed once at provision time


def write_lease(path: Path, lease: Lease) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(lease), indent=2), encoding="utf-8")
    tmp.replace(path)   # atomic: a reader never sees a half-written lease


def read_lease(path: Path) -> Lease | None:
    """None means 'no lease', including when the file is unreadable garbage.

    Raising here would take the watchdog down, and a dead watchdog bills
    $0.99/hour silently. Unreadable is treated as absent on purpose: tier 3
    reconciliation then sees a pod with no lease and destroys it, which is the
    safe direction to fail in.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Lease(pod_id=str(raw["pod_id"]),
                     provisioned_at=float(raw["provisioned_at"]),
                     manifest=str(raw["manifest"]),
                     abs_max_min=int(raw["abs_max_min"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_lease(path: Path) -> None:
    path.unlink(missing_ok=True)
