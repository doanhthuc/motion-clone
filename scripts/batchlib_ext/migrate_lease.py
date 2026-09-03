"""Who currently owns the two TEMPORARY CPU pods a volume migration rents —
distinct from batchlib_ext.lease.Lease, which names the one real GPU pod.
That shape has a single pod_id; a migration needs two, so this is its own
small dataclass rather than overloading the GPU pod's contract.

Storage only — no policy. The kill decision lives in watchdog.py, same
split as the GPU pod's own lease.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrateLease:
    pod_a_id: str      # temp pod mounting the SOURCE volume
    pod_b_id: str      # temp pod mounting the DESTINATION volume
    started_at: float  # unix seconds, set once when both pods are confirmed provisioned
    to_dc: str         # destination datacenter id


def write_migrate_lease(path: Path, lease: MigrateLease) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(lease), indent=2), encoding="utf-8")
    tmp.replace(path)


def read_migrate_lease(path: Path) -> MigrateLease | None:
    """None means 'no lease' — including an unreadable file. Tier 3
    reconciliation then treats the two temp pods as ordinary orphans, which
    is the safe direction to fail in (same reasoning as
    batchlib_ext.lease.read_lease)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return MigrateLease(pod_a_id=str(raw["pod_a_id"]),
                            pod_b_id=str(raw["pod_b_id"]),
                            started_at=float(raw["started_at"]),
                            to_dc=str(raw["to_dc"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def clear_migrate_lease(path: Path) -> None:
    path.unlink(missing_ok=True)
