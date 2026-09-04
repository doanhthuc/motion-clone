"""The money gate: [Confirm] is the only place `CONFIRM=yes` can be written.

[Run] (a future bot handler, Task 6) renders the manifest and calls
`make batch-validate` — free, no pod. Only when the user taps [Confirm] does
this module invoke `make drain ... CONFIRM=yes`, which reaches
scripts/pod-provision.sh and rents an RTX 5090 at $0.99/hour
(docs/gpu-pod.md). Putting the whole spend decision behind one literal
string, appended in exactly one place, is what makes everything after it
(the drain script's own provision -> bootstrap -> run -> teardown cycle)
safe to leave fully automatic.

`progress_text` never touches the pod. It reads the same on-disk journal
(`batchlib.manifest.load_state` via `state_path_for`) that `batch_status`
already uses, so a rendered progress message stays truthful after
`make gpu-destroy` has already run — the pod is the thing most likely to be
gone by the time someone asks.
"""
from __future__ import annotations

import html
import subprocess
import time
from pathlib import Path

# Relies on the caller having put scripts/ on sys.path (bot.py does this at
# import time; tests do it directly) — same convention as tgbot/job.py,
# which imports batchlib the same way without inserting its own path.
from batchlib.manifest import load_state, state_path_for
from batchlib.pipelines import PIPELINES, STAGES
from batchlib_ext.lease import read_lease

from .job import Job

ROOT = Path(__file__).resolve().parents[2]

# Same path scripts/drain.py:26 writes to (and clear_lease/write_lease use).
# Not imported from drain.py — drain.py itself imports Lease/clear_lease/
# write_lease but never read_lease, so nothing there re-derives this path for
# a reader; it is duplicated here on purpose, pinned to the one writer.
LEASE_PATH = ROOT / "batch" / "pod-lease.json"

# Measured medians from ONE real batch (2026-08-18-2105, RTX 5090, RunPod
# EU-RO-1, $0.99/hr), a 15-second driver at 1088x1920 —
# docs/batch-runner.md section 7. These are what actually happened once, not
# a ceiling and not a guarantee: a different preset, targetRes or fpsInterp
# changes both the runtime and the output size (batch-runner.md section 7's
# own closing note). A stage not in this table (only character-swap, as of
# 2026-08-31) falls back to its STAGES[...].timeout_min, which IS a ceiling
# — so the fallback is intentionally the more pessimistic number, not a
# substitute measurement.
MEASURED_STAGE_SEC = {
    "tryon": 351,
    "motion": 247,
    "enhance": 114,
}

# Popen handles for drains this process itself started, keyed by the
# resolved manifest path. This is process memory, not a journal file: the
# bot is a single long-running `python3 scripts/tgbot/bot.py` process, so
# the handle started by start_drain() and the one checked by
# drain_running() are the same process's dict, no persistence needed across
# a bot restart (a restarted bot has no drain of its own running yet).
_RUNNING: dict[Path, subprocess.Popen] = {}


def estimate_minutes(job: Job) -> int:
    """A minutes estimate to show BEFORE [Confirm], never presented as a promise.

    The caller must put this next to a caveat ("measured once, on one
    batch") — this function only computes the number, it does not word the
    disclaimer, so nothing here can silently drop it.
    """
    stages = PIPELINES[job.pipeline]
    total_sec = sum(MEASURED_STAGE_SEC.get(stage, STAGES[stage].timeout_min * 60)
                    for stage in stages)
    return max(1, round(total_sec / 60))


