"""Vast account credit via `vastai show user --raw`. Read-only — no renting.

Only `credit` is read: it is the prepaid balance in USD (a live account showed credit 10.70 next to
`balance` 0 on 2026-09-19, and docs/gpu-pod.md quotes the same field). Same contract as
runpod_account.account_balance: any failure raises RuntimeError and the caller decides what to tell
the user — an unreadable account must read as "cannot check", never as "$0".
"""
from __future__ import annotations

import json
import subprocess

_TIMEOUT_SEC = 30   # one HTTPS round trip behind the CLI, the same bound as runpod_account.


def account_credit() -> float:
    try:
        out = subprocess.run(["vastai", "show", "user", "--raw"],
                             capture_output=True, text=True, timeout=_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not run vastai: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(f"vastai show user failed: {(out.stderr or out.stdout).strip()[:200]}")
    try:
        data = json.loads(out.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"vastai returned invalid JSON: {exc}") from exc
    credit = data.get("credit") if isinstance(data, dict) else None
    if isinstance(credit, bool) or not isinstance(credit, (int, float)):
        raise RuntimeError("vastai show user returned no credit — is the API key set?")
    return float(credit)
