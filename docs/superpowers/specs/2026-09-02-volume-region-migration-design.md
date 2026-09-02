# Cross-datacenter volume migration — design

Date: 2026-09-02 · Status: approved for planning · Supersedes: the 2026-08-30 Telegram batch
control design's non-goal "Automated EU-CZ-1 failover" ([design](2026-08-30-telegram-batch-control-design.md#2-non-goals-v1)).

## 1. Why this reverses an earlier decision

The 2026-08-30 design deliberately cut this: "Unattended volume surgery is the wrong thing to
automate first. The bot surfaces the runbook instead." Trying the bot's new GPU picker live
(2026-09-02) surfaced the actual friction: the picker already shows the 5090 has stock at EU-CZ-1
when it reads none/Low at home, and the honest next question was "so why doesn't it just do that."
Asked directly, with the manual runbook's real steps and the irreversible-delete risk named
explicitly, the answer was: automate the copy and the volume swap, but keep every step's failure
mode safe (temp pods always destroyed; the OLD volume is the source of truth until a byte-for-byte
verify passes; only THEN delete it).

## 2. Non-goals (this v1)

| Cut | Why |
|---|---|
| Automating the reverse (EU-CZ-1 → EU-RO-1, once the 5090 comes back at home) | Not asked for. The 5090 shortage at EU-RO-1 is the trigger; nothing here assumes the direction. `--to-dc` is a parameter, so the same script covers it, but no bot button offers it yet. |
| Keeping a standing volume at EU-CZ-1 | Already rejected in `docs/gpu-pod.md#volume-migrate` ("Vì sao không giữ sẵn volume dự phòng ở EU-CZ-1") — monthly cost plus a second copy to keep in sync, for a shortage that is rare. This design migrates on demand, same as the manual runbook it replaces. |
| Migrating anything other than the Network Volume (Postgres data files, MinIO objects) as a SEPARATE step | They already live on the volume being migrated (`docs/gpu-pod.md`'s own layout: PGDATA and MinIO are symlinked onto the Network Volume). One rsync pass carries all of it. |
| Updating the Serverless endpoint's `networkVolumeIds`/`dataCenterIds` | Current deploy shape is GPU pod + `WORKER_SOURCE=local` (`docs/gpu-pod.md#deploy-shapes`) — no serverless endpoint is active to update. Left as a documented manual step for whenever serverless comes back, not built against config that does not currently exist. |

## 3. Architecture

A new, generic, bot-independent script — same layering choice as `drain.py`:

```
scripts/volume_migrate.py --to-dc EU-CZ-1 [--yes]

Phase 0  no --yes → describe the source volume (a free read) and stop. Creates NOTHING.
Phase 1  create-network-volume at --to-dc, same size as the source
Phase 2  provision 2 temp CPU pods (4 vCPU each — see §5), A=old volume, B=new volume
Phase 3  temp SSH keypair; enumerate sync units by listing the volume (never a name
         list — see §Phase 3); rsync -aR A→B direct (pod-to-pod IP), 8 threads
Phase 4  rsync -avncR (dry-run + checksum) per unit — must report 0 files needing sync —
         AND the top-level `ls -A` sets on A and B must be identical
Phase 5  destroy both temp pods (ALWAYS — success or failure, same discipline as drain.py's teardown)
Phase 6  Phase 4 passed → env_set(".env", "POD_VOLUME_ID", new_id); delete-network-volume(old_id)
         Phase 4 failed  → leave both volumes, report the diff, exit non-zero — nothing destructive
```

Without `--yes`: prints the plan (source size, target DC, estimated temp-pod cost) and exits —
same dry-run-by-default convention as `pod-provision.sh` and `drain.py`.

## 4. Step detail

**Phase 1 — new volume.** `runpodctl network-volume create --name <old-name>-<to-dc> --size <old-size> --data-center-id <to-dc>`. Size read from `network-volume get <old-id> -o json`, never guessed — RunPod
only allows growing a volume, so under-sizing here means a second migration later.

**Phase 2 — temp pods.** Neither touches `.env`'s `GPU_INSTANCE_ID`/`GPU_SSH_HOST` — those name the
real GPU pod slot. Temp pod ids and SSH info are tracked in a migration-local state file (see §6),
`COMPUTE_TYPE=cpu`, `CPU_FLAVOR=cpu5c`, `CPU_VCPU=4` — 4, not 2, so rsync is not CPU-bound (measured
2026-08-29: rsync at 4 vCPU/4 threads still capped ~94MB/s; the doc's own recommendation for a real
migration is more vCPU before more threads).

**Phase 3 — sync.** `ssh-keygen -t ed25519` into a temp file, public key appended to pod B's
`~/.ssh/authorized_keys` (via `runpodctl ssh info` + the pod's exec/SSH), private key placed on pod
A. `rsync -aR` for each SYNC UNIT, run in parallel, capped at 8 concurrent (measured
2026-08-29: 16 gets `MaxStartups`-rejected connections). **No throughput number is promised** — the
doc's ~427MB/s figure was measured for `dd|ssh cat`, not `rsync`; rsync was chosen anyway (see §1)
and its own real number gets measured and written down the first time this actually runs, not
guessed here.

**What a sync unit is, and why this paragraph was rewritten (2026-09-02, post-implementation
review).** The original text here said "each top-level subdirectory under the mount
(`diffusion_models`, `text_encoders`, `loras`, `checkpoints`, `PGDATA`, `minio`, …)" and the
implementation copied that list into a `MODEL_SUBDIRS` constant. **Every name in it except `minio`
is wrong.** Those are the children of `comfy-models/` — they are the eight parallel legs
`docs/gpu-pod.md#volume-migrate` measured, not volume entries — and `PGDATA` is the shell VARIABLE's
name in `pod-volume.sh`; the directory is `pgdata`. The volume's real top level, per
`motions-studio/setup/pod-volume.sh:85-89`, is:

```
comfy-models/   hf-cache/   ollama-models/   pgdata/   minio/   .motion-volume
```

Intersecting the old list against that yields exactly `["minio"]`. A real run would have copied
`minio/` alone, had verify report 0 differences **against that one-entry scope**, and then deleted
the source volume with every model, the HF cache, ollama-models and pgdata still only on it —
irreversible, on first real use. Caught in review before any live run.

So sync units are **enumerated, never named**: `ls -A` the mount and make every entry its own unit,
with exactly one exception — `comfy-models` is expanded one level deeper (`ls -A
$MOUNT/comfy-models`) so its 33-55GB still gets the 8-way parallelism that was the whole point of
the measurement. Units are relative paths from the mount (`minio`, `.motion-volume`,
`comfy-models/loras`), and `rsync -aR` with a `/./` marker in the source path is what makes ONE
command shape serve all three: verified against real GNU rsync 3.2.7 (docker `debian:12-slim`,
2026-09-02), the plain trailing-slash form fails with `mkdir "…/comfy-models/loras" failed: No such
file or directory` because pod B's volume is brand new and the parent does not exist, and it cannot
express a top-level plain FILE like the sentinel at all.

**Phase 4 — verify.** Two checks, because the first is structurally blind to the failure the second
catches.

1. `rsync -avncR` (dry-run + checksum, not `-a` alone — checksums, not just size/mtime) per sync
   unit, source→dest. Anything other than exactly 0 total changes is a hard stop.
2. A **coverage** check: the SET of top-level entries from `ls -A` on pod A must equal the set on
   pod B. Check 1 only ever speaks about the units it was GIVEN, so a unit list that missed a whole
   directory produces the identical clean zero as a perfect copy — which is precisely how the
   `MODEL_SUBDIRS` bug above could have destroyed the volume while reporting success. An empty unit
   list raises before sync even runs, for the same reason.

Both must pass. This is the ONE gate between "copied" and "safe to delete the original."

**Phase 5 — teardown.** Mirrors `drain.py::teardown`'s own discipline: wrapped so an exception
earlier in the phase still reaches pod deletion for both temp pods. Diagnostics (if the copy failed)
get pulled before destroy, same reasoning as the batch runner's own diagnostics-before-destroy.

**Phase 6 — swap or abort.** Only reached if Phase 4 reported 0 diffs. Writes `.env`'s
`POD_VOLUME_ID`, then `delete-network-volume` on the OLD id. The known "still referenced" failure
(`docs/gpu-pod.md#serverless-throttled` — a `THROTTLED` serverless worker holds a reference even
with zero pods listed) doesn't apply today (no serverless endpoint active — §2), but the delete call
is still wrapped: if it fails for any reason, the script reports the exact RunPod error and leaves
the OLD volume in place rather than retrying blindly. A migration that copied everything correctly
but failed to delete the source is a wasted ~$7/month, not a data-loss risk — the failure mode is
deliberately biased toward "costs a little more" over "lost something."

## 5. Failure/crash safety — the temp-pod lease

`drain.py` has `pod_watchdog.py` watching ONE lease for the real GPU pod. The two temp CPU pods this
script rents need the same net: if the migration script itself is killed (SIGKILL, laptop closes
the SSH session it was launched from, VPS reboots), a `finally` block does not run, and two CPU pods
would otherwise bill forever unnoticed.

Reuses the existing `batchlib_ext.lease` machinery rather than inventing a second one: writes
`batch/volume-migrate-lease.json` (distinct path from `batch/pod-lease.json` — this is not the real
GPU pod's lease, and must never be confused with it by `pod_watchdog.py`'s existing tier logic)
holding BOTH temp pod ids, written right after Phase 2 provisions them, cleared right after Phase 5
tears them down. `pod_watchdog.py` gains one more tier: same "orphan with no matching lease → kill"
reconciliation it already does for the real pod, applied to this second lease file — a small,
additive change, not a new watchdog process.

## 6. Progress reporting — reuses the batch pattern, does not reinvent it

Same shape as `scripts/tgbot/run.py`'s `_start_progress`/`tick_progress`, applied to a migration
instead of a drain:

- `volume_migrate.py` journals its own phase (`create`, `sync`, `verify`, `swap`, `done`, `failed`)
  and any measured throughput to `batch/volume-migrate.progress.json` as it goes — plain, synchronous
  writes, no IPC.
- `bot.py`'s existing poll loop gains one more per-tick check, parallel to `tick_progress`: if that
  file exists, render and edit one message the same way a drain's progress message works (animated
  while a phase is active, a final edit when `done`/`failed`).
- No separate "migration progress" UI is invented — this is the SAME editable-message mechanism the
  user already knows from every batch, so the phone experience during a migration looks like it
  already does during a render: one message, updated in place.

## 7. Trigger point in the bot

`_offer_run_confirm`'s "Other regions" section (shipped 2026-09-02) gains one button per listed
other-region entry: `Switch to <GPU> (<DC>)` → `_CB_MIGRATE_ASK:<dc-short>`. Tapping it does NOT
start the migration — it sends a SEPARATE explicit confirm naming the real cost/time/destructive
step plainly:

> This copies your Network Volume (~<measured size>GB) to EU-CZ-1: ~2 temp CPU pods for the
> duration (~$0.02-0.05 total), then **deletes the EU-RO-1 volume** once the copy is verified byte-
> for-byte. Takes an estimated 15-25 minutes. Cannot be undone once the old volume is deleted.
> [Yes, migrate] [Cancel]

Only on that second, explicit tap does `start_drain`-style `subprocess.Popen(["python3",
"scripts/volume_migrate.py", "--to-dc", dc, "--yes"])` fire, tracked the same way a drain's Popen is
(`_RUNNING`-equivalent keyed by a migration marker, so a second migration cannot be started while one
is already running).

A migration in flight also has to block a NEW `/confirm` from renting at the OLD datacenter mid-
copy — `_do_confirm`'s existing `drain_running` check gains a sibling check for a live migration
lease, refusing with a plain reason (the same shape as today's "a drain is already running" guard),
not silently letting two things race the same volume.

## 8. Testing strategy

Pure-logic pieces get real unit tests, same conventions as the rest of `scripts/tests/`
(`@patch("subprocess.run")`, `MagicMock(returncode=..., stdout=...)`):

- Verify-parse: "0 changes" vs "N changes" from real `rsync -avnc` output samples → pass/abort
  decision. This is the single most safety-critical piece of logic in the script and gets the most
  test coverage.
- Phase sequencing and the "always destroy temp pods" guarantee (mirrors
  `test_batch_drain.py::TestTeardown` and this session's own `TestChainOrTeardown` — an exception in
  any earlier phase still reaches pod deletion).
- Lease write/clear timing (provisioned right after Phase 2, cleared right after Phase 5, survives a
  simulated crash in between — i.e. the watchdog tier can find and kill it).
- `_CB_MIGRATE_ASK`/confirm-then-launch dispatch in `bot.py`, same `FakeTg` pattern as every other
  button flow in `test_batch_bot.py`.

Real end-to-end (an actual RunPod migration) is NOT run in CI — it costs real money and rents real
pods. It gets run once, live, on a small/cheap case before this is trusted for a real 79GB migration,
the same way the batch runner's own drain lifecycle was proven live before being trusted unattended.

## 9. Open questions carried into planning, not resolved here

- Exact `rsync` throughput at 8 threads / 4 vCPU is unmeasured (§4) — the plan should include running
  Phase 3 once, live, small-scale, and writing the real number into `docs/gpu-pod.md#volume-migrate`
  next to the existing measured table, replacing this open question rather than leaving it a guess.
- SSH key exchange onto a freshly-provisioned pod (write to `~/.ssh/authorized_keys` before the pod
  is confirmed reachable) needs the same wait/retry shape `pod-wait.sh` already has for the real GPU
  pod — reuse that script's polling loop rather than writing a second one.
