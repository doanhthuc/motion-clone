#!/usr/bin/env python3
"""Copy a Network Volume to another datacenter, swap .env to it, delete the
original — the automated form of docs/gpu-pod.md's manual EU-CZ-1 runbook.

    python3 scripts/volume_migrate.py --to-dc EU-CZ-1              # dry run
    python3 scripts/volume_migrate.py --to-dc EU-CZ-1 --yes        # for real

See docs/superpowers/specs/2026-09-02-volume-region-migration-design.md.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.config import env_get, env_set
from batchlib_ext.migrate_lease import (MigrateLease, clear_migrate_lease,
                                        write_migrate_lease)

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
LEASE_PATH = ROOT / "batch" / "volume-migrate-lease.json"
PROGRESS_PATH = ROOT / "batch" / "volume-migrate.progress.json"


def sh(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=ROOT, check=True, capture_output=True, text=True)


def _rp_json(*argv: str) -> dict:
    out = sh("runpodctl", *argv, "-o", "json")
    return json.loads(out.stdout or "{}")


def write_progress(phase: str, **extra) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps({"phase": phase, "at": time.time(), **extra},
                                        indent=2), encoding="utf-8")


def create_volume(source_volume_id: str, to_dc: str) -> tuple[str, int, str]:
    """Create the destination volume, same size as the source (RunPod only
    allows growing a volume later, never shrinking — under-sizing here means
    a second migration). Returns (new_volume_id, size_gb, source_dc)."""
    source = _rp_json("network-volume", "get", source_volume_id)
    size_gb = int(source["size"])
    source_dc = source.get("dataCenterId") or source.get("datacenterId") or ""
    name = f"{source.get('name', 'motion')}-{to_dc.lower()}"
    created = _rp_json("network-volume", "create", "--name", name,
                       "--size", str(size_gb), "--data-center-id", to_dc)
    return created["id"], size_gb, source_dc


def provision_temp_pod() -> None:
    """Placeholder for phases 2-6 in later tasks."""
    pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to-dc", required=True)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    old_volume_id = env_get(ENV_PATH, "POD_VOLUME_ID")
    if not old_volume_id:
        print("✗ POD_VOLUME_ID not set in .env — nothing to migrate", file=sys.stderr)
        return 1

    new_volume_id, size_gb, source_dc = create_volume(old_volume_id, args.to_dc)
    if not args.yes:
        print(f"DRY RUN. Would migrate {size_gb}GB from POD_VOLUME_ID={old_volume_id} "
             f"({source_dc}) to {args.to_dc} (created {new_volume_id} already — "
             f"delete it by hand if you do not proceed: "
             f"runpodctl network-volume delete {new_volume_id}).")
        print("Re-run with --yes to actually copy and swap.")
        return 0

    # Phases 2-6 land in later tasks of this plan.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
