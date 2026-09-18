"""RunPod account balance via `runpodctl user`. Read-only — no renting.

Only `clientBalance` is read. `runpodctl user` also returns
`currentSpendPerHr`, but that field is not trusted for cost in this repo
(CLAUDE.md: it was off by 33x against `runpodctl billing pods`), so it is
deliberately never surfaced.
"""
from __future__ import annotations

import json
import subprocess

_TIMEOUT_SEC = 30   # same bound as gpu_stock: one HTTP round trip behind the CLI.


def account_balance() -> float:
    """The account's prepaid balance in USD. Raises RuntimeError on any
    failure — the caller decides how to tell the user, same contract as
    gpu_stock.stock_at.
    """
    try:
        out = subprocess.run(["runpodctl", "user", "-o", "json"],
                             capture_output=True, text=True, timeout=_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not run runpodctl: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(f"runpodctl user failed: {out.stderr.strip()}")
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"runpodctl returned invalid JSON: {exc}") from exc
    balance = data.get("clientBalance") if isinstance(data, dict) else None
    if not isinstance(balance, (int, float)):
        raise RuntimeError("runpodctl user returned no clientBalance")
    return float(balance)
