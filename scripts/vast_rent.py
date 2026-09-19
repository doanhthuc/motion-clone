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
VERIFY_POLLS = 3                         # Vast destroys asynchronously: re-list a few times
VERIFY_SLEEP_S = 3
FAILED_STATUSES = frozenset({"exited", "error", "offline", "unknown_error"})
KNOWN_GOOD_QUERIES = 3


class RentError(RuntimeError):
    pass


class NoOffers(RentError):
    pass


class AmbiguousCreate(RentError):
    """A create whose outcome is unknown: an instance may exist. Never create another one."""


class _CommandTimeout(RentError):
    pass


class VastApi(Protocol):
    def search_offers(self, query: str) -> list[dict]: ...
    def create_instance(self, offer_id, *, image: str, disk_gb: int, label: str) -> str: ...
    def instance_status(self, instance_id: str) -> dict: ...
    def destroy_instance(self, instance_id: str) -> None: ...
    def instance_exists(self, instance_id: str) -> bool: ...
    def ssh_url(self, instance_id: str) -> str: ...


class RealVastApi:
    def _run(self, argv: list[str], timeout: int):
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise _CommandTimeout(f"{argv[0]} timed out after {timeout}s") from exc
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
        def ambiguous(detail: str) -> AmbiguousCreate:
            return AmbiguousCreate(
                f"vastai create for offer {offer_id} gave no clear answer ({detail}) — an "
                f"instance labelled {label} may exist. Check `vastai show instances-v1`; the "
                f"watchdog's tier 3 reaps an unleased labelled instance after ~10 minutes.")

        try:
            out = self._run(["vastai", "create", "instance", str(offer_id), "--image", image,
                             "--disk", str(disk_gb), "--ssh", "--direct", "--label", label,
                             "--cancel-unavail", "--raw"], 120)
        except _CommandTimeout as exc:
            raise ambiguous(str(exc)) from exc
        if out.returncode != 0:
            raise RentError(f"vastai create failed for offer {offer_id}: "
                            f"{(out.stderr or out.stdout).strip()}")
        # From here the exit code says the request was accepted: anything we cannot read is
        # AMBIGUOUS, not "no instance" — creating another one could leave two billing.
        try:
            data = json.loads(out.stdout)
        except json.JSONDecodeError:
            raise ambiguous(f"unreadable reply: {out.stdout.strip()[:120]!r}") from None
        if not isinstance(data, dict):
            raise ambiguous(f"unexpected reply: {out.stdout.strip()[:120]!r}")
        if "success" in data and not data["success"]:
            raise RentError(f"vastai create failed for offer {offer_id}: "
                            f"{out.stdout.strip()[:200]}")
        new_id = data.get("new_contract") or data.get("id")
        if not new_id:
            raise ambiguous(f"no instance id in the reply: {out.stdout.strip()[:120]!r}")
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

    def instance_exists(self, instance_id: str) -> bool:
        try:
            pods = VastCtl().list_pods()
        except RuntimeError as exc:
            raise RentError(f"could not verify whether instance {instance_id} still exists: "
                            f"{exc}") from exc
        return any(p.pod_id == str(instance_id) for p in pods)

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
    def filters(self) -> str:
        return (f"gpu_name={self.gpu} num_gpus=1 disk_space>={self.disk_gb} "
                f"reliability>{self.reliability} rentable=true")

    @property
    def query(self) -> str:
        return self.filters

    def machine_query(self, machine_id: int) -> str:
        # Same filters as the base search: rank() does not re-check GPU model, GPU count, disk
        # or reliability, so a known-good host's other-GPU or multi-GPU offer would otherwise
        # rank first and be rented.
        return f"machine_id={machine_id} {self.filters}"


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
            offers += api.search_offers(cfg.machine_query(machine_id))
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


def _safe(fn: Callable[[], None], what: str, log) -> None:
    """Run advisory bookkeeping: a failure is logged, never allowed to abort a rent."""
    try:
        fn()
    except Exception as exc:
        log(f"could not update {what}: {exc}")


def _still_listed(api: VastApi, instance_id: str, polls: int, sleep) -> str | None:
    """None once the instance is gone; otherwise why we cannot say it is."""
    why = "unknown"
    for n in range(polls):
        try:
            if not api.instance_exists(instance_id):
                return None
            why = "it is still listed after the destroy"
        except Exception as exc:
            why = f"it could not be verified gone ({exc})"
        if n < polls - 1:
            sleep(VERIFY_SLEEP_S)
    return why


def _abandon(api: VastApi, instance_id: str, on_released, log, sleep, *,
             best_effort: bool) -> None:
    """Destroy `instance_id` and PROVE it is gone by re-listing; an exit code proves nothing.

    best_effort=True is the unwinding path (an exception is already in flight): check once and
    never raise, but say STILL BILLING loudly. Otherwise a failure raises, so the caller stops
    renting instead of stacking a second instance on a first that still bills.
    """
    destroy_err = None
    try:
        api.destroy_instance(instance_id)
    except Exception as exc:
        destroy_err = exc
    problem = _still_listed(api, instance_id, 1 if best_effort else VERIFY_POLLS, sleep)
    if problem is None:
        _safe(lambda: on_released(instance_id),
              f"GPU_INSTANCE_ID after destroying {instance_id} (.env may hold a stale id)", log)
        return
    why = f"the destroy failed ({destroy_err}) and {problem}" if destroy_err else problem
    msg = (f"instance {instance_id}: {why} — it is STILL BILLING. "
           f"Destroy it by hand: vastai destroy instance {instance_id}")
    if best_effort:
        log(msg)
        return
    raise RentError(msg) from destroy_err


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
    bad_machines: set = set()        # one machine can carry several offers: don't retry a failure
    for cand in ranked:
        if pulls >= 1 + MAX_PULL_RETRIES:
            break
        if cand.machine_id is not None and cand.machine_id in bad_machines:
            continue
        try:
            instance_id = api.create_instance(cand.offer["id"], image=cfg.image,
                                              disk_gb=cfg.disk_gb, label=VAST_LABEL)
        except AmbiguousCreate:
            raise                    # an instance may exist: stop, never create a second one
        except RuntimeError as exc:
            create_failures += 1
            log(f"create failed for offer {cand.offer['id']}: {exc}")
            if create_failures >= MAX_CREATE_FAILURES:
                break
            continue
        # Say the id at once (stderr), before any bookkeeping that could fail.
        log(f"created instance {instance_id} from offer {cand.offer['id']} "
            f"(machine {cand.machine_id})")
        pulls += 1
        try:
            # First inside the try: if recording the id fails or Ctrl-C lands here, the
            # instance we just created must still be destroyed.
            on_created(instance_id)
            state, elapsed = _wait_running(api, instance_id, cfg.pull_deadline_s,
                                           now=now, sleep=sleep, log=log)
        except BaseException:
            _abandon(api, instance_id, on_released, log, sleep, best_effort=True)
            raise
        if state == "running":
            _record(board, cand.machine_id, elapsed, "ok", now())
            _safe(persist, "the machine scoreboard", log)
            return RentResult(instance_id, cand, ranked, elapsed)
        _abandon(api, instance_id, on_released, log, sleep, best_effort=False)
        if cand.machine_id is not None:
            bad_machines.add(cand.machine_id)
        _record(board, cand.machine_id, elapsed,
                "slow_pull" if state == "timeout" else "failed", now())
        _safe(persist, "the machine scoreboard", log)
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
