"""What each Vast machine ACTUALLY did, not what it advertised.

Advertised bandwidth does not predict boot time: the Washington host advertised 1593 Mbps yet
pulled a ghcr layer at 15.9 MB/s and took 556 s to reach `running`; the Bulgaria host advertised
2038 Mbps and took 325 s; the same Bulgaria machine took 35 s the second time (its docker layer
cache survived the destroy). Two hosts is not a model, so this keeps a per-machine history and
lets measurements outrank the marketplace's own numbers (docs/gpu-pod.md#vast-ghcr).

Storage only, no policy about WHICH machine to rent — that lives in vast_select.py. Read like
lease.py: an unreadable file is "no history", never an exception, because a rent that crashes on
its own bookkeeping would block the fallback exactly when RunPod is out of stock.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

# Slowest create -> running ever measured (Washington US, 2026-09-19). A machine nobody has
# measured is scored as this, never optimistically.
WORST_KNOWN_PULL_S = 556.0
# How long a slow or failed machine stays out of the candidate list.
BLACKLIST_TTL_S = 24 * 3600.0


@dataclass(frozen=True)
class MachineRecord:
    machine_id: int
    pull_s: float | None       # create -> actual_status running; a lower bound when it timed out
    model_mbps: float | None   # measured model download throughput; filled by the model probe
    gb: float | None           # GB that measurement covered
    measured_at: float         # unix seconds
    outcome: str               # "ok" | "slow_pull" | "failed"


class Scoreboard:
    def __init__(self, records: dict[int, MachineRecord]):
        self._records: dict[int, MachineRecord] = dict(records)

    def records(self) -> dict[int, MachineRecord]:
        return dict(self._records)

    def get(self, machine_id: int) -> MachineRecord | None:
        return self._records.get(int(machine_id))

    def record(self, rec: MachineRecord) -> None:
        self._records[int(rec.machine_id)] = rec

    def is_blacklisted(self, machine_id: int, now: float,
                       ttl: float = BLACKLIST_TTL_S) -> bool:
        rec = self.get(machine_id)
        return rec is not None and rec.outcome != "ok" and (now - rec.measured_at) < ttl

    def ready_estimate_s(self, machine_id: int) -> float:
        rec = self.get(machine_id)
        if rec is not None and rec.outcome == "ok" and rec.pull_s is not None:
            return rec.pull_s
        return WORST_KNOWN_PULL_S

    def known_good(self, limit: int = 3) -> list[int]:
        good = [r for r in self._records.values()
                if r.outcome == "ok" and r.pull_s is not None]
        good.sort(key=lambda r: r.pull_s)
        return [r.machine_id for r in good[:limit]]


def load_board(path: Path) -> Scoreboard:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        records = {}
        for key, value in raw.items():
            records[int(key)] = MachineRecord(
                machine_id=int(value["machine_id"]),
                pull_s=None if value.get("pull_s") is None else float(value["pull_s"]),
                model_mbps=(None if value.get("model_mbps") is None
                            else float(value["model_mbps"])),
                gb=None if value.get("gb") is None else float(value["gb"]),
                measured_at=float(value["measured_at"]),
                outcome=str(value["outcome"]))
        return Scoreboard(records)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return Scoreboard({})


def save_board(path: Path, board: Scoreboard) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({str(k): asdict(v) for k, v in board.records().items()},
                              indent=2), encoding="utf-8")
    tmp.replace(path)   # atomic: a reader never sees a half-written history
