#!/usr/bin/env python3
"""Copy a Network Volume to another datacenter, swap .env to it, delete the
original — the automated form of docs/gpu-pod.md's manual EU-CZ-1 runbook.

    python3 scripts/volume_migrate.py --to-dc EU-CZ-1              # dry run
    python3 scripts/volume_migrate.py --to-dc EU-CZ-1 --yes        # for real

The dry run creates NOTHING — no volume, no pod, no charge. Everything it
prints comes from one free `network-volume get` on the volume you already own.

The delete of the source volume is the one irreversible step, and it is gated
on verify() being clean in BOTH senses (see VerifyResult): every enumerated
unit checksum-identical, AND both pods listing the same top-level entries.

See docs/superpowers/specs/2026-09-02-volume-region-migration-design.md.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.config import env_get, env_set
from batchlib_ext.migrate_lease import (MigrateLease, clear_migrate_lease,
                                        write_migrate_lease)
from batchlib_ext.podctl import RunpodCtl
# Imported, not reimplemented: destroy_verified is the repo's one answer to
# "an exit code is not proof that a pod is gone", and teardown below needs
# exactly that answer (see its own docstring).
from pod_watchdog import destroy_verified

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
LEASE_PATH = ROOT / "batch" / "volume-migrate-lease.json"
PROGRESS_PATH = ROOT / "batch" / "volume-migrate.progress.json"

RUNPOD_PODS_URL = "https://rest.runpod.io/v1/pods"

# ── What actually lives on a Network Volume ──────────────────────────────────
# motions-studio/setup/pod-volume.sh:85-89 is the ONLY thing that creates the
# layout, and its top level is:
#
#     comfy-models/   hf-cache/   ollama-models/   pgdata/   minio/
#
# plus the `.motion-volume` sentinel FILE (pod-volume.sh:91,96-99).
#
# That list is a comment, deliberately — never an allowlist this code reads.
# It used to be one, and the shape of the bug is worth keeping written down:
# `MODEL_SUBDIRS = ["diffusion_models", "text_encoders", "loras", ...,
# "PGDATA", "minio"]` named the children of comfy-models/ as if they were
# top-level entries (they are the eight parallel rsync legs of
# docs/gpu-pod.md#volume-migrate, not volume entries), and spelled pgdata in
# the shell VARIABLE's case rather than the directory's. Intersected against a
# real `ls`, that list yields exactly ["minio"] — so a real run would have
# copied minio/ alone, had verify() report 0 diffs against that one-entry
# scope, and then deleted the source volume with every model, the HF cache,
# ollama-models and pgdata still only on it. Irreversible, on first real use.
#
# So: enumerate what is there (existing_subdirs), never match against names.
COMFY_MODELS_DIR = "comfy-models"

# 16 concurrent got some connections MaxStartups-rejected — measured
# 2026-08-29, docs/gpu-pod.md#volume-migrate.
MAX_RSYNC_THREADS = 8

# An exception message on its way into the progress file — and from there,
# verbatim, into a Telegram message (tgbot.bot.tick_migration_progress). Long
# enough to name the failure, short enough that a phone still shows the rest
# of the message.
REASON_MAX_CHARS = 400


def sh(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=ROOT, check=True, capture_output=True, text=True)


def _rp_json(*argv: str) -> dict:
    out = sh("runpodctl", *argv, "-o", "json")
    return json.loads(out.stdout or "{}")


def _scrub_and_bound(text: str) -> str:
    """Substitute out .env's known secrets, then cap the length.

    Both halves matter for anything on its way into the progress file, because
    everything in there is rendered verbatim into a Telegram message by
    tgbot.bot.tick_migration_progress: a secret would be published, and an
    unbounded string would blow the Bot API's own 4096-character message cap
    and turn an error report into a second error.
    """
    try:
        for key in ("RUNPOD_API_KEY", "TG_BOT_TOKEN", "RUNPOD_SSH_KEY"):
            secret = env_get(ENV_PATH, key)
            # Length guard: an empty or one-character value would replace
            # every occurrence of nothing/that character in the message.
            if secret and len(secret) >= 8:
                text = text.replace(secret, f"<{key}>")
    except OSError:
        # Reporting a failure must not itself be able to fail on a missing .env.
        pass
    return text[:REASON_MAX_CHARS] + "…" if len(text) > REASON_MAX_CHARS else text


def safe_reason(exc: BaseException) -> str:
    """What may be written as `reason=` in the progress file.

    NEVER repr(exc). Two independent reasons:

    1. `subprocess.CalledProcessError` puts the FULL ARGV in both its repr()
       and its str(). This script no longer passes a bearer token on an argv
       (provision_temp_pod uses urllib for exactly that reason), but ssh/scp/
       rsync argvs still reach here, and any future subprocess that carries a
       secret would inherit the same hole. So the known secrets in .env are
       substituted out by name, whatever exception carried them.
    2. The result is rendered into a Telegram message. An unbounded repr of a
       10k-character rsync failure would make the sendMessage itself fail
       (4096-char cap), turning an error report into a second error.
    """
    message = str(exc).strip()
    text = f"{exc.__class__.__name__}: {message}" if message else exc.__class__.__name__
    return _scrub_and_bound(text)


def write_progress(phase: str, **extra) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps({"phase": phase, "at": time.time(), **extra},
                                        indent=2), encoding="utf-8")


def describe_volume(volume_id: str) -> tuple[int, str, str]:
    """(size_gb, datacenter_id, name) for a volume that already exists.

    A FREE read — `network-volume get` creates nothing and bills nothing. That
    is the whole point of it being its own function: the dry run used to call
    create_volume() to learn the size to print, which left a real, billable
    destination volume behind every time someone ran the script without --yes
    just to see what it would do.
    """
    source = _rp_json("network-volume", "get", volume_id)
    return (int(source["size"]),
            source.get("dataCenterId") or source.get("datacenterId") or "",
            source.get("name", "motion"))


def create_volume(source_volume_id: str, to_dc: str) -> tuple[str, int, str]:
    """Create the destination volume, same size as the source (RunPod only
    allows growing a volume later, never shrinking — under-sizing here means
    a second migration). Returns (new_volume_id, size_gb, source_dc)."""
    size_gb, source_dc, name = describe_volume(source_volume_id)
    created = _rp_json("network-volume", "create", "--name", f"{name}-{to_dc.lower()}",
                       "--size", str(size_gb), "--data-center-id", to_dc)
    return created["id"], size_gb, source_dc


def _cpu_pod_body(name: str, volume_id: str, dc: str, disk_gb: int) -> dict:
    return {
        "name": name,
        "computeType": "CPU",
        "cpuFlavorIds": ["cpu5c"],
        "vcpuCount": 4,
        "imageName": "runpod/base:0.6.2-cpu",
        "containerDiskInGb": disk_gb,
        "ports": ["22/tcp"],
        "networkVolumeId": volume_id,
        "volumeMountPath": "/workspace",
        "dataCenterIds": [dc],
    }


def provision_temp_pod(name: str, volume_id: str, dc: str, disk_gb: int) -> str:
    """A temp CPU pod via REST — same reason pod-provision.sh's own CPU
    branch goes through REST instead of `runpodctl pod create`: that CLI has
    no flag for cpuFlavorIds/vcpuCount, only a fixed 2 vCPU/4GB default.
    Returns the pod id.

    urllib, NOT `curl` — and that is a secrets decision, not a style one.
    Shelling out put `Authorization: Bearer <RUNPOD_API_KEY>` on the argv, and
    a transport-level failure (DNS, TLS, connection reset — none of which
    `-sS` suppresses) raises CalledProcessError, whose repr()/str() carry the
    WHOLE argv. main()'s handler wrote that into the progress file, and
    tgbot.bot.tick_migration_progress renders `reason` straight into a
    Telegram message, so one flaky DNS lookup would have published the API key
    to the chat and to journald. Held in-process, the header cannot appear in
    any exception: urllib.error.URLError carries only the underlying reason,
    and HTTPError only the response. Same transport tgbot/tgclient.py uses.
    """
    api_key = env_get(ENV_PATH, "RUNPOD_API_KEY")
    if not api_key:
        raise RuntimeError(
            "RUNPOD_API_KEY not set in .env — required for the REST pod-create call")
    body = _cpu_pod_body(name, volume_id, dc, disk_gb)
    request = urllib.request.Request(
        RUNPOD_PODS_URL, data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # RunPod puts the real explanation ("insufficient capacity", a bad
        # dataCenterId) in the BODY of a 4xx, so it must be read before the
        # status alone is reported.
        with exc:
            raw = exc.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        # `exc` deliberately not chained with `from`: nothing in URLError
        # carries the key, but neither does anything in it help a reader more
        # than its own message, and a chained traceback is one more surface
        # that reaches journald.
        raise RuntimeError(f"pod create for {name!r} could not reach RunPod: "
                           f"{safe_reason(exc)}")
    try:
        data = json.loads(raw)
    except ValueError:
        raise RuntimeError(f"pod create for {name!r} returned non-JSON: {raw[:200]!r}")
    pod_id = data.get("id") or data.get("podId")
    if not pod_id:
        raise RuntimeError(f"pod create for {name!r} did not return an id: {raw[:400]}")
    return str(pod_id)


def wait_for_ssh(pod_id: str, timeout_min: int = 10) -> tuple[str, int]:
    """Poll `runpodctl ssh info` until it carries a host+port AND a real SSH
    handshake succeeds — status strings alone are not proof (pod-wait.sh's
    own docstring: RunPod reports RUNNING well before sshd is listening).
    Key names are read permissively, matching pod-wait.sh's probe(): this
    CLI's JSON shape has moved across releases.
    """
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        raw = sh("runpodctl", "ssh", "info", pod_id, "-o", "json").stdout
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        host = (data.get("host") or data.get("hostname")
               or data.get("publicIp") or data.get("ip"))
        port = data.get("port") or data.get("sshPort") or data.get("publicPort")
        if host and port:
            probe = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "ConnectTimeout=5", "-p", str(port), f"root@{host}", "true"],
                capture_output=True)
            if probe.returncode == 0:
                return host, int(port)
        time.sleep(10)
    raise RuntimeError(f"pod {pod_id} did not accept SSH within {timeout_min} min")


KEYPAIR_DIR_PREFIX = "migrate-ssh-"


def make_temp_keypair() -> tuple[Path, Path]:
    """A throwaway ed25519 keypair, scoped to this one migration, so pod A
    can SSH straight to pod B without either pod's normal keys involved.

    The caller MUST pass the returned private key to discard_temp_keypair()
    when it is done — main() does it in the same `finally` that tears the
    temp pods down. Without that, every migration (and every test run that
    generated a real key) left a private key sitting in /tmp forever.
    """
    tmpdir = Path(tempfile.mkdtemp(prefix=KEYPAIR_DIR_PREFIX))
    priv = tmpdir / "id_migrate"
    try:
        subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv), "-q"],
                       check=True)
    except BaseException:
        # ssh-keygen missing, or a Ctrl-C between mkdtemp and the key landing:
        # the directory exists either way, and nobody downstream has a handle
        # on it to clean it up, so it has to happen here.
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise
    return priv, priv.with_suffix(".pub")


def discard_temp_keypair(priv_key_path: Path | None) -> None:
    """Remove the whole mkdtemp directory the keypair lives in.

    Refuses any directory whose name does not carry KEYPAIR_DIR_PREFIX:
    rmtree on a path derived from a caller's argument is a foot-gun, and the
    prefix make_temp_keypair itself set is the only evidence this function
    has that the directory is ours to delete.
    """
    if priv_key_path is None:
        return
    tmpdir = Path(priv_key_path).parent
    if not tmpdir.name.startswith(KEYPAIR_DIR_PREFIX):
        return
    shutil.rmtree(tmpdir, ignore_errors=True)


def install_key_on(host: str, port: int, pub_key_path: Path) -> None:
    """Appends the temp public key to pod B's authorized_keys — piped over
    stdin rather than interpolated into a shell command."""
    pub_key = pub_key_path.read_text(encoding="utf-8")
    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port), f"root@{host}",
         "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys"],
        input=pub_key, text=True, check=True)


def place_key_on(host: str, port: int, priv_key_path: Path) -> None:
    """Copies the temp private key onto pod A, so pod A can SSH straight to
    pod B without routing the transfer through this machine."""
    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port), f"root@{host}",
         "mkdir -p ~/.ssh_migrate"], check=True)
    subprocess.run(
        ["scp", "-o", "StrictHostKeyChecking=accept-new", "-P", str(port),
         str(priv_key_path), f"root@{host}:~/.ssh_migrate/id_migrate"], check=True)
    subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port), f"root@{host}",
         "chmod 600 ~/.ssh_migrate/id_migrate"], check=True)


def _ssh_out(host: str, port: int, command: str) -> str:
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port), f"root@{host}",
         command], capture_output=True, text=True, check=True)
    return result.stdout


def list_top_level(host: str, port: int, mount: str = "/workspace") -> list[str]:
    """Every entry at the volume's top level, dotfiles included.

    `ls -A`, not `ls`: the `.motion-volume` sentinel pod-volume.sh writes is a
    dotfile, and a plain `ls` would leave it off the copy — which the
    top-level coverage check in verify() would then (correctly) refuse the
    whole migration over.
    """
    return sorted(name for name in
                  (line.strip() for line in _ssh_out(host, port, f"ls -A {mount}").splitlines())
                  if name)


def existing_subdirs(host: str, port: int, mount: str = "/workspace") -> list[str]:
    """The volume's real contents, split into one rsync unit per entry.

    Listing-driven, never matched against a name list — see COMFY_MODELS_DIR's
    comment for the data-loss bug that a name list caused. Anything on the
    volume becomes a unit, including entries added long after this was
    written, and a smaller volume simply produces fewer units.

    `comfy-models` is the one entry expanded a level deeper. It is 33-55GB,
    the reason MAX_RSYNC_THREADS exists at all: docs/gpu-pod.md#volume-migrate
    measured 8 parallel legs at ~427MB/s against ~57MB/s for one, and the
    legs it measured were exactly this directory's children
    (diffusion_models, text_encoders, loras, checkpoints, …). Every OTHER
    top-level entry is small enough to be one leg, so splitting them further
    would only add SSH handshakes to the same MaxStartups budget.

    Units are relative paths from `mount` — "minio", ".motion-volume",
    "comfy-models/loras" — and _rsync_cmd handles all three shapes with one
    command form.
    """
    units: list[str] = []
    for name in list_top_level(host, port, mount):
        if name != COMFY_MODELS_DIR:
            units.append(name)
            continue
        children = sorted(
            child for child in
            (line.strip() for line in
             _ssh_out(host, port, f"ls -A {mount}/{name}").splitlines())
            if child)
        if children:
            units.extend(f"{name}/{child}" for child in children)
        else:
            # An EMPTY comfy-models is still a unit: the directory itself has
            # to exist on pod B, or the top-level coverage check in verify()
            # reports it missing and refuses the whole migration.
            units.append(name)
    return units


def _rsync_cmd(mount: str, unit: str, dest_host: str, dest_port: int,
               dry_run: bool) -> str:
    """One rsync leg, as a shell string to run ON pod A.

    `-R` (--relative) with the `/./` marker in the source path, rather than
    the more obvious `{mount}/{unit}/ → host:{mount}/{unit}/`. Two reasons,
    both verified against real GNU rsync 3.2.7 (docker debian:12-slim,
    2026-09-02):

    1. Pod B's volume is BRAND NEW and empty, so `/workspace/comfy-models`
       does not exist yet. The trailing-slash form fails outright on a nested
       unit — `rsync: [Receiver] mkdir "/dst/comfy-models/loras" failed: No
       such file or directory (2)`, exit 11 — because rsync creates the final
       destination directory but not missing parents. `-R` recreates the
       whole relative path.
    2. Units are not all directories. `.motion-volume` is a plain file, and
       `{mount}/.motion-volume/` with a trailing slash is not a valid source.
       The `-R` form copies a file and a directory with the same syntax.

    Clean `-avncR` output on identical trees is the bare "sending incremental
    file list" banner and nothing else (no "./" line), which
    count_pending_changes already scores as 0; a dirty one lists full
    relative paths such as "comfy-models/loras/a.safetensors".
    """
    flags = "-avncR" if dry_run else "-aR"
    return (f"rsync {flags} -e 'ssh -i ~/.ssh_migrate/id_migrate "
            f"-o StrictHostKeyChecking=accept-new -p {dest_port}' "
            f"{mount}/./{unit} root@{dest_host}:{mount}/")


def sync(host_a: str, port_a: int, host_b: str, port_b: int, units: list[str],
         mount: str = "/workspace") -> None:
    """rsync -aR, one process per sync unit, run FROM pod A (using the temp
    key placed there by place_key_on) straight to pod B — never routed
    through this machine. Capped at MAX_RSYNC_THREADS concurrent.
    """
    for start in range(0, len(units), MAX_RSYNC_THREADS):
        batch = units[start:start + MAX_RSYNC_THREADS]
        procs = [
            subprocess.Popen(
                ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port_a),
                 f"root@{host_a}", _rsync_cmd(mount, d, host_b, port_b, dry_run=False)])
            for d in batch
        ]
        # Wait on EVERY process already launched in this batch before
        # raising anything. A previous version returned/raised on the FIRST
        # non-zero .wait(), leaving any OTHER Popen in the same batch
        # running untracked in the background — flagged in the Task 6
        # review and deferred to here, the task that first wires sync()
        # into main()'s real flow.
        bad_rcs = [rc for rc in (p.wait() for p in procs) if rc != 0]
        if bad_rcs:
            raise RuntimeError(f"rsync leg exited {bad_rcs[0]} — see pod A's own stderr above")


#  real GNU rsync 3.2.7 (verified locally via `docker run debian:12-slim`,
# 2026-09-02) prints this literal line FIRST, before any file/directory
# entries, whenever -v is combined with the default incremental-recursion
# sender — on every single invocation, clean or not. Treating it as a
# changed "file" would report 1 pending change on a perfectly clean verify,
# permanently blocking the delete-original gate. "receiving ..." is the
# mirror-image line rsync prints on the receiving side; not expected from
# this script's own invocations (always run as the sender on pod A), kept
# here defensively since it costs nothing to skip.
_RSYNC_HEADER_LINES = {
    "sending incremental file list",
    "receiving incremental file list",
}


def count_pending_changes(rsync_dry_run_output: str) -> int:
    """How many real changes `rsync -avnc`'s stdout reports.

    -v lists one line per file that WOULD be transferred, then a blank line,
    then a summary block starting with "sent". Everything before that
    summary counts, EXCEPT: the bare "./" line -a always prints for the top
    directory itself even when nothing inside it changed, the
    "sending/receiving incremental file list" banner line (see
    _RSYNC_HEADER_LINES), and blank lines — counting any of those would
    report a pending change on a perfectly clean verify.

    Only the "sent " summary line itself ends the count. Blank lines are
    skipped rather than treated as the end-of-listing marker: in every real
    sample seen there is exactly one, immediately before "sent", but nothing
    guarantees rsync never emits one earlier — stopping there instead of
    breaking would silently undercount everything after it.
    """
    count = 0
    for line in rsync_dry_run_output.splitlines():
        stripped = line.strip()
        if line.startswith("sent "):
            break
        if not stripped or stripped == "./" or stripped in _RSYNC_HEADER_LINES:
            continue
        count += 1
    return count


@dataclass(frozen=True)
class VerifyResult:
    """Two independent questions, because passing one says nothing about the
    other: did every unit we COPIED come out byte-identical, and did the set
    of units we copied actually cover the volume?"""
    pending_changes: int
    missing_on_b: list[str] = field(default_factory=list)
    extra_on_b: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (self.pending_changes == 0
                and not self.missing_on_b and not self.extra_on_b)

    def reason(self) -> str:
        parts = []
        if self.pending_changes:
            parts.append(f"{self.pending_changes} file(s) still differ after sync")
        if self.missing_on_b:
            parts.append("top-level entries present on the SOURCE but missing on the "
                         f"destination: {', '.join(self.missing_on_b)}")
        if self.extra_on_b:
            parts.append("unexpected top-level entries on the destination: "
                         f"{', '.join(self.extra_on_b)}")
        return "; ".join(parts) or "verified clean"


def verify(host_a: str, port_a: int, host_b: str, port_b: int, units: list[str],
           mount: str = "/workspace") -> VerifyResult:
    """The ONE gate between "copied" and "safe to delete the original".

    Two checks, and the second is not decoration. `rsync -avncR` per unit
    answers "is every file we copied identical on both sides" — but only for
    the units it is GIVEN. A unit list that missed a whole top-level directory
    produces the exact same clean zero as a perfect copy, which is how a
    hard-coded name list could have destroyed the volume (see
    COMFY_MODELS_DIR). So the top-level entry NAMES on both pods are compared
    too: a cheap `ls -A` on each, no checksums, catching precisely the class
    of failure the per-unit checks are structurally blind to.

    Deliberately symmetric — an unexpected extra entry on the destination is
    reported as well as a missing one. It could be harmless, but "refuse and
    keep both volumes" is the direction this script is allowed to be wrong in.
    """
    total = 0
    for unit in units:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port_a),
             f"root@{host_a}", _rsync_cmd(mount, unit, host_b, port_b, dry_run=True)],
            capture_output=True, text=True, check=True)
        total += count_pending_changes(result.stdout)

    on_a = set(list_top_level(host_a, port_a, mount))
    on_b = set(list_top_level(host_b, port_b, mount))
    return VerifyResult(pending_changes=total,
                        missing_on_b=sorted(on_a - on_b),
                        extra_on_b=sorted(on_b - on_a))


def teardown_temp_pods(pod_a: str | None, pod_b: str | None,
                       pods_api=None) -> None:
    """ALWAYS called, success or failure — same discipline as
    drain.py::teardown. A pod_id of None means it was never provisioned
    (an earlier phase failed first) and is simply skipped, not an error.

    The lease is cleared ONLY when every pod that existed is verifiably gone.
    `runpodctl pod delete` exiting 0 is not proof (Makefile:139-142 records
    this repo printing success over an aborted destroy and learning otherwise
    from the invoice), so the same destroy_verified re-list pod_watchdog.py
    uses is applied here. Clearing the lease over an unverified pod would be
    strictly worse than leaving it: pod_watchdog's tier 1/2 retries a LEASED
    migration pod on its next 60s tick, while a lease-less one falls to tier
    3's orphan path, which waits out a 10-minute grace window first — ten
    minutes of billing bought by deleting the one file that pointed at it.
    """
    pods_api = pods_api if pods_api is not None else RunpodCtl()
    confirmed = True
    for pod_id in (pod_a, pod_b):
        if pod_id is None:
            continue
        try:
            gone = destroy_verified(pods_api, pod_id)
        except Exception as exc:
            # Broad on purpose. A raise must not skip the OTHER pod's delete —
            # the same reason sync() waits on every leg in a batch before
            # raising — and RunpodCtl can raise RuntimeError (non-zero exit,
            # unparseable JSON) or subprocess.TimeoutExpired, which is not one.
            gone = False
            print(f"✗ delete failed for {pod_id}: {safe_reason(exc)}", file=sys.stderr)
        if not gone:
            confirmed = False
            print(f"✗ DESTROY NOT CONFIRMED for {pod_id} — it may still be in "
                  f"'runpodctl pod list' and STILL BILLING. Keeping the migration "
                  f"lease so the watchdog retries. By hand: "
                  f"runpodctl pod delete {pod_id}", file=sys.stderr)
    if confirmed:
        clear_migrate_lease(LEASE_PATH)


def swap(*, new_volume_id: str, old_volume_id: str) -> None:
    """Only ever called after verify() reported VerifyResult.ok — every unit
    checksum-clean AND both pods listing the same top-level entries. The
    caller (main) is where that gate lives."""
    env_set(ENV_PATH, "POD_VOLUME_ID", new_volume_id)
    delete = subprocess.run(["runpodctl", "network-volume", "delete", old_volume_id],
                            capture_output=True, text=True)
    if delete.returncode != 0:
        # Scrubbed and bounded for the same reason safe_reason exists: this
        # `warning` is rendered into a Telegram message by
        # tgbot.bot.tick_migration_progress, and runpodctl's stderr is
        # arbitrary text of arbitrary length.
        write_progress("done",
                       warning=f"copied and swapped .env, but could not delete "
                               f"{old_volume_id}: "
                               f"{_scrub_and_bound(delete.stderr.strip())}")
        return
    write_progress("done")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to-dc", required=True)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    try:
        old_volume_id = env_get(ENV_PATH, "POD_VOLUME_ID")
        if not old_volume_id:
            print("✗ POD_VOLUME_ID not set in .env — nothing to migrate", file=sys.stderr)
            return 1

        # The dry run creates NOTHING. `network-volume get` on the volume that
        # already exists is a free read and answers everything this line needs;
        # calling create_volume() here (what this used to do) left a real,
        # billable destination volume behind on every "just show me the plan".
        if not args.yes:
            size_gb, source_dc, _name = describe_volume(old_volume_id)
            print(f"DRY RUN. Would migrate {size_gb}GB from "
                 f"POD_VOLUME_ID={old_volume_id} ({source_dc}) to {args.to_dc}. "
                 f"Nothing has been created — no volume, no pod, no charge.")
            print("Re-run with --yes to create the destination volume, copy and swap.")
            return 0

        new_volume_id, size_gb, source_dc = create_volume(old_volume_id, args.to_dc)

        write_progress("create", to_dc=args.to_dc, new_volume_id=new_volume_id)
        pod_a: str | None = None
        pod_b: str | None = None
        priv: Path | None = None
        try:
            pod_a = provision_temp_pod("migrate-tmp-a", old_volume_id, source_dc, size_gb + 20)
            pod_b = provision_temp_pod("migrate-tmp-b", new_volume_id, args.to_dc, size_gb + 20)
            write_migrate_lease(LEASE_PATH, MigrateLease(
                pod_a_id=pod_a, pod_b_id=pod_b, started_at=time.time(), to_dc=args.to_dc))

            write_progress("sync", pod_a=pod_a, pod_b=pod_b)
            host_a, port_a = wait_for_ssh(pod_a)
            host_b, port_b = wait_for_ssh(pod_b)
            priv, pub = make_temp_keypair()
            install_key_on(host_b, port_b, pub)
            place_key_on(host_a, port_a, priv)
            units = existing_subdirs(host_a, port_a)
            if not units:
                # Before sync(), before verify(), before anything can report a
                # reassuring zero: verify() over an empty unit list sums to 0
                # pending changes, which would walk straight through the one
                # gate that stands between this script and `network-volume
                # delete`. An empty listing means the volume is not mounted
                # where we think it is, not that there is nothing to copy.
                raise RuntimeError(
                    f"pod A ({pod_a}) lists no entries at all under /workspace — "
                    f"refusing to 'migrate' an empty listing. The Network Volume "
                    f"is probably not mounted there; {old_volume_id} is untouched.")
            sync(host_a, port_a, host_b, port_b, units)

            write_progress("verify", pod_a=pod_a, pod_b=pod_b)
            result = verify(host_a, port_a, host_b, port_b, units)
        finally:
            # Nested, not two statements side by side: a raise out of the pod
            # teardown (a RunpodCtl timeout, say) would otherwise skip the key
            # cleanup and leave a private key in /tmp forever — the exact leak
            # this cleanup was added for.
            try:
                teardown_temp_pods(pod_a, pod_b)
            finally:
                discard_temp_keypair(priv)

        if not result.ok:
            write_progress("failed",
                           reason=_scrub_and_bound(
                               f"{result.reason()} — NOT deleting {old_volume_id}. "
                               f"Both volumes kept: {old_volume_id} (original), "
                               f"{new_volume_id} (partial copy)."))
            print(f"✗ verify failed: {result.reason()} — aborting, both volumes kept",
                 file=sys.stderr)
            return 1

        swap(new_volume_id=new_volume_id, old_volume_id=old_volume_id)
        return 0
    except Exception as exc:
        # safe_reason, never repr(exc): this string is read back by
        # tgbot.bot.tick_migration_progress and rendered into a Telegram
        # message. See safe_reason's docstring.
        write_progress("failed", reason=safe_reason(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
