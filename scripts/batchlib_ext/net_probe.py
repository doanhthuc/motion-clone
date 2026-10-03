"""Can the rented box pull models from HuggingFace fast enough? Measured before bootstrap starts.

Why it exists (2026-10-03): a stateless box — Vast, or RunPod Community, which cannot mount the
Network Volume — downloads the manifest's models (34.4 GB for the Wan Animate group) on every
rent. The host's advertised bandwidth is a filter, not a measurement: on Vast the line-rate model
predicted a 116 s image pull and got 556 s (docs/gpu-pod.md#vast-ghcr). A slow route found only
after bootstrap has already burned minutes is a slow route paid for twice.

The probe reads from the same place preload-models.sh will: the Wan Animate checkpoint on
huggingface.co, four byte ranges in parallel for a fixed window. Four streams because aria2c -x4
was within 2% of -x16 on the one host where both were measured (Bulgaria, 2026-09-19), so four
curls stand in for what preload-models.sh actually gets.

Pure judgement lives in `judge`; like gpu_probe it fails OPEN — no usable sample is "unknown",
never "slow", so a probe that cannot run never destroys a pod that may be fine.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

# 34.4 GB at 100 MB/s is ~6 min of download that runs alongside a ~200 s bootstrap, so below this
# the download, not the install, sets time-to-ready. Vast hosts measured 242-296 MB/s from
# huggingface.co with aria2c, so a healthy host clears this by 2x. An assumption until a RunPod
# Community host is measured — the drain log prints every probe's number.
MIN_MBPS = 100.0
WINDOW_S = 15
STREAMS = 4
PROBE_TIMEOUT_S = WINDOW_S + 45

# The largest file preload-models.sh fetches (catalog-motion-transfer.json id wan-animate-14b),
# so the probe walks the same CDN route the real download will.
PROBE_URL = ("https://huggingface.co/Kijai/WanVideo_comfy_fp8_scaled/resolve/main/Wan22Animate/"
             "Wan2_2-Animate-14B_fp8_e4m3fn_scaled_KJ.safetensors")
# Each stream starts 4 GiB further into the 18.4 GB file, so no two read the same bytes.
RANGE_STEP = 4 << 30


def probe_script(url: str = PROBE_URL) -> str:
    """One `bytes` line per stream: what curl moved inside the window."""
    lines = []
    for i in range(STREAMS):
        start = i * RANGE_STEP
        lines.append(
            f"curl -sL -o /dev/null --max-time {WINDOW_S} -r {start}-{start + RANGE_STEP - 1} "
            f"-w 'bytes %{{size_download}}\\n' '{url}' &")
    return "\n".join(lines) + "\nwait\n"


@dataclass(frozen=True)
class Verdict:
    state: str             # "ok" | "slow" | "unknown"
    mbps: float | None     # MB/s, all streams together
    detail: str


def judge(output: str, window_s: float = WINDOW_S, floor: float = MIN_MBPS) -> Verdict:
    total = 0.0
    samples = 0
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2 or parts[0] != "bytes":
            continue
        try:
            total += float(parts[1])
        except ValueError:
            continue
        samples += 1
    if samples == 0 or total <= 0:
        # Zero bytes from every stream is a DNS/proxy/HF outage as often as a slow host; replacing
        # the pod would not fix it, so it is not grounds to destroy one.
        return Verdict("unknown", None, "no bytes downloaded")
    mbps = total / 1e6 / window_s
    detail = f"{mbps:.0f} MB/s from huggingface.co over {samples} streams (floor {floor:.0f})"
    return Verdict("ok" if mbps >= floor else "slow", mbps, detail)


def run_probe(host: str, port: str, floor: float = MIN_MBPS) -> Verdict:
    """SSH to the box, download for WINDOW_S, judge. Never raises: a failed probe is "unknown"."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20",
             "-p", str(port), f"root@{host}", "bash -s"],
            input=probe_script(), capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Verdict("unknown", None, f"probe did not run: {exc!r}")
    return judge(result.stdout, floor=floor)
