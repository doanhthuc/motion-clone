#!/usr/bin/env python3
"""Rent a Vast.ai instance: rank by time-to-ready and cost, create, wait, give up on a slow pull.

    python3 scripts/vast_rent.py --gpu RTX_5090 --disk 120 --image IMG …            # dry run
    python3 scripts/vast_rent.py … --confirm                                        # rents
    python3 scripts/vast_rent.py --ssh-target <instance id>                         # "host port"

Called by pod-provision.sh's Vast branch. Prints ONLY the offer id (dry run) or the instance id
(--confirm) on stdout; everything human-readable goes to stderr.

Safety properties, each pinned by a test:
- every instance carries --label motion-transfer (the name the watchdog's tier 3 may destroy) and
  is created with --cancel-unavail (an error instead of a stopped instance that still bills);
- an instance abandoned for a slow pull is destroyed and the destroy is CHECKED — if it cannot be
  destroyed we stop renting and say "STILL BILLING" with its id;
- any exception (Ctrl-C included) between create and success destroys the new instance;
- GPU_INSTANCE_ID in .env is written the moment an instance exists and cleared when it is
  abandoned, so a stale id can never be read into a lease.
Search returns a random ~40-row sample (measured 2026-09-19), so known-good machines are also
queried by machine_id, which is deterministic.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.config import env_get, env_set
from batchlib_ext.podctl import VastCtl
from batchlib_ext.vast_scoreboard import (MachineRecord, Scoreboard, load_board,
                                          save_board)
from batchlib_ext.vast_select import (Criteria, Ranked, dedupe, explain_rejections,
                                      format_table, rank)

ROOT = Path(__file__).resolve().parents[1]
BOARD_PATH = ROOT / "batch" / "vast-machines.json"

VAST_LABEL = "motion-transfer"           # == a member of batchlib_ext.watchdog.DESTROYABLE_NAMES
DEFAULT_PULL_DEADLINE_S = 8 * 60         # must stay < the watchdog's GRACE_MIN (10 min) with slack
MAX_PULL_RETRIES = 2                     # spec §3.2: at most 2 retries after the first pull
MAX_CREATE_FAILURES = 6                  # offers vanish between search and create
POLL_S = 10
FAILED_STATUSES = frozenset({"exited", "error", "offline", "unknown_error"})
KNOWN_GOOD_QUERIES = 3


class RentError(RuntimeError):
    pass


class NoOffers(RentError):
    pass


class VastApi(Protocol):
    def search_offers(self, query: str) -> list[dict]: ...
    def create_instance(self, offer_id, *, image: str, disk_gb: int, label: str) -> str: ...
    def instance_status(self, instance_id: str) -> dict: ...
    def destroy_instance(self, instance_id: str) -> None: ...
    def ssh_url(self, instance_id: str) -> str: ...


class RealVastApi:
    def _run(self, argv: list[str], timeout: int):
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RentError(f"could not run {argv[0]}: {exc}") from exc

    def search_offers(self, query: str) -> list[dict]:
        out = self._run(["vastai", "search", "offers", query, "-o", "dph+", "--raw"], 90)
        if out.returncode != 0:
            raise RentError(f"vastai search failed: {out.stderr.strip()} — is your API key set?")
        try:
            data = json.loads(out.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise RentError(f"vastai search returned invalid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise RentError(f"vastai search returned {type(data).__name__}, not a list")
        return data

    def create_instance(self, offer_id, *, image: str, disk_gb: int, label: str) -> str:
        out = self._run(["vastai", "create", "instance", str(offer_id), "--image", image,
                         "--disk", str(disk_gb), "--ssh", "--direct", "--label", label,
                         "--cancel-unavail", "--raw"], 120)
        if out.returncode != 0:
            raise RentError(f"vastai create failed for offer {offer_id}: "
                            f"{(out.stderr or out.stdout).strip()}")
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            data = {}
        new_id = data.get("new_contract") or data.get("id")
        if not data.get("success", True) or not new_id:
            raise RentError(f"vastai create for offer {offer_id} returned no instance id: "
                            f"{out.stdout.strip()[:200]} — check 'vastai show instances-v1'")
        return str(new_id)

    def instance_status(self, instance_id: str) -> dict:
        try:
            out = self._run(["vastai", "show", "instance", str(instance_id), "--raw"], 60)
            if out.returncode != 0:
                return {}
            data = json.loads(out.stdout or "{}")
            return data if isinstance(data, dict) else {}
        except (RentError, json.JSONDecodeError):
            return {}

    def destroy_instance(self, instance_id: str) -> None:
        VastCtl().destroy(str(instance_id))

    def ssh_url(self, instance_id: str) -> str:
        out = self._run(["vastai", "ssh-url", str(instance_id)], 60)
        if out.returncode != 0:
            raise RentError(f"vastai ssh-url failed: {out.stderr.strip()}")
        return out.stdout


@dataclass(frozen=True)
class RentConfig:
    gpu: str
    disk_gb: int
    image: str
    reliability: float
    criteria: Criteria
    gb: float                       # GB this rental will download (image + models), prices bandwidth
    pull_deadline_s: float
    pin: str | None = None

    @property
    def query(self) -> str:
        return (f"gpu_name={self.gpu} num_gpus=1 disk_space>={self.disk_gb} "
                f"reliability>{self.reliability} rentable=true")


@dataclass(frozen=True)
class RentResult:
    instance_id: str | None
    chosen: Ranked
    ranked: list
    pull_s: float | None


def _stderr(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def gather_offers(api: VastApi, cfg: RentConfig, board: Scoreboard, log) -> list[dict]:
    offers = list(api.search_offers(cfg.query))
    for machine_id in board.known_good(limit=KNOWN_GOOD_QUERIES):
        try:
            offers += api.search_offers(f"machine_id={machine_id} rentable=true")
        except RentError as exc:
            log(f"machine_id={machine_id} lookup failed: {exc}")
    return dedupe(offers)


def _wait_running(api: VastApi, instance_id: str, deadline_s: float, *, now, sleep, log):
    start = now()
    while True:
        try:
            status = str((api.instance_status(instance_id) or {}).get("actual_status")
                         or "").lower()
        except RuntimeError as exc:
            status = ""
            log(f"status check failed (will retry): {exc}")
        elapsed = now() - start
        if status == "running":
            return "running", elapsed
        if status in FAILED_STATUSES:
            return "failed", elapsed
        if elapsed >= deadline_s:
            return "timeout", elapsed
        sleep(POLL_S)


def _abandon(api: VastApi, instance_id: str, on_released, log, *, best_effort: bool) -> None:
    try:
        api.destroy_instance(instance_id)
    except Exception as exc:
        if best_effort:
            log(f"could not destroy {instance_id} while unwinding: {exc}")
            return
        raise RentError(
            f"instance {instance_id} could NOT be destroyed ({exc}) — it is STILL BILLING. "
            f"Destroy it by hand: vastai destroy instance {instance_id}") from exc
    on_released(instance_id)


def _record(board: Scoreboard, machine_id: int | None, pull_s: float | None, outcome: str,
            when: float) -> None:
    if machine_id is None:
        return
    board.record(MachineRecord(machine_id=machine_id, pull_s=pull_s, model_mbps=None,
                               gb=None, measured_at=when, outcome=outcome))


def rent(api: VastApi, cfg: RentConfig, board: Scoreboard, *, confirm: bool,
         now: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
         log: Callable[[str], None] = _stderr,
         on_created: Callable[[str], None] = lambda iid: None,
         on_released: Callable[[str], None] = lambda iid: None,
         persist: Callable[[], None] = lambda: None) -> RentResult:
    offers = gather_offers(api, cfg, board, log)
    ranked, rejected = rank(offers, cfg.criteria, board, gb=cfg.gb, now=now())
    if cfg.pin:
        ranked = [r for r in ranked if str(r.offer["id"]) == str(cfg.pin)]
        if not ranked:
            raise NoOffers(f"offer {cfg.pin} is not among the qualifying offers "
                           f"({len(offers)} searched)")
    if not ranked:
        raise NoOffers(explain_rejections(rejected, len(offers)))
    if not confirm:
        return RentResult(None, ranked[0], ranked, None)

    pulls = 0
    create_failures = 0
    for cand in ranked:
        if pulls >= 1 + MAX_PULL_RETRIES:
            break
        try:
            instance_id = api.create_instance(cand.offer["id"], image=cfg.image,
                                              disk_gb=cfg.disk_gb, label=VAST_LABEL)
        except RuntimeError as exc:
            create_failures += 1
            log(f"create failed for offer {cand.offer['id']}: {exc}")
            if create_failures >= MAX_CREATE_FAILURES:
                break
            continue
        pulls += 1
        on_created(instance_id)
        try:
            state, elapsed = _wait_running(api, instance_id, cfg.pull_deadline_s,
                                           now=now, sleep=sleep, log=log)
        except BaseException:
            _abandon(api, instance_id, on_released, log, best_effort=True)
            raise
        if state == "running":
            _record(board, cand.machine_id, elapsed, "ok", now())
            persist()
            return RentResult(instance_id, cand, ranked, elapsed)
        _abandon(api, instance_id, on_released, log, best_effort=False)
        _record(board, cand.machine_id, elapsed,
                "slow_pull" if state == "timeout" else "failed", now())
        persist()
        log(f"offer {cand.offer['id']} (machine {cand.machine_id}): {state} after "
            f"{elapsed:.0f}s — destroyed, trying the next machine")
    raise RentError(f"no machine reached 'running' within {cfg.pull_deadline_s:.0f}s "
                    f"after {pulls} pull attempt(s) and {create_failures} failed create(s)")


def parse_ssh_url(text: str) -> tuple[str, str] | None:
    """`vastai ssh-url` prints ssh://root@HOST:PORT (assumed; parsed tolerantly)."""
    for line in reversed([ln.strip() for ln in text.splitlines() if ln.strip()]):
        s = line[len("ssh://"):] if line.startswith("ssh://") else line
        if "@" in s:
            s = s.split("@", 1)[1]
        s = s.split("/")[0]
        host, sep, port = s.rpartition(":")
        if sep and host and port.isdigit() and " " not in host:
            return host, port
    return None


