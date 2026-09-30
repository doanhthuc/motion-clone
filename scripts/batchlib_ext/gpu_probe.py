"""Is the rented GPU actually running at speed? Measured under load, before any batch is paid for.

Why it exists (2026-09-30): Vast machine 140110 booted, pulled its image in 322 s and passed every
check the scoreboard had, yet its RTX 5090 sat in P8 at 210 MHz / ~55 W (max 3090 MHz) with no
throttle reason set. Wan Animate sampled at 157 s/it and one 10 s job had burned 44+ min at
$0.557/h with the drain log printing the same "90%" label for an hour. Nothing in the pipeline
looked at GPU speed, so the only signal was a human noticing.

A probe has to LOAD the GPU: an idle card reports 210 MHz whether it is broken or healthy.
`probe_script` runs a bf16 matmul in the pod's ComfyUI venv and samples nvidia-smi while it runs.

Pure judgement lives in `judge` so it is testable without a pod. It fails OPEN: no usable
samples means "unknown", never "slow", because a probe that cannot run must not destroy a healthy
pod that is already paid for.
"""
from __future__ import annotations

import statistics
import subprocess
from dataclasses import dataclass

# Median SM clock under load must reach this share of the card's max clock. Only the broken host
# has been measured (210/3090 = 0.07); 0.5 is a deliberately wide gate that a healthy card, which
# boosts well above half of max under a matmul, clears easily. TODO(measure): replace with the
# ratio from the first healthy pod's probe -- the drain log prints it.
MIN_CLOCK_RATIO = 0.5
PROBE_TIMEOUT_S = 90

# ComfyUI's venv is the one place torch is guaranteed after bootstrap (setup-pm2.sh:704).
# 4 s of warm-up lets CUDA init and the clocks ramp before sampling.
PROBE_SCRIPT = r"""
PY="$HOME/ComfyUI/venv/bin/python"; [ -x "$PY" ] || PY=python3
"$PY" -c "
import time, torch
a = torch.randn(8192, 8192, device='cuda', dtype=torch.bfloat16)
t = time.time()
while time.time() - t < 12:
    a @ a
    torch.cuda.synchronize()
" >/dev/null 2>&1 &
sleep 4
for i in 1 2 3 4 5 6; do
  nvidia-smi --query-gpu=clocks.sm,clocks.max.sm,power.draw --format=csv,noheader,nounits
  sleep 1
done
wait
"""

# While a job is already running the GPU is loaded by it, so no matmul is needed: just read.
PASSIVE_SCRIPT = r"""
for i in 1 2 3; do
  nvidia-smi --query-gpu=clocks.sm,clocks.max.sm,power.draw --format=csv,noheader,nounits
  sleep 1
done
"""


@dataclass(frozen=True)
class Verdict:
    state: str            # "ok" | "slow" | "unknown"
    ratio: float | None   # median sm / max sm
    detail: str


def judge(output: str) -> Verdict:
    ratios: list[float] = []
    powers: list[float] = []
    for line in output.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            sm, mx, watts = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            continue
        if mx > 0:
            ratios.append(sm / mx)
            powers.append(watts)
    if not ratios:
        return Verdict("unknown", None, "no nvidia-smi samples")
    ratio = statistics.median(ratios)
    detail = f"median SM clock {ratio:.0%} of max, median power {statistics.median(powers):.0f} W"
    return Verdict("ok" if ratio >= MIN_CLOCK_RATIO else "slow", ratio, detail)


def run_probe(host: str, port: str, script: str = PROBE_SCRIPT) -> Verdict:
    """SSH to the pod, load the GPU, judge. Never raises: a failed probe is "unknown"."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20",
             "-p", str(port), f"root@{host}", "bash -s"],
            input=script, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Verdict("unknown", None, f"probe did not run: {exc!r}")
    return judge(result.stdout)
