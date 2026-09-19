"""A read-only price check for one Vast rental, for the bot's provider panel.

Runs the REAL scripts/pod-provision.sh in quote mode (VAST_QUOTE=1) so the search filters, image,
disk and GPU spelling come from the one place that already derives them — a second copy of that
configuration in Python would drift the first time a knob changed. vast_rent.py --quote prints one
JSON line and never rents; this module additionally strips CONFIRM from the child's environment, so
even a bot process that somehow carried it could not turn a price check into a rental.

Cached for TTL_SEC per download size: the panel redraws on every tap, and a search is a marketplace
round trip (about 4 s measured 2026-09-19). Refresh passes force=True.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT_SEC = 120
TTL_SEC = 60.0
# What still has to happen AFTER the instance reports `running`, measured 2026-09-19 on a warm host
# (docs/gpu-pod.md#vast-e2e): SSH answering ~17 s later, bootstrap 200 s (the model download runs
# inside it), ComfyUI restart ~15 s. The per-machine create -> running time is separate: it comes
# from the scoreboard, or is the slowest ever seen when the machine is unmeasured.
BOOT_AFTER_RUNNING_S = 232.0

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True)
class VastQuote:
    offer_id: int
    machine_id: int | None
    dph: float
    gpu: str
    location: str
    ready_s: float
    known: bool
    bandwidth_usd: float
    gb: float
    qualifying: int
    fetched_at: float


_CACHE: dict[float, VastQuote] = {}
_last: VastQuote | None = None


def last_quote() -> VastQuote | None:
    """The most recent quote that succeeded, however old — what the progress message prices a
    running rental with (labelled an estimate; the real rate is on Vast's own invoice)."""
    return _last


def _reason(stderr: str) -> str:
    text = _ANSI.sub("", stderr or "").strip()
    if "✗" in text:
        text = text.rsplit("✗", 1)[1].strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return (lines[0] if lines else "no reason given")[:300]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return (lines[-1] if lines else "no reason given")[:300]


def _parse(stdout: str, *, gb: float, fetched_at: float) -> VastQuote:
    # pod-provision.sh prints its own log/warn lines to stdout before it hands over, so the JSON is
    # the last line that looks like an object, not the whole of stdout.
    objects = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip().startswith("{")]
    if not objects:
        raise RuntimeError("the quote command printed no JSON")
    try:
        d = json.loads(objects[-1])
        machine = d.get("machine_id")
        return VastQuote(
            offer_id=int(d["offer_id"]), machine_id=None if machine is None else int(machine),
            dph=float(d["dph"]), gpu=str(d.get("gpu") or ""), location=str(d.get("location") or ""),
            ready_s=float(d["ready_s"]), known=bool(d["known"]),
            bandwidth_usd=float(d["bandwidth_usd"]), gb=float(d.get("gb", gb)),
            qualifying=int(d.get("qualifying", 0)), fetched_at=fetched_at)
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"the quote command printed unreadable JSON: {exc}") from exc


def fetch_quote(gb: float, *, force: bool = False, run=subprocess.run, now=time.time,
                repo_root: Path = ROOT) -> VastQuote:
    """The best qualifying offer for a rental that will download `gb` GB. Raises RuntimeError with
    the reason (no offer qualifies, vastai missing or logged out, unreadable answer)."""
    global _last
    key = round(float(gb), 1)
    t = now()
    cached = _CACHE.get(key)
    if cached is not None and not force and t - cached.fetched_at < TTL_SEC:
        return cached
    env = {k: v for k, v in os.environ.items() if k != "CONFIRM"}
    env.update({"GPU_PROVIDER": "vast", "POD_VOLUME": "", "VAST_QUOTE": "1",
                "VAST_GB": f"{key:.1f}"})
    try:
        out = run(["bash", str(repo_root / "scripts" / "pod-provision.sh")], cwd=repo_root,
                  env=env, capture_output=True, text=True, timeout=TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not run pod-provision.sh: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(_reason(out.stderr))
    quote = _parse(out.stdout, gb=key, fetched_at=t)
    _CACHE[key] = quote
    _last = quote
    return quote
