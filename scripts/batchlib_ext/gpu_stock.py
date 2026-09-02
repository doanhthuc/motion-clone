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
    except (subprocess.SubprocessError, json.JSONDecodeError):
        return None
    return data.get("dataCenterId") or data.get("datacenterId") or None


def stock_at(gpu_ids: list[str], datacenter_id: str | None) -> dict[str, Stock]:
    """One Stock per requested gpu_id.

    `stock_status` is narrowed to `datacenter_id` when one is given — a GPU
    can read High overall while being `none` at the one datacenter that
    actually matters, which is exactly the case the volume-locked deploy
    (docs/gpu-pod.md) needs to see rather than the marketing-page number.
    A gpu_id runpodctl does not currently list at all is simply absent from
    the result — the caller decides how to word that, this module only
    reports what it saw.
    """
    out = subprocess.run(["runpodctl", "gpu", "list", "-o", "json"],
                         capture_output=True, text=True, timeout=_TIMEOUT_SEC)
    if out.returncode != 0:
        raise RuntimeError(f"runpodctl gpu list failed: {out.stderr.strip()}")
    try:
        rows = json.loads(out.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"runpodctl returned invalid JSON: {exc}") from exc

    wanted = set(gpu_ids)
    result: dict[str, Stock] = {}
    for row in rows:
        gpu_id = row.get("gpuId")
        if gpu_id not in wanted:
            continue
        status = row.get("stockStatus", "none")
        if datacenter_id:
            per_dc = {d.get("dataCenterId"): d.get("stockStatus")
                     for d in row.get("dataCenterAvailability") or []}
            status = per_dc.get(datacenter_id, "none")
        result[gpu_id] = Stock(gpu_id=gpu_id,
                               display_name=row.get("displayName", gpu_id),
                               price_per_hr=row.get("securePricePerHr"),
                               stock_status=status)
    return result