def make_api() -> VastApi:
    return RealVastApi()


def _cfg_get(key: str, default: str) -> str:
    return os.environ.get(key) or env_get(ROOT / ".env", key) or default


def _parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gpu")
    ap.add_argument("--disk", type=int)
    ap.add_argument("--image")
    ap.add_argument("--max-dph", type=float)
    ap.add_argument("--reliability", type=float)
    ap.add_argument("--min-disk-bw", type=float, default=3000.0)
    ap.add_argument("--min-cpu-ghz", type=float, default=2.5)
    ap.add_argument("--min-inet-mbps", type=float,
                    default=float(_cfg_get("VAST_MIN_INET_MBPS", "1000")))
    ap.add_argument("--max-down-usd-per-tb", type=float,
                    default=float(_cfg_get("VAST_MAX_DOWN_USD_PER_TB", "20")))
    ap.add_argument("--gb", type=float, default=float(_cfg_get("VAST_GB", "60")))
    ap.add_argument("--pull-deadline", type=float,
                    default=float(_cfg_get("VAST_PULL_DEADLINE_S", str(DEFAULT_PULL_DEADLINE_S))))
    ap.add_argument("--skip", default="")
    ap.add_argument("--offer", default="")
    ap.add_argument("--confirm", action="store_true")
    ap.add_argument("--ssh-target", metavar="INSTANCE_ID")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    api = make_api()

    if args.ssh_target:
        try:
            target = parse_ssh_url(api.ssh_url(args.ssh_target))
        except RentError as exc:
            _stderr(str(exc))
            return 1
        if target is None:
            return 1
        print(f"{target[0]} {target[1]}")
        return 0

    for required in ("gpu", "disk", "image", "max_dph", "reliability"):
        if getattr(args, required) is None:
            _stderr(f"missing --{required.replace('_', '-')}")
            return 2

    criteria = Criteria(
        max_dph=args.max_dph, min_disk_bw=args.min_disk_bw, min_cpu_ghz=args.min_cpu_ghz,
        min_inet_mbps=args.min_inet_mbps, max_down_usd_per_tb=args.max_down_usd_per_tb,
        skip_offers=frozenset(s.strip() for s in args.skip.split(",") if s.strip()))
    cfg = RentConfig(gpu=args.gpu, disk_gb=args.disk, image=args.image,
                     reliability=args.reliability, criteria=criteria, gb=args.gb,
                     pull_deadline_s=args.pull_deadline, pin=args.offer or None)
    board = load_board(BOARD_PATH)
    env_path = ROOT / ".env"
    _stderr(f"searching: {cfg.query}  (<= ${args.max_dph:.2f}/hr, ~{args.gb:.0f} GB to download)")
    try:
        result = rent(
            api, cfg, board, confirm=args.confirm,
            on_created=lambda iid: env_set(env_path, "GPU_INSTANCE_ID", iid),
            on_released=lambda iid: env_set(env_path, "GPU_INSTANCE_ID", ""),
            persist=lambda: save_board(BOARD_PATH, board))
    except RentError as exc:
        print(f"\033[31m ✗ \033[0m{exc}", file=sys.stderr)
        return 1

    if not args.confirm:
        _stderr(f"\n{len(result.ranked)} qualifying offer(s), best first:\n"
                f"{format_table(result.ranked)}\n\n"
                "Dry run — nothing rented. Read the shortlist, then:\n"
                "  CONFIRM=yes bash scripts/pod-provision.sh")
        print(result.chosen.offer["id"])
        return 0
    _stderr(f"rented — instance {result.instance_id} running after {result.pull_s:.0f}s "
            f"(saved to .env as GPU_INSTANCE_ID). Next: make gpu-wait")
    print(result.instance_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