# Motion INSIDE a progress message still can only come from re-editing it —
# a sticker (the other thing that genuinely animates) cannot be edited at all
# ("message can't be edited"), so it can never be a progress display, and
# that has not changed. What DID change (re-measured 2026-09-04): a
# `<tg-emoji>` custom-emoji entity used to come back stripped on this bot
# (entities:null, plain fallback glyph, no error to catch) because the only
# entitlement path known at the time was buying a username on Fragment. It
# turns out the bot owner's own Telegram Premium subscription is a second,
# far cheaper path — with it active, the entity survives and animates for
# real (see bot.py's ICON_*_CE constants for where that gets used). Re-editing
# measured at 0.48/s, 0.91/s and 2.02/s with zero rejections.
#
# Braille, chosen by the user on 2026-09-01 after watching BOTH options animate
# on their own phone — braille first, then ◐◓◑◒ — not from a screenshot. Worth
# recording because the argument against it was reasonable and lost anyway: on
# a dark background these dots are much fainter than quarter-circles, so a
# rendering check ("is it tofu?") passes them while a legibility check does
# not. The person watching the screen for forty minutes preferred them, and
# that is the measurement that counts here.
# Defined locally rather than imported from tgbot.bot — bot.py imports FROM
# this module, so the reverse import would be circular. Same
# custom_emoji_ids as bot.py's ICON_ASK_CE / ICON_EYES_CE (NewsEmoji#76,
# NewsEmoji#0); see bot.py's own comment for why the entity now survives
# (Premium on the bot owner's account, re-measured 2026-09-04).
_ICON_ASK_CE = '<tg-emoji emoji-id="5341715473882955310">⚙️</tg-emoji>'
_ICON_EYES_CE = '<tg-emoji emoji-id="5210956306952758910">👀</tg-emoji>'
# LoadingEmoji#40 (verified animated against the real pack 2026-09-04) — used
# everywhere this file used to hand-cycle _SPIN/_HOURGLASS characters. It
# spins on its own, client-side, whether or not this bot ever edits the
# message again, which is exactly why it is decoration only now — see
# `_elapsed()` below for what actually proves the process is still alive.
_ICON_LOADING_CE = '<tg-emoji emoji-id="5328089410963513796">💠</tg-emoji>'


def _elapsed(lease) -> str:
    """mm:ss since the pod was provisioned, or "" if there is no lease yet.

    This — not a moving glyph — is what proves the process is still alive.
    An animated custom emoji spins forever once sent, dead process or not
    (2026-09-04 finding: it animates client-side, independent of whether this
    bot ever edits the message again), so the thing the old _SPIN/_HOURGLASS
    cycling actually protected against — a drain that died mid-stage leaving
    the same `running` record as one still working — now has to come from a
    real, still-ticking number instead. mm:ss rather than whole minutes so it
    visibly moves at roughly the cadence tick_progress itself polls at (2s
    while a drain runs), not just once a minute.
    """
    if lease is None:
        return ""
    total = int(time.time() - lease.provisioned_at)
    return f"{total // 60}m{total % 60:02d}s"


