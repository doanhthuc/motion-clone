"""Live RunPod GPU stock via runpodctl. Read-only — no policy, no renting.

Same JSON `runpodctl gpu list -o json` returns that docs/gpu-pod.md's own
worked examples (section 0.3, and the 5090-vs-others comparison) already
filter by hand. This gives the bot's /gpu command the identical shape so a
live check and a manual `grep -A3 EU-RO-1` never disagree about the field
names.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

_TIMEOUT_SEC = 30   # a stock query is one HTTP round trip behind runpodctl;
                    # this only bounds a hung CLI from blocking the poll loop.


@dataclass(frozen=True)
class Stock:
    gpu_id: str
    display_name: str
    price_per_hr: float | None
    datacenter_id: str
    stock_status: str   # runpodctl's own spelling: "High" / "Medium" / "Low" / "none"


def volume_datacenter(volume_id: str) -> str | None:
    """Where a Network Volume lives — the ONLY datacenter a pod using it can
    rent in (scripts/pod-provision.sh's own VOL_DC lookup, docs/gpu-pod.md
    §Ràng buộc quyết định trước cả VRAM). None if there is no volume
    configured, or runpodctl cannot answer — never raises, because a stock
    check with no answer should fall back to "show every datacenter"
    rather than take the whole /gpu command down.
    """
    if not volume_id:
        return None
    try:
        out = subprocess.run(
            ["runpodctl", "network-volume", "get", volume_id, "-o", "json"],
            capture_output=True, text=True, timeout=_TIMEOUT_SEC)
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout or "{}")
    # OSError (FileNotFoundError if runpodctl itself is missing from PATH,
    # PermissionError, ...) is a SIBLING of subprocess.SubprocessError, not
    # a subclass — catching only the latter let a missing binary raise
    # straight through this "never raises" function.
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return data.get("dataCenterId") or data.get("datacenterId") or None


def stock_at(gpu_ids: list[str]) -> dict[str, list[Stock]]:
    """Every datacenter each requested gpu_id is listed at, stock included.

    One entry per (gpu, datacenter) pair rather than a single collapsed
    status: renting outside the Network Volume's own datacenter is a real
    option here (docs/gpu-pod.md's EU-CZ-1 failover), so the caller needs
    to see every candidate, not just the one that matters most, and decide
    how to rank/highlight them. A gpu_id runpodctl does not currently list
    at all is simply absent from the result.
    """
    try:
        out = subprocess.run(["runpodctl", "gpu", "list", "-o", "json"],
                             capture_output=True, text=True, timeout=_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as exc:
        # Same OSError-vs-SubprocessError split as volume_datacenter above,
        # but this function DOES raise on failure (its callers decide how
        # to degrade) — so a missing/hung runpodctl has to become the same
        # RuntimeError the returncode/JSON checks below already raise,
        # rather than a different exception type callers didn't ask for.
        raise RuntimeError(f"could not run runpodctl: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(f"runpodctl gpu list failed: {out.stderr.strip()}")
    try:
        rows = json.loads(out.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"runpodctl returned invalid JSON: {exc}") from exc

    wanted = set(gpu_ids)
    result: dict[str, list[Stock]] = {}
    for row in rows:
        gpu_id = row.get("gpuId")
        if gpu_id not in wanted:
            continue
        entries = [
            Stock(gpu_id=gpu_id, display_name=row.get("displayName", gpu_id),
                 price_per_hr=row.get("securePricePerHr"),
                 datacenter_id=dc.get("dataCenterId", "?"),
                 stock_status=dc.get("stockStatus", "none"))
            for dc in row.get("dataCenterAvailability") or []
        ]
        result[gpu_id] = entries
    return result
