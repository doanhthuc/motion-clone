# Vast.ai as a selectable GPU provider — design

Date: 2026-09-19 · Status: approved in chat; pending written-spec review · Supersedes nothing

RunPod often has no RTX 5090 in the Network Volume's datacenter (EU-RO-1). This spec makes Vast.ai a
second, user-selected GPU provider for a batch, without giving up the money and teardown guarantees
the RunPod path has.

Numbers below were measured on 2026-09-19 and are recorded in
[docs/gpu-pod.md](../../gpu-pod.md#vast-ghcr) (`#vast-ghcr`, `#vast-e2e`, `#vast-search-sampling`).
Function names are the durable reference; line numbers drift.

---

## 1. What was asked

- Fallback when RunPod is out of 5090s, wired into the Telegram bot.
- The **GPU picker** offers a choice between RunPod and Vast, not only the stock-out card.
- Rent a Vast machine by `machine_id` when a good one is known; download models **in parallel with
  bootstrap**; a **teardown net** that also covers Vast; do not pick the machine by price alone —
  download speed decides how fast the box becomes usable.
- Pipelines `camera-motion-enhance`, `character-swap-enhance` and the like must run on Vast, and only
  the models a batch actually needs are downloaded (a batch may be only
  `tryon-camera-motion-enhance` with FlashVSR).
- Acceptance bar set earlier: cold start **≤ 10 minutes**.

Decisions taken in chat: the switch to Vast is a **user tap** (never automatic — the two clouds price
differently and Vast bills bandwidth per TB); scope is motion + enhance + character-swap behind a
gate, with one measured Vast session per new pipeline family before its button is enabled.

## 2. What the code already does

- **The runner needs no change.** `scripts/batchlib` reaches the box through the Cloudflare Tunnel
  (`https://$DOMAIN`, `batchlib/config.py`), which is provider-independent. A full Vast session ran
  through that path on 2026-09-19.
- `scripts/pod-provision.sh` already has a `GPU_PROVIDER=vast` branch (search → create), `make
  gpu-destroy` already has a vast branch that verifies the instance is gone, `pod-wait.sh` and
  `pod-bootstrap.sh` handle an empty `POD_VOLUME`.
- **Teardown is RunPod-only.** `batchlib_ext.lease.Lease` has no provider field;
  `batchlib_ext.podctl.RunpodCtl` is the only `PodControl`; `scripts/pod_watchdog.py` tiers 1–3 list
  and destroy through it; `batchlib_ext.watchdog.DESTROYABLE_NAMES` scopes tier 3 to the name
  `motion-transfer`. A Vast instance today is invisible to the watchdog — a billing box nobody watches.
- The bot switches GPU by rewriting `.env` (`GPU=`). For a provider that would leave
  `GPU_PROVIDER=vast` behind if the bot died mid-run.
- `bot.py` already uses "provider" for the **try-on** provider (`_CB_PROVIDER`, `_retry_tryon`).
- The picker is `_offer_run_confirm`; the stock-out card is `_deliver_provision_failure`;
  `_do_resume` re-launches `drain.py` for an already-confirmed batch.

## 3. Design

### 3.1 Provider follows the run, not `.env`

- `drain.py --provider vast|runpod` (default: `.env`'s `GPU_PROVIDER`). It exports `GPU_PROVIDER` to
  every child and passes an empty `POD_VOLUME` to `pod-provision.sh` only. The other scripts and the
  Makefile prefer the exported provider and derive an empty volume off RunPod (`scripts/lib-gpu-provider.sh`,
  `GPU_PROVIDER_EFF`/`POD_VOLUME_EFF`), which they resolve environment-first, then `.env`, then `vast`
  (the same order as `pod-provision.sh:21`). `.env` keeps `runpod` as the home value.
- `Lease.provider` (default `"runpod"` when absent, so leases already on the VPS stay valid) is the
  watchdog's source of truth.
- Makefile targets that read the provider with `grep .env` change to prefer `$(GPU_PROVIDER)` from the
  environment.
- **Correction (found while planning, 2026-09-19).** `pod-provision.sh` already prefers the environment
  for `GPU_PROVIDER` (line 21) and reads `POD_VOLUME` with `${POD_VOLUME-…}` (line 142), so it needs
  only `POD_VOLUME=` passed empty. The gap is elsewhere: `pod-wait.sh`, `pod-bootstrap.sh`,
  `pod-smoke.sh` and every Makefile target read **only** `.env` through their own `env_get`, so an
  exported override never reaches them and `pod-bootstrap.sh` would wire the RunPod volume onto a Vast
  box. Rather than teach each script "override to empty", a **Vast run never has a volume**: those
  scripts derive `POD_VOLUME` as empty whenever the effective provider is not `runpod`
  (`scripts/lib-gpu-provider.sh`, and `GPU_PROVIDER_EFF` / `POD_VOLUME_EFF` in the Makefile).
- The bot's `/kill` (`_do_kill`) also runs `make gpu-destroy` with the bot's own environment, which
  would destroy against `.env`'s provider. It must pass the lease's provider.
- `start_drain` (`tgbot/run.py`) forwards the provider; the bot never writes it to `.env`.
- Naming: in bot code the new concept is `gpu_provider`, to keep it apart from the try-on `provider`.

### 3.2 Renting: one atomic motion, ranked by time-to-ready

Measured: `vastai search offers` returns a random ~40-row sample (three identical queries intersect at
13/40; `--limit 1000` still returns 40), and pinning `OFFER=<id>` failed about 3 of 4 times because
offers vanish between search and create. So search + choose + create is **one function with a retry
loop**, using `--cancel-unavail` (error instead of a stopped instance that still bills) and
`--label motion-transfer`.

Selection is by **time-to-ready and total cost**, not by `$/h`:

1. **Filter** on offer fields: `inet_down ≥ VAST_MIN_INET_MBPS` (default 1000), `disk_bw ≥ 3000`
   (existing), `reliability`, `internet_down_cost_per_tb`, direct-port availability.
   *`inet_down` is necessary, not sufficient:* the Washington host advertised 1593 Mbps yet pulled a
   ghcr layer at 15.9 MB/s (≈12× below the advertised line) and took 556 s to reach `running`; the
   Bulgaria host advertised 2038 Mbps and took 325 s. Two data points — the default is an assumption
   to calibrate, not a finding.
2. **Rank** by `dph × estimated_ready_time + bandwidth_cost`, where bandwidth cost is
   `GB_to_download × internet_down_cost_per_tb` (a 50 GB boot is $2.00 at the Hungarian $40/TB,
   $0.07 at Bulgaria's $1.37/TB). A machine with a scoreboard entry uses its measured time; an unknown
   machine is scored as the slowest ever seen (556 s), never optimistically.
3. **Pull deadline.** If the instance is not `running` after `VAST_PULL_DEADLINE` (default 8 min), destroy
   it, blacklist the `machine_id`, and rent the next candidate. At most 2 retries, then a clear failure
   on Telegram. A partial pull is billed for bandwidth, which is why $40/TB hosts are excluded up front.
4. **Model-download probe.** Once running, ~15 s of `aria2c` on the first model file. The threshold is
   derived from the batch: models finish inside the bootstrap window (~200 s) when
   `MB/s ≥ GB_needed / 200` — ≈170 MB/s for 34 GB, ≈220 MB/s for 44 GB. Measured HuggingFace throughput
   is 242–296 MB/s. Below the threshold: continue if the total still fits 10 minutes, otherwise
   re-rent.
5. **Scoreboard** (`batch/vast-machines.json`; must be added to `.gitignore` beside
   `batch/pod-lease.json` and `batch/volume-migrate-lease.json` — it is not covered today): per `machine_id`
   `{pull_s, model_mbps, gb, measured_at, outcome}`. Slow machines are excluded for a period; fast ones
   are asked for directly by `machine_id` query (deterministic, unlike a random-sample search). Every
   real rental adds a data point.

Other facts to carry in code: use the direct `ip:port` from `vastai ssh-url`, with
`-o IdentitiesOnly=yes -i <key>` — the `sshX.vast.ai` proxy rejected the registered key while the direct
address accepted it. `vastai show instances` is deprecated; use `show instances-v1`.

Host layer cache: 325 s cold vs 35 s warm on the same machine (`machine_id` 144253). How long it
persists is **unproven** (longest gap tested ≈ 10 min).

### 3.3 Models per batch

A data registry `stage (+ params) → {catalog ids, extra setup steps}`, sizes from
`motions-studio/comfyui/catalog-motion-transfer.json`:

| Need | Catalog ids / step | Size |
|---|---|---|
| `motion`, `camera-motion` | Wan 2.2 Animate group (incl. vitpose, yolo) | ≈34.4 GB |
| `enhance` with FlashVSR | 4 `flashvsr-*` files | ≈9.3 GB |
| `enhance` with Lanczos | none extra | 0 |
| `character-swap` | `swap-sam3`, `swap-scail2-unet`, `swap-scail2-umt5-fp8`, `swap-scail2-lightx2v-r64`, `swap-scail2-dpo` | ≈28.3 GB |
| `faceLock` on | `pod-facelock.sh` (insightface swap) — **not in any catalog** | small |
| `faceLockRestore` on | the above plus CodeFormer (`facerestore_cf` node + model) | small |

`camera-motion` turns `faceLock` on by default (`batchlib/pipelines.py`), so the model set is catalog
ids **plus setup steps**. `faceLockRestore` was a default too until 20/09/2026, when
`faceLockDetailKeep` replaced it — a box provisioned for camera-motion no longer needs CodeFormer
unless a manifest asks for it. DWPose and RIFE self-download on first use.

- `pod-bootstrap` starts the model download for the manifest's set **in parallel** with the backend
  install. Measured 2026-09-19: 34 GB in 137 s, in parallel with a 200 s bootstrap, so the download is
  hidden behind bootstrap when throughput ≥ the threshold in 3.2.
- Without a volume, `preload-models.sh` only needs `[ -d "$POD_VOLUME" ]`; on Vast it targets
  `COMFY_DIR/models`, which is a plain directory. Use `--id` per catalog entry.
- A gate, `make check-vast-models`, fails when a stage in `PIPELINES` has no registry entry or a
  registry id is not in the catalog. Same drift class `check-job-types` guards.
- A manifest containing a stage with no registry entry gets **no spend button** for Vast (see 3.5),
  instead of a job that sits `queued` — the repo's most common silent failure.

### 3.4 Teardown net covers both providers

- `podctl.VastCtl` implementing `PodControl` (`list_pods`, `destroy`). `list_pods` returns every
  instance with its label as `PodInfo.name`; it is `reconcile` and `DESTROYABLE_NAMES` in the watchdog
  that limit destruction to instances labelled `motion-transfer`. `destroy` pipes `y` (the CLI asks `[y/N]` and exits 0 without
  deleting on EOF) and the caller re-lists to verify — `pod_watchdog.destroy_verified` already does
  this through the protocol.
- The watchdog holds `provider → PodControl`. Tiers 1–2 pick the control from `lease.provider`. Tier 3
  lists **both** providers and kills labelled orphans; if one provider's listing fails, the other is
  still scanned and the failing one is skipped for that tick (not seeing is not the same as nothing
  being there).
- Vast has no `--stop-after`; the lease's `abs_max_min` is the only hard ceiling, so `drain.py` writes
  the lease right after the rent, as today.
- **Diagnostics.** On RunPod a failed job survives `gpu-destroy` because the database lives on the
  volume. On Vast the database dies with the box. `teardown()` already runs `collect_diagnostics`
  before the destroy when a stage failed; that is the only post-mortem path on Vast and the docs say so.

### 3.5 Bot: provider choice in the picker

- `_offer_run_confirm` gets a first row `[RunPod ✓] [Vast]`. RunPod stays the default.
- The Vast panel shows: the 5090 offer, `$/h`, estimated bandwidth cost for **this manifest's** GB, the
  estimated session total, and cold start — the measured figure for a scoreboard machine, otherwise
  "not measured, 5–9 min (2 data points)". No datacenter or stock lines; "Switch GPU type" and
  "Other regions" are hidden. v1 searches only the configured GPU (5090).
- The provider travels in the **callback data**, not in `.env` or state: `run:go:<token>:vast`, and
  Refresh and submenus carry the same suffix. This fits `_run_token` (a button describes itself; stale
  buttons die when the manifest changes) and survives a bot restart. The suffix fits Telegram's 64-byte
  limit.
- Branches that only make sense for RunPod are skipped on Vast: `_do_resume`'s `migration_running()`
  guard, `home_dc`, `POD_VOLUME_ID`.
- The stock-out card gets a "Rent on Vast" button that opens the same panel. Existing buttons (Wait,
  Retry, Migrate, Subscribe) stay.
- Both renderings of the panel (before Phase A, and the post-Phase-A rent panel) carry the choice; the
  order stays try-on first, GPU rental second.
- The Vast tab always renders; its spend button is hidden with a concrete reason when a condition
  fails: a manifest stage missing from the model registry, `vastai` not logged in or balance too low
  (`batchlib_ext/vast_account.py`, modelled on `runpod_account.py`), or no machine passing the filters.
- Progress text gains the provider and the running estimated cost.

**As built (Plan 4, 2026-09-19), where it differs from the above:**
- The RunPod tab is its own callback, `run:rp`, not `run:back`: Back insists on a drafted job in
  memory (`_STATE`), which the rent panel drawn after Phase A may no longer have while the bot is
  still up (the draft itself is persisted across a restart).
- The try-on confirm that precedes Phase A has no provider row: that tap rents nothing, and the GPU
  is chosen on the rent panel drawn after the try-on ("both renderings carry the choice" above means
  the two stock screens).
- A Vast panel does not survive a bot restart: the tap-time gate needs a price quote from the running
  process, so an old spend button refuses until the panel is drawn again (the spec's "survives a bot
  restart" holds for the callback data, not for the quote).
- A job queued onto a Vast pod that is already running is checked before it is written into the
  mailbox (the write is the queueing): enabled pipeline, registered stages, and every model already
  on the pod, which only has the first manifest's models.
- The quote is `VAST_QUOTE=1 bash scripts/pod-provision.sh` (`vast_rent.py --quote`), so the search
  filters come from the one place that derives them; the bot never re-derives them.
- "One measured session per pipeline family before its button is enabled" is the
  `VAST_ENABLED_PIPELINES` list, and the mechanism ships empty by default (an operator sets it per
  pipeline). `.env.example`'s own default was changed the same day, on explicit user request, to list
  every pipeline `PIPELINES` currently has — ahead of a measured session for five of the six — so the
  gate is enforced by the code but not, for this deployment, by the recommended rollout order.
- A RunPod spend button also names its provider explicitly in its callback data (`run:go:<token>
  :runpod` etc., same shape as Vast's `:vast`) — added after review found the original bare button
  fell back to `.env`'s `GPU_PROVIDER` on the tap, so a host misconfigured with `GPU_PROVIDER=vast`
  could rent Vast from a button labelled RunPod. A button minted before this fix keeps its old,
  suffix-less meaning.
- The spend handlers re-check the hidden-button conditions at tap time, before the manifest is
  rewritten, so a refusal does not invalidate the panel's own buttons. The marketplace search is
  replaced by a price quote for this batch's download size fetched in the last ten minutes.
- `pod-provision.sh` translates the RunPod GPU name in `.env` to Vast's spelling (`VAST_GPU`); the
  bot's `.env` holds the RunPod one, and a search with it failed outright.
- The progress and `/kill` text price a Vast pod at the rate quoted on the panel, or give time only.

## 4. Testing

Free, no rental:
- `VastCtl` with a fake CLI (as `test_batch_podctl.py`), including the `[y/N]` and re-list-verify cases.
- `Lease` round trip and reading an old lease without `provider`.
- Watchdog with two providers, one failing its listing.
- Selection: ranking, scoreboard preference, blacklist, pull deadline, retry cap — with recorded offer
  JSON, no network.
- Model registry, and `check-vast-models`.
- Bot callbacks: `run:go:<token>:vast`, stale token, hidden spend button reasons.
- Existing gates: `make batch-test`, `check-job-types`, `check-batch-params`, `scrub-secrets.sh --check`.

Paid, before the Vast spend button is enabled for a pipeline family (≈ $0.3–0.6 each):
1. `tryon-camera-motion-enhance` with FlashVSR — confirms the model list, download time, and whether
   faceLock works on a Vast box.
2. `character-swap-enhance`.
Both write to the scoreboard, and a few more machines are needed to calibrate `VAST_MIN_INET_MBPS`.

## 5. Not proven — stated so nobody assumes it

- How long a host keeps its docker layer cache; how often a known machine is rentable again.
- Loss of the host in the middle of a job.
- Every job type other than `motion` on Vast (only `motion` has run there).
- The `VAST_MIN_INET_MBPS` default and the 8-minute pull deadline (both derived from two hosts).

## 6. Alternatives rejected

- **A separate Vast lane** (`drain_vast.py` plus a second watchdog): faster to write, but duplicates the
  teardown logic — the drift class `check-job-types` exists for.
- **Self-destruct on the instance** using an API key placed on the rented box: Vast has no
  `--terminate-after`, a dead box cannot destroy itself, and it puts a secret on a machine we do not own
  (the rule so far: presigned URLs only).
- **Automatic failover:** rejected in chat. Price per hour differs and bandwidth is billed per TB, so a
  tap is a real spend decision.
- **R2 as a model store for v1:** not needed. HuggingFace measured 242–296 MB/s against R2's ≈200 MB/s
  on the same host, and the runner already downloads outputs through `download_output`.

## 7. Out of scope (v1)

Choosing GPU types other than the configured 5090 on Vast; rebuilding the stale prebuilt image
(`sha-0cbe433` reinstalls `node_modules` at boot); masking `NUXT_MOTION_API_KEY` in bootstrap output
(it prints the key and mails connection details — an existing leak, tracked separately);
`DS_DEFAULT_JOB_TYPES` drift in `gpu-preflight`.

## 8. Suggested order of work

1. Lease + `VastCtl` + watchdog (safety first: nothing rents before the net exists).
2. `drain.py --provider` and the Makefile / env-empty-override fix.
3. Rent function, scoreboard, pull deadline, download probe.
4. Model registry, parallel download in bootstrap, `check-vast-models`.
5. Bot panel and callbacks.
6. The two paid sessions; enable the button per pipeline family; update `docs/gpu-pod.md`.
