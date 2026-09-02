#!/usr/bin/env python3
"""Copy a Network Volume to another datacenter, swap .env to it, delete the
original — the automated form of docs/gpu-pod.md's manual EU-CZ-1 runbook.

    python3 scripts/volume_migrate.py --to-dc EU-CZ-1              # dry run
    python3 scripts/volume_migrate.py --to-dc EU-CZ-1 --yes        # for real

See docs/superpowers/specs/2026-09-02-volume-region-migration-design.md.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from batchlib.config import env_get, env_set
from batchlib_ext.migrate_lease import (MigrateLease, clear_migrate_lease,
                                        write_migrate_lease)

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
LEASE_PATH = ROOT / "batch" / "volume-migrate-lease.json"
PROGRESS_PATH = ROOT / "batch" / "volume-migrate.progress.json"

MODEL_SUBDIRS = ["diffusion_models", "text_encoders", "loras", "checkpoints",
                 "clip_vision", "vae", "upscale_models", "PGDATA", "minio"]
# 16 concurrent got some connections MaxStartups-rejected — measured
# 2026-08-29, docs/gpu-pod.md#volume-migrate.
MAX_RSYNC_THREADS = 8


def sh(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=ROOT, check=True, capture_output=True, text=True)


def _rp_json(*argv: str) -> dict:
    out = sh("runpodctl", *argv, "-o", "json")
    return json.loads(out.stdout or "{}")


def write_progress(phase: str, **extra) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps({"phase": phase, "at": time.time(), **extra},
                                        indent=2), encoding="utf-8")


def create_volume(source_volume_id: str, to_dc: str) -> tuple[str, int, str]:
    """Create the destination volume, same size as the source (RunPod only
    allows growing a volume later, never shrinking — under-sizing here means
    a second migration). Returns (new_volume_id, size_gb, source_dc)."""
    source = _rp_json("network-volume", "get", source_volume_id)
    size_gb = int(source["size"])
    source_dc = source.get("dataCenterId") or source.get("datacenterId") or ""
    name = f"{source.get('name', 'motion')}-{to_dc.lower()}"
    created = _rp_json("network-volume", "create", "--name", name,
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
    """
    api_key = env_get(ENV_PATH, "RUNPOD_API_KEY")
    if not api_key:
        raise RuntimeError(
            "RUNPOD_API_KEY not set in .env — required for the REST pod-create call")
    body = _cpu_pod_body(name, volume_id, dc, disk_gb)
    result = subprocess.run(
        ["curl", "-sS", "-X", "POST", "https://rest.runpod.io/v1/pods",
         "-H", f"Authorization: Bearer {api_key}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(body)],
        capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    pod_id = data.get("id") or data.get("podId")
    if not pod_id:
        raise RuntimeError(f"pod create for {name!r} did not return an id: {result.stdout}")
    return pod_id


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


def make_temp_keypair() -> tuple[Path, Path]:
    """A throwaway ed25519 keypair, scoped to this one migration, so pod A
    can SSH straight to pod B without either pod's normal keys involved."""
    tmpdir = Path(tempfile.mkdtemp(prefix="migrate-ssh-"))
    priv = tmpdir / "id_migrate"
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(priv), "-q"],
                   check=True)
    return priv, priv.with_suffix(".pub")


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


def existing_subdirs(host: str, port: int, mount: str = "/workspace") -> list[str]:
    """Only sync subdirectories that actually exist — a smaller/newer volume
    will not have every entry in MODEL_SUBDIRS."""
    result = subprocess.run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port), f"root@{host}",
         f"ls {mount}"], capture_output=True, text=True, check=True)
    present = set(result.stdout.split())
    return [d for d in MODEL_SUBDIRS if d in present]


def _rsync_cmd(mount: str, subdir: str, dest_host: str, dest_port: int,
               dry_run: bool) -> str:
    flags = "-avnc" if dry_run else "-a"
    return (f"rsync {flags} -e 'ssh -i ~/.ssh_migrate/id_migrate "
            f"-o StrictHostKeyChecking=accept-new -p {dest_port}' "
            f"{mount}/{subdir}/ root@{dest_host}:{mount}/{subdir}/")


def sync(host_a: str, port_a: int, host_b: str, port_b: int, subdirs: list[str],
         mount: str = "/workspace") -> None:
    """rsync -a, one process per subdir, run FROM pod A (using the temp key
    placed there by place_key_on) straight to pod B — never routed through
    this machine. Capped at MAX_RSYNC_THREADS concurrent.
    """
    for start in range(0, len(subdirs), MAX_RSYNC_THREADS):
        batch = subdirs[start:start + MAX_RSYNC_THREADS]
        procs = [
            subprocess.Popen(
                ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port_a),
                 f"root@{host_a}", _rsync_cmd(mount, d, host_b, port_b, dry_run=False)])
            for d in batch
        ]
        for p in procs:
            rc = p.wait()
            if rc != 0:
                raise RuntimeError(f"rsync leg exited {rc} — see pod A's own stderr above")


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


def verify(host_a: str, port_a: int, host_b: str, port_b: int, subdirs: list[str],
           mount: str = "/workspace") -> int:
    """rsync -avnc (dry-run + checksum) for every subdir, summed. The ONE
    gate between "copied" and "safe to delete the original" — see
    count_pending_changes and the Global Constraints at the top of this plan.
    """
    total = 0
    for d in subdirs:
        result = subprocess.run(
            ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-p", str(port_a),
             f"root@{host_a}", _rsync_cmd(mount, d, host_b, port_b, dry_run=True)],
            capture_output=True, text=True, check=True)
        total += count_pending_changes(result.stdout)
    return total


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to-dc", required=True)
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args(argv)

    old_volume_id = env_get(ENV_PATH, "POD_VOLUME_ID")
    if not old_volume_id:
        print("✗ POD_VOLUME_ID not set in .env — nothing to migrate", file=sys.stderr)
        return 1

    new_volume_id, size_gb, source_dc = create_volume(old_volume_id, args.to_dc)
    if not args.yes:
        print(f"DRY RUN. Would migrate {size_gb}GB from POD_VOLUME_ID={old_volume_id} "
             f"({source_dc}) to {args.to_dc} (created {new_volume_id} already — "
             f"delete it by hand if you do not proceed: "
             f"runpodctl network-volume delete {new_volume_id}).")
        print("Re-run with --yes to actually copy and swap.")
        return 0

    # Phases 2-6 land in later tasks of this plan.
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
