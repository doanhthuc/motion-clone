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
import sys
import time
from pathlib import Path

# Relies on the caller having put scripts/ on sys.path (bot.py does this at
# import time; tests do it directly) — same convention as tgbot/job.py,
# which imports batchlib the same way without inserting its own path.
from batchlib.manifest import ManifestError, load_manifest, load_state, state_path_for
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

# Popen handles for Phase A runs (local try-on, no pod). Kept SEPARATE from
# _RUNNING: drain_running() being True makes bot._do_confirm route the job
# into the mailbox so chain_or_teardown picks it up on a pod already paid for,
# and Phase A has no pod — sharing the dict would queue a job into its own
# mailbox. busy() is the union, for the guards that exist because drain.py
# READS the manifest file rather than because a pod is billed.
_PHASE_A: dict[Path, subprocess.Popen] = {}
# Exit codes collected from finished Phase A processes, keyed the same way.
# Kept after the handle is reaped so the tick that acts on the code cannot
# race itself between two polls, and so a code is never lost by being read
# twice.
_PHASE_A_RC: dict[Path, int] = {}


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
# Same id as bot.py's ICON_EMPTY — an unstarted stage here is the same "not
# filled in yet" state as an empty slot on the confirmation panel, and the two
# messages sit next to each other in the chat, so they should read the same
# (2026-09-12, reported live: the plain "⬜" here didn't match the panel).
_ICON_EMPTY_CE = '<tg-emoji emoji-id="5884089033558070257">⬜️</tg-emoji>'
# Same ids as bot.py's ICON_OK_CE / ICON_ERROR_CE — a finished/failed stage
# here is the same state as a filled/warning slot on the confirmation panel,
# so it should animate the same way instead of falling back to a flat glyph
# (2026-09-15, reported live: done/error rows here were the only ones left
# static while running/empty already animated).
_ICON_OK_CE = '<tg-emoji emoji-id="5980930633298350051">✅</tg-emoji>'
_ICON_ERROR_CE = '<tg-emoji emoji-id="5210952531676504517">❌</tg-emoji>'


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
                  stages: list[str] | None = None,
                  phase: str | None = None,
                  provider: str | None = None,
                  usd_per_hr: float | None = None) -> str:
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

    `stages` is a fallback denominator only, used when a run's own pipeline
    can't be recovered from the manifest (deleted mid-render, or corrupt).
    Per run, the real stage list comes from the manifest's own
    `Run.pipeline` via `PIPELINES` — a batch mixing e.g. character-swap-enhance
    with tryon-camera-motion-enhance must not show one job's checklist padded
    with the other's stages (2026-09-12: reported live, a character-swap-enhance
    run was rendered with camera-tryon/camera-motion boxes it would never run).
    Without any fallback there is no denominator at all: the journal records
    only stages that have already begun, so a bar computed from it alone would
    read 1/1 at the first stage and never move.

    `phase` is "local" while Phase A (the API try-on) is running and None
    otherwise. It exists because the no-lease case used to mean exactly one
    thing — "the pod is being provisioned" — and Phase A broke that: there is
    no pod yet and none is coming until the user says so. Inferring a phase
    from the absence of a lease is how the message came to say "waiting for
    the pod" about a step that deliberately runs before any pod exists.

    `provider` and `usd_per_hr` exist for Vast, whose price is not the flat $0.99 a RunPod 5090
    costs: the lease's own provider wins when there is one, and the dollar figure is the rate
    QUOTED when the user tapped spend, printed as an estimate ("≈") because the offer actually
    rented can differ from the quote. With no rate known a Vast message shows time only — never
    the RunPod figure. RunPod (or nothing) renders exactly as it always has.

    HTML (2026-08-31) because this is re-rendered into the same message every
    poll — the caller must send it with parse_mode="HTML", and every
    interpolated value here is escaped for that reason.
    """
    on_vast = ((lease.provider if lease is not None else provider) or "runpod") == "vast"
    state = load_state(state_path_for(manifest_path))
    batch = state.get("batch") or "(not started yet)"
    try:
        pipeline_by_run = {r.id: r.pipeline for r in load_manifest(manifest_path).runs}
    except (ManifestError, OSError):
        pipeline_by_run = {}
    # ⚙️, not the 🎬 the control panel opens with (2026-09-01). The two used to
    # be indistinguishable at a glance, which matters most in the one place
    # they sit next to each other: the frozen panel and the progress message
    # are adjacent in the chat for the whole of a render, and they mean
    # different things — what was submitted, versus what is happening.
    lines = [f"{_ICON_ASK_CE} <b>{html.escape(str(batch), quote=False)}</b>"]

    elapsed = _elapsed(lease)
    runs = state.get("runs") or {}
    # Only this manifest's runs. The journal is one per chat and can still
    # hold a finished batch's runs — the panel listed two of them above the
    # four actually running (reported live 2026-09-18). bot's
    # _journal_is_resumable stops new batches inheriting them; this keeps a
    # journal that already did from showing them.
    if pipeline_by_run:
        runs = {k: v for k, v in runs.items() if k in pipeline_by_run}
    if not runs:
        if phase == "local":
            lines.append(f"{_ICON_EYES_CE} running the try-on over the API — "
                         "no pod rented yet")
        else:
            # This is the provision + bootstrap window, the longest stretch
            # (~10 min) in which the journal says nothing whatsoever — the one
            # phase where the only real question is whether anything is
            # happening at all, which `elapsed` answers and the journal cannot.
            tail = f" ({elapsed})" if elapsed else ""
            where = "Vast.ai" if on_vast else "the pod"
            lines.append(f"{_ICON_EYES_CE} waiting for {where} — "
                         f"nothing recorded yet{tail}")
    for run_id in sorted(runs):
        run = runs[run_id]
        seen = run.get("stages") or {}
        # The bar needs a DENOMINATOR the journal cannot give: it only records
        # stages already started, so done/seen would read 1/1 at the first
        # stage and never move. Each run's OWN pipeline (from the manifest)
        # gives the right denominator and checklist for THAT run; `stages`
        # (captured batch-wide when the drain starts, bot._start_progress)
        # is only a fallback for when the manifest can't be read.
        run_pipeline = pipeline_by_run.get(run_id)
        if run_pipeline in PIPELINES:
            planned = list(PIPELINES[run_pipeline])
        else:
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
            icon = {"done": _ICON_OK_CE, "running": _ICON_LOADING_CE,
                    "error": _ICON_ERROR_CE}.get(status, _ICON_EMPTY_CE)
            sec = stage.get("sec")
            suffix = f" · {sec}s" if sec is not None else ""
            lines.append(f"{icon} {html.escape(stage_name, quote=False)}{suffix}")
        if run.get("status") == "error":
            lines.append(f"{_ICON_ERROR_CE} <b>this run failed</b>")

    if lease is not None:
        mins = (time.time() - lease.provisioned_at) / 60
        # Elapsed, not a prediction: the pod bills from provisioned_at whether
        # or not a stage is moving, so this is the number that costs money.
        if on_vast:
            cost = (f" · 💸 ≈${mins / 60 * usd_per_hr:.2f} so far (quoted ${usd_per_hr:.2f}/h)"
                    if usd_per_hr else "")
            lines.append(f"\n⏱ {elapsed} on Vast.ai{cost}")
        else:
            lines.append(f"\n⏱ {elapsed} on the pod · 💸 ${mins / 60 * 0.99:.2f} so far")

    return "\n".join(lines)


def start_drain(manifest_path: Path, *, dry_run: bool,
                resume: bool = False, force_local: bool = False,
                gpu_provider: str | None = None) -> subprocess.Popen:
    """Launch `make drain FILE=...`, appending CONFIRM=yes only when dry_run is False.

    This is the ONLY line in this module (in this repo) that may write the
    string "CONFIRM=yes" — `grep -rn CONFIRM scripts/tgbot/` must show
    exactly one hit, and it must be inside this `if`. Output goes to a log
    file beside the manifest rather than a pipe: a drain can run for the
    lifetime of a rented pod (hours), and a Popen pipe that nobody reads
    fills its OS buffer and deadlocks the child.

    `resume` forwards RESUME=1 to `make drain` (drain.py's own --resume,
    Makefile:84) — used by bot.py's _do_resume to continue a manifest whose
    local try-on phase already ran and was journalled, so batch_run.py skips
    it instead of re-running (and re-billing Gemini for) it.

    `force_local` forwards FORCE_LOCAL=1 (drain.py's --force-local) for the
    other half of the bot's reuse-or-rerun chooser: the user looked at a
    finished try-on and asked for a different one. It is placed before the
    dry_run gate deliberately — the gate must stay the last thing appended so
    that "CONFIRM=yes appears iff dry_run is False" remains readable as a
    single trailing condition.

    `gpu_provider` forwards PROVIDER=vast|runpod (Makefile:119, drain.py's --provider) so the cloud
    follows THIS run and never .env: the bot must not rewrite .env for a per-batch choice, because a
    bot that dies mid-run would leave the other provider's value behind. None appends nothing, and
    the drain then uses .env's GPU_PROVIDER exactly as before. It sits before the dry_run gate for
    the same reason force_local does.
    """
    if gpu_provider is not None and gpu_provider not in ("runpod", "vast"):
        raise ValueError(f"unknown gpu_provider {gpu_provider!r}")
    argv = ["make", "drain", f"FILE={manifest_path}"]
    if resume:
        argv.append("RESUME=1")
    if force_local:
        argv.append("FORCE_LOCAL=1")
    if gpu_provider is not None:
        argv.append(f"PROVIDER={gpu_provider}")
    if not dry_run:
        argv.append("CONFIRM=yes")

    log_path = manifest_path.with_suffix(".drain.log")
    with open(log_path, "ab") as log_file:
        # Popen duplicates the fd into the child; closing our copy on exit
        # of this `with` is what lets the child (which can outlive this
        # function by hours) keep writing without this process holding a
        # second handle open for as long as the bot itself runs.
        #
        # start_new_session=True (F2/C2, 2026-09-19): puts `make`, drain.py and whatever it
        # spawns (vast_rent.py) in their OWN process group/session, so bot.py's _do_kill can
        # reach all of them with one os.killpg call. Without it, a bare SIGTERM to this single
        # Popen does not run drain.py's `finally: teardown()` and leaves vast_rent.py orphaned —
        # it kept renting up to MAX_PULL_RETRIES more instances after /kill had already told the
        # user the pod was destroyed.
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT,
                                start_new_session=True)
    _RUNNING[manifest_path.resolve()] = proc
    return proc


def start_phase_a(manifest_path: Path, *, resume: bool = False,
                  force_local: bool = False) -> subprocess.Popen:
    """Launch the local try-on phase only. NEVER appends CONFIRM=yes.

    That is the whole point of this function existing beside start_drain
    rather than inside it: `grep -rn CONFIRM scripts/tgbot/` must keep showing
    exactly one executable hit, in start_drain's dry_run gate, and this is a
    second subprocess launcher that must not become a second one. Phase A
    spends Gemini quota and writes the journal; it cannot rent anything,
    because drain.py's --phase-a-only returns before provision() — and before
    the --yes gate, which is why no --yes belongs in this argv either
    (drain.py:313-343).

    Same log-file-not-a-pipe shape as start_drain, for the same reason: a
    Popen pipe nobody reads fills its OS buffer and deadlocks the child.
    Phase A is minutes rather than hours, but a 12-run batch of Gemini calls
    is enough output to matter.

    drain.py directly, not `make drain PHASE_A=1` like start_drain: the caller
    dispatches on the exit code, and GNU make exits 2 for ANY failing recipe.
    Through make, drain.py's EXIT_NEEDS_POD (3) arrived as 2, so every
    finished try-on was reported as "failed (exit 2)" (2026-09-18, batch
    2026-09-16-1706) and the rent panel never appeared.
    """
    argv = [sys.executable, str(ROOT / "scripts" / "drain.py"),
            "--file", str(manifest_path), "--phase-a-only"]
    if resume:
        argv.append("--resume")
    if force_local:
        argv.append("--force-local")

    log_path = manifest_path.with_suffix(".phase-a.log")
    with open(log_path, "ab") as log_file:
        proc = subprocess.Popen(argv, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT)
    key = manifest_path.resolve()
    _PHASE_A[key] = proc
    _PHASE_A_RC.pop(key, None)   # a fresh run invalidates the last one's code
    return proc


def stop_phase_a(manifest_path: Path) -> bool:
    """Terminate a live Phase A child. True if one was running and got stopped.

    Exists because moving Phase A out of the drain's Popen removed the only brake
    on it. Before that change /kill reached Phase A through _RUNNING — _do_kill
    terminates that handle (bot.py:4169-4176) — and after it, nothing does:
    _ask_kill gates on drain_running, which is False during Phase A, so the bot
    answers "there is no pod to kill" while a 12-run batch of hosted try-on calls
    keeps spending. /kill is about forfeiting a PAID pod and Phase A has no pod,
    so the answer is not to widen /kill's meaning but to give Phase A its own
    stop path.

    SIGTERM then SIGKILL after a short wait, matching _do_kill's shape. Unlike a
    drain there is no teardown afterwards: Phase A rents nothing, so there is no
    pod to destroy and no lease to clear.

    The handle is deliberately LEFT in _PHASE_A rather than popped, so
    phase_a_exit still answers with the negative signal code and the tick that
    reports it stays idempotent. Task 9's failure branch is what the user sees.
    """
    key = manifest_path.resolve()
    proc = _PHASE_A.get(key)
    # An already-exited child is not a stop, and saying so is the whole
    # contract: the return value is how Task 10's caller picks its message, so
    # answering True for a child that finished by itself would tell the user
    # they halted a run that had already completed — including one that
    # succeeded, which is the version of this lie that costs trust.
    if proc is None or proc.poll() is not None:
        return False
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return True


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


def phase_a_running(manifest_path: Path) -> bool:
    """True while THIS process has a Phase A child alive.

    Popen-only, no lease fallback the way drain_running has: a lease is
    written at provision time and Phase A never provisions, so there is
    nothing on disk to recover from. The consequence is deliberate and
    acceptable — a restarted bot loses track of an in-flight Phase A. It
    rents nothing, so the worst case is that the user taps Run again, and
    with resume=True the finished try-ons are skipped rather than re-billed.

    "Worst case" there is money, and it understates the real one: losing the
    handle also makes busy() False, and busy() is what _clear_job and
    _wipe_chat gate on — so a bot restarted with a Phase A still in flight
    lets /clear reach its shutil.rmtree(batch/tg-staging/<chat>/) and delete
    the files the orphaned child is reading. That is precisely the harm those
    two guards exist to prevent. It stays acceptable because nothing is
    billed, the window is one restart inside a minutes-long phase, and the
    child's resulting failure is recoverable with resume=True — not a licence
    to widen the window (a longer phase, or another guard moved off busy)
    without closing the blindness at the same time.
    """
    proc = _PHASE_A.get(manifest_path.resolve())
    return proc is not None and proc.poll() is None


def phase_a_exit(manifest_path: Path) -> int | None:
    """The finished Phase A's exit code, or None if it is still running.

    Recorded once and kept, so the tick that acts on it is idempotent: a
    second poll after the handle is reaped still answers, and answering None
    there would strand the chat with a progress message and no panel.
    """
    key = manifest_path.resolve()
    proc = _PHASE_A.get(key)
    if proc is None:
        return _PHASE_A_RC.get(key)
    rc = proc.poll()
    if rc is None:
        return None
    _PHASE_A_RC[key] = rc
    return rc


def busy(manifest_path: Path) -> bool:
    """Anything holding this manifest — a billed drain OR an unpaid Phase A.

    Use this for the guards that exist because drain.py READS the manifest
    file: overwriting a file a running child is about to re-read corrupts its
    input. /clear and /wipe are exactly that and use it whole.
    _render_and_validate only uses it for the mailbox-occupied half — a Phase A
    there is an unconditional refusal (nothing to queue into) and a drain there
    is a redirect into the mailbox, so the two halves of the `or` below need
    different answers and it tests them separately.

    Keep using drain_running() for the guards that exist because a POD IS
    BILLED — conflating them would let an unpaid try-on phase block a kill.
    """
    return drain_running(manifest_path) or phase_a_running(manifest_path)


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
