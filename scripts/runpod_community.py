#!/usr/bin/env python3
"""RunPod Community Cloud: the host filters, the create body, and a stock check that uses them.

    python3 scripts/runpod_community.py body      # POST /v1/pods request body (env + .env)
    python3 scripts/runpod_community.py summary   # one human line describing the filters
    python3 scripts/runpod_community.py pod-id    # create response (stdin) -> pod id, or ""
    python3 scripts/runpod_community.py cost      # create response (stdin) -> costPerHr, or ""

One module for pod-provision.sh (which rents) and the bot (which quotes price and stock before
anyone taps Run), so the filters a quote is checked against are the filters the rent uses.

Why filters at all: a Community host is a third-party machine, and they vary like Vast's do
(docs/gpu-pod.md#vast-slow measured a 32x disk spread on one GPU model). Unlike Vast there is
no offer list to rank — RunPod picks the machine — so these filters are all the say we get
before renting. What they cannot catch (a throttled GPU, a slow route to HuggingFace, an image
pull that never finishes) drain.py checks after the rent, and replaces the pod.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]

# The name the watchdog may destroy (scripts/batchlib_ext/watchdog.py DESTROYABLE_NAMES), same as
# the Secure branch's `--name`. Renaming it here and not there disarms tier 3.
POD_NAME = "motion-transfer"

# Catalog list price for the RTX 5090 on Community, read 2026-10-03 (Secure: $0.99). Only a
# fallback for when the live price cannot be read; the rent prints costPerHr from the response.
DEFAULT_USD_PER_HR = 0.69

# REST /v1/pods takes an exact list (allowedCudaVersions), not a floor like runpodctl's
# --min-cuda-version. These are the versions the catalog listed for the RTX 5090 on 2026-10-03
# (12.8-13.3 across Secure and Community), plus two ahead. A newer version RunPod adds later is
# excluded until it is appended here — the safe direction: an unknown host is never rented.
KNOWN_CUDA_VERSIONS = ("12.8", "12.9", "13.0", "13.1", "13.2", "13.3", "13.4", "13.5")

GRAPHQL_URL = "https://api.runpod.io/graphql"
_TIMEOUT_S = 15


def dotenv_get(key: str, env_path: Path = ROOT / ".env") -> str:
    """KEY from the process environment, then .env — the precedence every script here uses."""
    if os.environ.get(key):
        return os.environ[key]
    try:
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    value = ""
    for line in lines:
        if line.startswith(f"{key}="):
            value = line.split("=", 1)[1].split("#", 1)[0].strip().replace('"', "")
    return value


def cloud(get: Callable[[str], str] = dotenv_get) -> str:
    return (get("RUNPOD_CLOUD") or "SECURE").strip().upper()


def is_community(get: Callable[[str], str] = dotenv_get) -> bool:
    return (get("GPU_PROVIDER") or "vast") == "runpod" and cloud(get) == "COMMUNITY"


@dataclass(frozen=True)
class Filters:
    # Defaults: the Vast floors (1000 Mbps inet_down, MIN_DISK_BW=3000 MB/s) and a host with room
    # for Wan's CPU offload. Assumptions until a Community rental is measured.
    min_download_mbps: float = 1000.0
    min_disk_mbps: float = 3000.0
    min_vcpu: int = 8
    min_ram_gb: int = 32
    countries: tuple[str, ...] = ()
    min_cuda: str = "13.0"

    @classmethod
    def from_env(cls, get: Callable[[str], str] = dotenv_get) -> "Filters":
        d = cls()
        countries = tuple(c.strip().upper() for c in (get("RUNPOD_COUNTRIES") or "").split(",")
                          if c.strip())
        return cls(
            min_download_mbps=float(get("RUNPOD_MIN_DOWNLOAD_MBPS") or d.min_download_mbps),
            min_disk_mbps=float(get("RUNPOD_MIN_DISK_MBPS") or get("MIN_DISK_BW")
                                or d.min_disk_mbps),
            min_vcpu=int(get("RUNPOD_MIN_VCPU") or d.min_vcpu),
            min_ram_gb=int(get("RUNPOD_MIN_RAM_GB") or d.min_ram_gb),
            countries=countries,
            min_cuda=get("MIN_CUDA_VERSION") or d.min_cuda,
        )

    def summary(self) -> str:
        parts = [f"download >= {self.min_download_mbps:.0f} Mbps",
                 f"disk >= {self.min_disk_mbps:.0f} MB/s",
                 f">= {self.min_vcpu} vCPU", f">= {self.min_ram_gb} GB RAM",
                 f"CUDA >= {self.min_cuda}"]
        if self.countries:
            parts.append("countries " + ",".join(self.countries))
        return " · ".join(parts)


def _version(v: str) -> tuple[int, int]:
    major, _, minor = v.strip().partition(".")
    return int(major), int(minor or 0)


def cuda_versions_from(floor: str) -> list[str]:
    """Every known CUDA version at or above `floor`, compared numerically (13.10 > 13.2)."""
    want = _version(floor)
    out = [v for v in KNOWN_CUDA_VERSIONS if _version(v) >= want]
    if not out:
        raise ValueError(f"MIN_CUDA_VERSION={floor} is above every known version "
                         f"{KNOWN_CUDA_VERSIONS}")
    return out


def build_body(*, gpu: str, image: str, disk_gb: int, filters: Filters) -> dict:
    body = {
        "name": POD_NAME,
        "cloudType": "COMMUNITY",
        "computeType": "GPU",
        "gpuTypeIds": [gpu],
        "gpuCount": 1,
        "imageName": image,
        "containerDiskInGb": int(disk_gb),
        # 22/tcp on a public IP: pod-bootstrap.sh rsyncs over plain ssh. A Community pod without a
        # public IP is reachable only through RunPod's ssh proxy, which does not carry rsync/scp.
        "ports": ["22/tcp"],
        "supportPublicIp": True,
        "allowedCudaVersions": cuda_versions_from(filters.min_cuda),
        "minDownloadMbps": filters.min_download_mbps,
        "minDiskBandwidthMBps": filters.min_disk_mbps,
        "minVCPUPerGPU": filters.min_vcpu,
        "minRAMPerGPU": filters.min_ram_gb,
    }
    if filters.countries:
        body["countryCodes"] = list(filters.countries)
    return body


@dataclass(frozen=True)
class Stock:
    status: str | None          # "High" | "Medium" | "Low", or None: no host passes the filters
    usd_per_hr: float | None

    @property
    def sold_out(self) -> bool:
        return not self.status or self.status.lower() == "none"


def stock_query(gpu: str, filters: Filters, disk_gb: int) -> str:
    """GraphQL lowestPrice for one Community GPU under the rent's own filters.

    GpuLowestPriceInput has no disk-bandwidth field (checked 2026-10-03: "minDiskBandwidth is not
    defined"), so a host can pass this check and still be refused by the rent for its disk — the
    rent then reports a stock-out, same as it would have here.
    """
    args = [
        "gpuCount: 1", "secureCloud: false", "supportPublicIp: true",
        f"minDownload: {int(filters.min_download_mbps)}",
        f"minVcpuCount: {int(filters.min_vcpu)}",
        f"minMemoryInGb: {int(filters.min_ram_gb)}",
        f"minDisk: {int(disk_gb)}",
    ]
    if len(filters.countries) == 1:
        args.append(f"countryCode: {json.dumps(filters.countries[0])}")
    return ("query { gpuTypes(input: {id: %s}) { communityPrice lowestPrice(input: {%s}) "
            "{ stockStatus uninterruptablePrice } } }" % (json.dumps(gpu), ", ".join(args)))


def parse_stock(raw: dict) -> Stock:
    rows = ((raw or {}).get("data") or {}).get("gpuTypes") or []
    if not rows:
        return Stock(None, None)
    row = rows[0] or {}
    lowest = row.get("lowestPrice") or {}
    price = lowest.get("uninterruptablePrice") or row.get("communityPrice")
    return Stock(lowest.get("stockStatus") or None, float(price) if price else None)


def fetch_stock(gpu: str, *, filters: Filters, disk_gb: int, api_key: str) -> Stock:
    """Live Community stock for `gpu` under `filters`. Raises RuntimeError on any failure — the
    caller decides how to degrade, like gpu_stock.stock_at."""
    req = urllib.request.Request(
        GRAPHQL_URL, method="POST",
        data=json.dumps({"query": stock_query(gpu, filters, disk_gb)}).encode(),
        # A User-Agent is required: RunPod's edge answers urllib's default one with 403
        # (measured 2026-10-03; curl, with its own UA, got 200 for the same query).
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "User-Agent": "motion-clone/runpod_community"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            raw = json.loads(resp.read().decode() or "{}")
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"RunPod stock query failed: {exc}") from exc
    if raw.get("errors"):
        raise RuntimeError(f"RunPod stock query failed: {raw['errors']}")
    return parse_stock(raw)


_CACHE_TTL_S = 60.0
_cache: dict[tuple, tuple[float, Stock]] = {}


def stock_now(*, force: bool = False, get: Callable[[str], str] = dotenv_get) -> Stock:
    """The configured GPU's Community stock under the configured filters, for the bot's panels.

    Cached for 60 s like gpu_stock.stock_at_cached — the Telegram panel redraws on nearly every
    action. `force` is the Refresh button. Raises RuntimeError when RunPod cannot be asked."""
    import time
    gpu = get("GPU") or "NVIDIA GeForce RTX 5090"
    filters = Filters.from_env(get)
    disk = int(get("DISK") or 120)
    key = (gpu, filters, disk)
    hit = _cache.get(key)
    if hit and not force and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]
    api_key = get("RUNPOD_API_KEY")
    if not api_key:
        raise RuntimeError("RUNPOD_API_KEY is not set")
    stock = fetch_stock(gpu, filters=filters, disk_gb=disk, api_key=api_key)
    _cache[key] = (time.monotonic(), stock)
    return stock


def _field(raw: str, key: str) -> str:
    try:
        data = json.loads(raw)
    except ValueError:
        return ""
    value = data.get(key) if isinstance(data, dict) else None   # REST errors come back as a list
    return "" if value in (None, "") else str(value)


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd in ("body", "summary"):
        try:
            filters = Filters.from_env()
            if cmd == "summary":
                print(filters.summary())
            else:
                print(json.dumps(build_body(gpu=os.environ["GPU"], image=os.environ["IMAGE"],
                                            disk_gb=int(os.environ["DISK"]), filters=filters)))
        except (KeyError, ValueError) as exc:
            print(f"runpod_community.py: {exc!r}", file=sys.stderr)
            return 1
        return 0
    if cmd == "pod-id":
        print(_field(sys.stdin.read(), "id"))
        return 0
    if cmd == "cost":
        print(_field(sys.stdin.read(), "costPerHr"))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