def progress_text(manifest_path: Path, *, lease,
                  stages: list[str] | None = None) -> str:
    """Render one progress message from the journal alone. Returns HTML.

    A bar of `done/len(planned)` cells is discrete because the journal is
    discrete, and smoothing it into a percentage would be inventing progress
    the runner never reported.

    `lease` (batchlib_ext.lease.Lease | None) is used only for its own
    fields (pod_id, provisioned_at) already written to disk at provision
    time — never to query RunPod. That is deliberate: the pod is exactly
    the thing that may already be destroyed by the time this renders, while
    the journal (load_state) is written by the runner on every stage
    transition and outlives the pod, same as batch_status already relies on.
    It is also the source of `_elapsed()`, the one number in this message
    that has to be real (see its own docstring for why).

    `stages` is the pipeline's full stage list, captured when the drain starts.
    Without it there is no denominator: the journal records only stages that
    have already begun, so a bar computed from it alone would read 1/1 at the
    first stage and never move.

    HTML (2026-08-31) because this is re-rendered into the same message every
    poll — the caller must send it with parse_mode="HTML", and every
    interpolated value here is escaped for that reason.
    """
    state = load_state(state_path_for(manifest_path))
    batch = state.get("batch") or "(not started yet)"
    # ⚙️, not the 🎬 the control panel opens with (2026-09-01). The two used to
    # be indistinguishable at a glance, which matters most in the one place
    # they sit next to each other: the frozen panel and the progress message
    # are adjacent in the chat for the whole of a render, and they mean
    # different things — what was submitted, versus what is happening.
    lines = [f"{_ICON_ASK_CE} <b>{html.escape(str(batch), quote=False)}</b>"]

    elapsed = _elapsed(lease)
    runs = state.get("runs") or {}
    if not runs:
        # This is the provision + bootstrap window, the longest stretch
        # (~10 min) in which the journal says nothing whatsoever — the one
        # phase where the only real question is whether anything is
        # happening at all, which `elapsed` answers and the journal cannot.
        tail = f" ({elapsed})" if elapsed else ""
        lines.append(f"{_ICON_EYES_CE} waiting for the pod — "
                     f"nothing recorded yet{tail}")
    for run_id in sorted(runs):
        run = runs[run_id]
        seen = run.get("stages") or {}
        # The bar needs a DENOMINATOR the journal cannot give: it only records
        # stages already started, so done/seen would read 1/1 at the first
        # stage and never move. `stages` is captured from the pipeline when the
        # drain starts (bot._start_progress) precisely so this can say 1/3.
        planned = list(stages or seen.keys())
        done = sum(1 for st in seen.values() if st.get("status") == "done")
        current = next((n for n in planned
                        if (seen.get(n) or {}).get("status") == "running"), None)
        if planned:
            filled = "▰" * done + "▱" * max(0, len(planned) - done)
            tail = (f" {_ICON_LOADING_CE} {html.escape(current, quote=False)}"
                    if current else "")
            lines.append(f"{filled} {done}/{len(planned)}{tail}")
        for stage_name in planned:
            stage = seen.get(stage_name) or {}
            status = stage.get("status")
            icon = {"done": "✅", "running": _ICON_LOADING_CE,
                    "error": "❌"}.get(status, "⬜")
            sec = stage.get("sec")
            suffix = f" · {sec}s" if sec is not None else ""
            lines.append(f"{icon} {html.escape(stage_name, quote=False)}{suffix}")
        if run.get("status") == "error":
            lines.append("❌ <b>this run failed</b>")

    if lease is not None:
        mins = (time.time() - lease.provisioned_at) / 60
        # Elapsed, not a prediction: the pod bills from provisioned_at whether
        # or not a stage is moving, so this is the number that costs money.
        lines.append(f"\n⏱ {elapsed} on the pod · 💸 ${mins / 60 * 0.99:.2f} so far")

    return "\n".join(lines)


def start_drain(manifest_path: Path, *, dry_run: bool) -> subprocess.Popen:
    """Launch `make drain FILE=...`, appending CONFIRM=yes only when dry_run is False.

    This is the ONLY line in this module (in this repo) that may write the
    string "CONFIRM=yes" — `grep -rn CONFIRM scripts/tgbot/` must show
    exactly one hit, and it must be inside this `if`. Output goes to a log
    file beside the manifest rather than a pipe: a drain can run for the
    lifetime of a rented pod (hours), and a Popen pipe that nobody reads
    fills its OS buffer and deadlocks the child.
    """
    argv = ["make", "drain", f"FILE={manifest_path}"]
    if not dry_run:
        argv.append("CONFIRM=yes")

    log_path = manifest_path.with_suffix(".drain.log")
    with open(log_path, "ab") as log_file:
        # Popen duplicates the fd into the child; closing our copy on exit
        # of this `with` is what lets the child (which can outlive this
        # function by hours) keep writing without this process holding a
        # second handle open for as long as the bot itself runs.
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT)
    _RUNNING[manifest_path.resolve()] = proc
    return proc


def lease_for(manifest_path: Path):
    """The on-disk lease if it names THIS manifest, else None.

    Read at call time, never cached: `drain.py` writes the lease from a
    separate process and `teardown()` clears it, so any value this module
    held would be stale the moment the drain moved on. Matching on the
    manifest is what keeps one chat's pod out of another chat's progress
    message — the lease file is global to the VPS, the manifest is not.
    """
    lease = read_lease(LEASE_PATH)
    if lease is None:
        return None
    # drain.py:239 writes manifest=str(manifest_path.resolve()) at provision
    # time — resolve ours the same way rather than comparing raw strings, so
    # a relative vs. absolute spelling of the same file cannot cause a false
    # "not running".
    return lease if Path(lease.manifest).resolve() == manifest_path.resolve() else None


