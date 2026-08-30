"""Thin adapter over runpodctl, so the watchdog's logic can be tested with a fake.

Deliberately minimal: list and destroy. Provisioning stays in
scripts/pod-provision.sh, which already carries the POD_MAX_HOURS safety net
and the dry-run gate.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PodInfo:
    pod_id: str
    name: str


class PodControl(Protocol):
    def list_pods(self) -> list[PodInfo]: ...
    def destroy(self, pod_id: str) -> None: ...


class RunpodCtl:
    def list_pods(self) -> list[PodInfo]:
        out = subprocess.run(["runpodctl", "get", "pod", "-o", "json"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            # Returning [] would read as "no pods", and tier 3 would then do
            # nothing — which is the safe direction when we cannot see.
            # Destroying on a failed query would be the unsafe one.
            raise RuntimeError(f"runpodctl get pod failed: {out.stderr.strip()}")
        try:
            data = json.loads(out.stdout or "[]")
            return [PodInfo(pod_id=str(p["id"]), name=str(p.get("name", "")))
                    for p in data]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            # runpodctl may exit 0 but return malformed output (e.g., unauthenticated
            # CLI). Normalize all parse failures to RuntimeError so tick() can catch
            # "cannot list pods" uniformly. Include a snippet of the offending output.
            snippet = out.stdout[:100] if out.stdout else "(empty)"
            raise RuntimeError(f"runpodctl returned invalid JSON: {exc} — output: {snippet}") from exc

    def destroy(self, pod_id: str) -> None:
        subprocess.run(["runpodctl", "remove", "pod", pod_id],
                       check=True, timeout=120)
