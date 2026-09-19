"""What the bot's Vast tab says and whether it may offer a spend button — decided here, with no
Telegram and no network of its own (the quote and the account are passed in).

The tab always renders. Its spend button is hidden, with the concrete reasons written out, when any
of these holds (spec §3.5):
  - a pipeline in the manifest has no measured Vast session yet (VAST_ENABLED_PIPELINES, spec §1:
    one measured session per pipeline family before its button is enabled — as of 2026-09-19 only
    the `motion` stage has ever run on Vast),
  - a stage in the manifest has no model-registry entry (the runtime twin of `make
    check-vast-models`, so a job cannot be sent to a box that never downloaded its models),
  - no Vast machine passes the filters, or the CLI is missing / not logged in,
  - the account credit is below this batch's estimated session cost.

Everything returned as text is HTML for Telegram, with reasons escaped: a reason can carry a
command's stderr.
"""
from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Callable, Iterable

from batchlib.manifest import Manifest
from batchlib.pipelines import PIPELINES, STAGES
from batchlib.runner import _local_tryon_stage
from batchlib.vast_models import STAGE_MODEL_IDS
from batchlib_ext.vast_quote import BOOT_AFTER_RUNNING_S, VastQuote

from .run import MEASURED_STAGE_SEC


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def _unique(items: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def parse_enabled(raw: str | None) -> frozenset[str]:
    """`VAST_ENABLED_PIPELINES=motion-enhance,character-swap` -> the set of pipeline names."""
    return frozenset(part.strip() for part in (raw or "").split(",") if part.strip())


def static_blockers(manifest: Manifest, enabled: frozenset[str]) -> list[str]:
    """Reasons a Vast rental for this manifest is not allowed — no network involved, so the spend
    handler re-runs it at tap time instead of trusting the panel that was drawn minutes earlier."""
    reasons: list[str] = []
    for pipeline in _unique(run.pipeline for run in manifest.runs):
        if pipeline not in enabled:
            reasons.append(
                f"no measured Vast session for {pipeline} yet — add it to "
                "VAST_ENABLED_PIPELINES in .env after the paid check (docs/gpu-pod.md#vast-provider)")
    stages = _unique(stage for run in manifest.runs for stage in PIPELINES.get(run.pipeline, []))
    for stage in stages:
        if stage not in STAGE_MODEL_IDS:
            reasons.append(f"stage {stage} has no Vast model registry entry")
    return reasons


def gpu_seconds(manifest: Manifest) -> float:
    """Seconds of GPU work the manifest will do once the box is up. A try-on that runs locally over
    an API (Phase A) costs no GPU time, so it is left out. Stages with no measurement fall back to
    their timeout ceiling — the same pessimistic fallback tgbot.run.estimate_minutes uses."""
    total = 0.0
    for run in manifest.runs:
        local = _local_tryon_stage(run)
        for stage in PIPELINES.get(run.pipeline, []):
            if stage == local:
                continue
            total += MEASURED_STAGE_SEC.get(stage, STAGES[stage].timeout_min * 60)
    return total


def session_usd(quote: VastQuote, run_s: float) -> float:
    """Estimated total for one session: GPU time from create to teardown, plus bandwidth."""
    billed_s = quote.ready_s + BOOT_AFTER_RUNNING_S + run_s
    return quote.dph * billed_s / 3600.0 + quote.bandwidth_usd


def _credit_check(credit_fn: Callable[[], float],
                  session: float | None) -> tuple[float | None, list[str]]:
    """(credit, reasons). An unreadable account is a reason, never "$0" and never a pass."""
    try:
        credit = credit_fn()
    except RuntimeError as exc:
        return None, [f"could not read your Vast account (is vastai logged in?) — {exc}"]
    if session is not None and credit < session:
        return credit, [f"Vast credit ${credit:.2f} is below this session's estimate "
                        f"${session:.2f}"]
    return credit, []


def spend_blockers(manifest: Manifest, enabled: frozenset[str], *,
                   credit_fn: Callable[[], float], quote: VastQuote | None) -> list[str]:
    """The reasons to refuse a Vast spend at the moment of the tap. A subset of what the panel
    shows: no marketplace search here (a stale button must not stall the bot for a search), so the
    estimate the credit is compared with comes from the last quote, when there is one."""
    reasons = static_blockers(manifest, enabled)
    session = None if quote is None else session_usd(quote, gpu_seconds(manifest))
    return reasons + _credit_check(credit_fn, session)[1]


@dataclass(frozen=True)
class VastView:
    lines: list[str]              # HTML, one per line, for the caller to join
    blockers: list[str]           # plain text; empty means the spend button may be shown
    quote: VastQuote | None
    session_usd: float | None

    @property
    def can_spend(self) -> bool:
        return not self.blockers and self.quote is not None


def build_view(manifest: Manifest, *, gb: float, enabled: frozenset[str],
               quote_fn: Callable[[], VastQuote], credit_fn: Callable[[], float]) -> VastView:
    blockers = static_blockers(manifest, enabled)
    lines = ["<b>Vast.ai</b> — one RTX 5090, rented for this batch only"]
    quote: VastQuote | None = None
    session: float | None = None
    try:
        quote = quote_fn()
    except RuntimeError as exc:
        blockers.append(f"no Vast machine qualifies right now — {exc}")
    if quote is not None:
        run_s = gpu_seconds(manifest)
        session = session_usd(quote, run_s)
        total_min = (quote.ready_s + BOOT_AFTER_RUNNING_S) / 60
        lines += [
            f"{_esc(quote.gpu)} · ${quote.dph:.2f}/h · {_esc(quote.location)}",
            f"Download for this batch ≈ {gb:.0f} GB → bandwidth ${quote.bandwidth_usd:.2f}",
            f"Estimated session ≈ <b>${session:.2f}</b> "
            f"(cold start + {run_s / 60:.0f} min of GPU work, plus bandwidth)",
        ]
        if quote.known:
            lines.append(f"Cold start ≈ {total_min:.0f} min — this machine took "
                         f"{quote.ready_s:.0f} s to start last time")
        else:
            lines.append("Cold start: not measured — typically 5–9 min (2 data points); "
                         f"planned for up to {total_min:.0f} min")
    credit, credit_reasons = _credit_check(credit_fn, session)
    blockers += credit_reasons
    if credit is not None:
        lines.append(f"Vast credit: ${credit:.2f}")
    if blockers:
        lines += ["", "<b>No spend button</b> — why:"] + [f"• {_esc(reason)}" for reason in blockers]
    return VastView(lines=lines, blockers=blockers, quote=quote, session_usd=session)
