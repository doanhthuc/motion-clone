"""Which Vast offers qualify, and in what order — pure functions, no I/O, no clock.

Rank by what it costs to GET READY, not by $/hour alone (spec §3.2):

    score = dph_total * ready_s / 3600  +  gb_to_download * internet_down_cost_per_tb / 1000

`ready_s` is the machine's own measured create -> running time when the scoreboard has one, and
the slowest ever measured (556 s) when it does not. Bandwidth is billed per TB: a 50 GB boot is
$2.00 at the Hungarian flat $40/TB and $0.07 at Bulgaria's $1.37/TB, so an expensive-bandwidth host
is excluded up front rather than discovered on the invoice (docs/gpu-pod.md#vast-ghcr).

`inet_down` is a FILTER here, not a predictor: the Washington host advertised 1593 Mbps and pulled
a ghcr layer at 15.9 MB/s. It keeps obviously slow lines out; the scoreboard does the real work.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .vast_scoreboard import Scoreboard


@dataclass(frozen=True)
class Criteria:
    max_dph: float
    min_disk_bw: float
    min_cpu_ghz: float
    min_inet_mbps: float
    max_down_usd_per_tb: float
    skip_offers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Ranked:
    offer: dict
    machine_id: int | None
    ready_s: float
    known: bool            # True when ready_s is a measurement, False when it is the worst case
    bandwidth_usd: float
    score: float


def reject_reason(offer: dict, c: Criteria, board: Scoreboard, now: float) -> str | None:
    """The first reason this offer does not qualify; None means it does."""
    if str(offer.get("id")) in c.skip_offers:
        return "skipped"
    if not offer.get("rentable", True):
        return "not rentable"
    dph = offer.get("dph_total")
    if dph is None or dph > c.max_dph:
        return "over price cap"
    if (offer.get("disk_bw") or 0) < c.min_disk_bw:
        return "disk too slow"
    if (offer.get("cpu_ghz") or 0) < c.min_cpu_ghz:
        return "cpu too slow"
    if (offer.get("inet_down") or 0) < c.min_inet_mbps:
        return "advertised bandwidth too low"
    cost = offer.get("internet_down_cost_per_tb")
    if cost is None:
        return "bandwidth cost unknown"        # unknown is assumed expensive, never free
    if cost > c.max_down_usd_per_tb:
        return "bandwidth too expensive"
    if not (offer.get("direct_port_count") or 0):
        return "no direct ports"               # `create --direct` needs them
    machine_id = offer.get("machine_id")
    if machine_id is not None and board.is_blacklisted(int(machine_id), now):
        return "machine blacklisted"
    return None


def rank(offers: list[dict], c: Criteria, board: Scoreboard, *, gb: float,
         now: float) -> tuple[list[Ranked], Counter]:
    rejected: Counter = Counter()
    ranked: list[Ranked] = []
    for o in offers:
        reason = reject_reason(o, c, board, now)
        if reason is not None:
            rejected[reason] += 1
            continue
        raw_id = o.get("machine_id")
        machine_id = None if raw_id is None else int(raw_id)
        record = None if machine_id is None else board.get(machine_id)
        known = record is not None and record.outcome == "ok" and record.pull_s is not None
        ready_s = board.ready_estimate_s(machine_id) if machine_id is not None \
            else board.ready_estimate_s(-1)
        bandwidth_usd = gb * float(o["internet_down_cost_per_tb"]) / 1000.0
        score = float(o["dph_total"]) * ready_s / 3600.0 + bandwidth_usd
        ranked.append(Ranked(o, machine_id, ready_s, known, bandwidth_usd, score))
    ranked.sort(key=lambda r: (r.score, r.offer["dph_total"]))
    return ranked, rejected


def dedupe(offers: list[dict]) -> list[dict]:
    seen: set = set()
    out = []
    for o in offers:
        key = o.get("id")
        if key in seen:
            continue
        seen.add(key)
        out.append(o)
    return out


def explain_rejections(rejected: Counter, total: int) -> str:
    if total == 0:
        return "no offers matched the search at all — loosen GPU= or DISK="
    parts = ", ".join(f"{n} {reason}" for reason, n in rejected.most_common())
    return (f"0 of {total} offers qualify ({parts}).\n"
            "  Knobs (env or .env): MAX_DPH, MIN_DISK_BW, MIN_CPU_GHZ, VAST_MIN_INET_MBPS, "
            "VAST_MAX_DOWN_USD_PER_TB; SKIP=<offer ids> to exclude one.")


def format_table(ranked: list[Ranked], top: int = 5) -> str:
    lines = []
    for r in ranked[:top]:
        o = r.offer
        gb = (o.get("gpu_ram") or 0) / 1024
        lines.append(
            f"  id={o.get('id')!s:<10} ${o.get('dph_total', 0):.3f}/hr  "
            f"ready≈{r.ready_s:.0f}s ({'measured' if r.known else 'unmeasured'})  "
            f"bw ${r.bandwidth_usd:.2f}  score ${r.score:.3f}  {o.get('gpu_name')} {gb:.0f}GB  "
            f"disk={o.get('disk_bw') or 0:.0f}MB/s  down={o.get('inet_down') or 0:.0f}Mbps  "
            f"{o.get('geolocation', '?')}")
    return "\n".join(lines)
