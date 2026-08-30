"""Thin adapter over runpodctl, so the watchdog's logic can be tested with a fake.

Deliberately minimal: list and destroy. Provisioning stays in
scripts/pod-provision.sh, which already carries the POD_MAX_HOURS safety net
and the dry-run gate.
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol

# runpodctl 2.8 reflects a delete asynchronously: `pod delete` returns before the
# pod leaves `pod list`. Makefile:166 already sleeps 3s between the two for that
# reason, and this adapter has to match it or every destroy would look unverified
# on its first check. Measured convention, not a guess: copied from the target
# that has been destroying pods on this repo since 2026-08-04.
DELETE_SETTLE_SEC = 3.0


@dataclass(frozen=True)
class PodInfo:
    pod_id: str
    name: str


class PodControl(Protocol):
    def list_pods(self) -> list[PodInfo]: ...
    def destroy(self, pod_id: str) -> None: ...


class RunpodCtl:
    def list_pods(self) -> list[PodInfo]:
        # `runpodctl pod list -o json`, NOT `runpodctl get pod -o json`. In
        # runpodctl 2.8 the `get pod` subcommand is deprecated and IGNORES -o,
        # printing a tab-separated table — so json.loads raised on every call and
        # tier 3 never ran once. Verified 2026-08-31: `runpodctl pod list -o json`
        # with no pods rented prints `[]`. Same invocation as Makefile:161 and
        # scripts/gpu-preflight.sh:259.
        out = subprocess.run(["runpodctl", "pod", "list", "-o", "json"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            # Returning [] would read as "no pods", and tier 3 would then do
            # nothing — which is the safe direction when we cannot see.
            # Destroying on a failed query would be the unsafe one.
            raise RuntimeError(f"runpodctl pod list failed: {out.stderr.strip()}")
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
        """Ask RunPod to delete the pod. Exit code is NOT proof — see below.

        `runpodctl pod delete`, matching Makefile:161. `runpodctl remove pod` is
        not a subcommand of runpodctl 2.8 at all.

        A non-zero exit raises so the caller keeps its lease and retries, but a
        ZERO exit is deliberately not treated as success either: Makefile:139-142
        records this repo printing "GPU pod destroyed" over an aborted destroy and
        only finding out from the invoice. The confirmation is a re-list, done by
        the caller through this same protocol so a fake can prove it in a test
        (scripts/pod_watchdog.py destroy_verified).
        """
        out = subprocess.run(["runpodctl", "pod", "delete", pod_id],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise RuntimeError(
                f"runpodctl pod delete {pod_id} failed: {out.stderr.strip()}")
        time.sleep(DELETE_SETTLE_SEC)
