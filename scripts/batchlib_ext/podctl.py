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
# pod leaves `pod list`. Makefile:160 already sleeps 3s between the two for that
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
        # scripts/gpu-preflight.sh:290.
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

        `runpodctl pod delete`, matching Makefile:159. `runpodctl remove pod` is
        a live deprecated alias for the same thing, not a dead command — measured
        2026-08-31, `runpodctl remove pod --help` exits 0. It was the LIST call
        that was broken, not this one; preferring the modern spelling here is
        hygiene, not a bug fix.

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


class VastCtl:
    """The same list/destroy contract as RunpodCtl, over the vastai CLI.

    Instances are identified to the watchdog by their `label`: pod-provision.sh creates every
    one with `--label motion-transfer`, which is exactly the name
    batchlib_ext.watchdog.DESTROYABLE_NAMES lets tier 3 destroy. An unlabelled instance is not
    ours to kill.

    The JSON shape was checked against `vastai show instances-v1 --raw` (CLI 1.3.0,
    2026-09-19) for the empty case only: `{"instances": [], "next_token": null, ...}`. That an
    instance carries `id` and `label` is taken from the CLI's own column list and is confirmed
    on the first real rental.
    """

    def list_pods(self) -> list[PodInfo]:
        try:
            out = subprocess.run(["vastai", "show", "instances-v1", "--raw", "--all"],
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            # OSError covers a vastai that is not installed (likely on the VPS); tick() catches
            # only RuntimeError, so anything else would take the RunPod scan down with it.
            raise RuntimeError(f"could not run vastai: {exc}") from exc
        if out.returncode != 0:
            raise RuntimeError(f"vastai show instances-v1 failed: {out.stderr.strip()}")
        try:
            data = json.loads(out.stdout or '{"instances": []}')
            if data.get("next_token"):
                # F4/I2: `--all` is supposed to fetch every page in one call, but if the CLI
                # ever paginates anyway, silently returning only the first page could read a
                # live instance as gone. Every caller (rent()'s instance_exists, the watchdog)
                # already treats a raising listing as "unverifiable" — fail closed instead.
                raise RuntimeError(
                    "vastai listing may be incomplete (next_token is set)")
            return [PodInfo(pod_id=str(i["id"]), name=str(i.get("label") or ""))
                    for i in data["instances"]]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            snippet = out.stdout[:100] if out.stdout else "(empty)"
            raise RuntimeError(
                f"vastai returned invalid JSON: {exc} — output: {snippet}") from exc

    def destroy(self, pod_id: str) -> None:
        """Ask Vast to destroy the instance. Exit code is NOT proof — the caller re-lists.

        The prompt is answered by piping `y` rather than passing `-y`, so this works on CLI
        versions that predate the flag (the Makefile's gpu-destroy does the same).
        """
        try:
            out = subprocess.run(["vastai", "destroy", "instance", pod_id],
                                 input="y\n", capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"could not run vastai: {exc}") from exc
        if out.returncode != 0:
            raise RuntimeError(
                f"vastai destroy instance {pod_id} failed: {out.stderr.strip()}")
        time.sleep(DELETE_SETTLE_SEC)
