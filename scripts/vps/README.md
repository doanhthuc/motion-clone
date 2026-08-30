# VPS setup

The VPS exists for one reason: something has to be awake to rent a pod and to guarantee it dies.
The Mac is a laptop that sleeps and travels, so it cannot be that something.

## Box

Hetzner CX22 or equivalent — 2 vCPU / 4 GB / 40 GB, ~EUR 4/month. Cheaper than any RunPod CPU pod,
and not billed by the hour. 40 GB is enough for material plus `out/`; keep it that way with
`make batch-clean KEEP=3`.

## Install

```bash
apt update && apt install -y python3 git rsync ffmpeg make
# runpodctl: see https://docs.runpod.io/cli/install
git clone <this repo> /opt/motion-clone && cd /opt/motion-clone
```

## Secrets — copy, never commit

`.env` and `motions/.env` are gitignored and must be copied from the Mac by hand:

```bash
scp .env       vps:/opt/motion-clone/.env
scp motions/.env vps:/opt/motion-clone/motions/.env
```

The VPS needs `GEMINI_API_KEY` (local try-on), `RUNPOD_API_KEY`, `DOMAIN`, `POD_VOLUME_ID`, and the
SSH private key `pod-bootstrap.sh` uses to reach the pod. Nothing here is ever committed — the repo
is public and `motions-studio/setup/scrub-secrets.sh --check` gates every commit.

## First run must be manual

`load_settings()` needs `NUXT_MOTION_API_KEY` in `motions/.env`, and that value is written by
`gpu-bootstrap` when it configures a pod. On a brand-new VPS it does not exist yet, so
`drain.py` would fail before renting anything. Do one manual cycle first:

```bash
make gpu-preflight
CONFIRM=yes bash scripts/pod-provision.sh && make gpu-wait && make gpu-bootstrap
make gpu-destroy
```

After that, `motions/.env` has the key and drains are unattended.

## Watchdog

```bash
cp scripts/vps/pod-watchdog.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now pod-watchdog
systemctl status pod-watchdog          # must be active (running)
make watchdog-dry                      # must report no lease and destroy nothing
journalctl -u pod-watchdog -f          # watch it tick
```

## Pod ownership — the rule that matters

**From this point the VPS is the sole owner of pod lifecycle.** Do not run `gpu-provision`,
`gpu-up` or `gpu-destroy` on the Mac. Two machines each tracking a pod in their own `.env` means
double provisioning, or the Mac destroying a pod mid-batch — at $0.99/hour.

What actually protects you, and what does not:

- **`make gpu-preflight` on the Mac warns when a live pod named `motion-transfer` exists** — it asks
  `runpodctl pod list -o json`, which is the only state both machines share. It is a warning, not a
  block: it cannot tell a pod the VPS rented from one you rented yourself, and it says nothing about
  whether a batch is running on it. Check `make watchdog-dry` on the VPS before acting on it.
- **It does not read `batch/pod-lease.json`.** That file is written by `drain.py` on the VPS and is
  gitignored, so it never exists on the Mac. A guard that checked it (as this one did until
  2026-08-31) could never fire on the machine it was written for.
- **Nothing prevents double ownership.** There is no lock, no shared lease, no handshake. The rule
  above is a convention enforced by one warning and by tier-3 reconciliation — and tier 3 catches
  the collision only after the money is spent, and only for pods named `motion-transfer` that no
  lease claims, after a 10-minute grace window.

The Mac keeps `make batch` for debugging against a pod the VPS already rented. That is read-only
with respect to ownership and is fine.

## Running a batch

```bash
make drain FILE=batch/2026-08-30.yaml               # dry run: prints the plan, rents nothing
make drain FILE=batch/2026-08-30.yaml CONFIRM=yes   # rents, runs, destroys
```