def drain_running(manifest_path: Path) -> bool:
    """True if THIS process still has the drain alive, OR a lease says it might be.

    Neither half alone is enough. The Popen check alone (what this used to
    be) is blind to a bot restart: scripts/vps/motion-bot.service sets
    `Restart=always`, so the bot process is replaced and a fresh interpreter
    starts with an empty `_RUNNING` — and if systemd's default
    KillMode=control-group took the `make drain` child down with the old
    process, that child was SIGKILLed, which skips drain.py's `finally:
    teardown()` entirely. The lease scripts/drain.py:26 writes right after
    provisioning is the one thing that survives both: it is a file, not
    process memory. A dry run (no CONFIRM=yes) never writes a lease, which
    is why the Popen check is still needed for that case, and why this is an
    OR rather than a lease-only check.

    Without this, a restarted bot would answer False for a manifest whose
    pod is still live or being drained, and a second [Confirm] tap would
    launch a second `make drain ... CONFIRM=yes` on the same manifest — two
    runners writing one state.json corrupts the journal, and two jobs on one
    GPU breaks the exclusive-use assumption run_enhance's comfy_recycle
    depends on (docs/batch-runner.md).
    """
    proc = _RUNNING.get(manifest_path.resolve())
    if proc is not None and proc.poll() is None:
        return True

    return lease_for(manifest_path) is not None


def final_files(batch_dir: Path) -> list[Path]:
    """The finished video(s) for a batch — `_final/*.mp4` only, nothing from `runs/`.

    `runs/<run>/NN-stage.mp4` holds an intermediate for every stage of the
    pipeline (e.g. `02-motion.mp4` before `enhance` runs); scripts/batchlib
    only promotes a run's LAST stage output into `_final/<run>.mp4`
    (runner.py's `_finalize`). Shipping an intermediate to the user as if it
    were the finished result would be worse than shipping nothing — they
    would have no way to tell it apart from the real output.
    """
    final_dir = batch_dir / "_final"
    if not final_dir.is_dir():
        return []
    return sorted(final_dir.glob("*.mp4"))


def summary_text(batch_dir: Path) -> str:
    """One text summary of a batch: per-stage seconds/bytes, and which runs failed.

    Reads only `_index.tsv` (runner.py's `write_index` — one row per STAGE,
    run/status/stage/job_id/elapsed_sec/bytes/params) and `runs/<run>/` on
    disk. A failed run is recognised the same way `_index.tsv` already can
    show it (a run whose last stage row has status "error"), and by the
    presence of `pod-job.log`/`run.log` under `runs/<run>/` — files
    `scripts/drain.py`'s `teardown()` already pulled down (or wrote, for
    run.log) before the pod was destroyed. Never touches the pod itself.
    """
    lines = [f"batch {batch_dir.name}"]

    index_path = batch_dir / "_index.tsv"
    if index_path.exists():
        rows = index_path.read_text(encoding="utf-8").splitlines()
        header, data_rows = (rows[0].split("\t"), rows[1:]) if rows else ([], [])
        for row in data_rows:
            if not row:
                continue
            record = dict(zip(header, row.split("\t")))
            lines.append(f"{record.get('run', '?')} {record.get('stage', '?')}: "
                         f"{record.get('status', '?')} "
                         f"{record.get('elapsed_sec', '?')}s "
                         f"{record.get('bytes', '?')}B")
    else:
        lines.append("no _index.tsv yet")

    runs_dir = batch_dir / "runs"
    if runs_dir.is_dir():
        for run_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
            if (run_dir / "pod-job.log").exists():
                lines.append(f"{run_dir.name}: failed — pod-job.log and run.log attached")

    return "\n".join(lines)
