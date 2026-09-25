# VPS control-plane API (shared by the Telegram bot and an iPhone app) — design

Date: 2026-09-21 · Status: approved in chat; pending written-spec review · Supersedes nothing

The goal is a native SwiftUI iPhone app that does what the Telegram bot does: upload material, build
and run jobs, watch progress, view outputs, manage the pod. This spec covers **sub-project 1 of 2**:
the HTTP API on `motion-vps` that the app talks to. The SwiftUI app is sub-project 2 and gets its own
spec; this document fixes only the contract the app will consume.

Function names are the durable reference; line numbers drift.

---

## 1. What was asked, and what was decided

- App v1 scope: **all four** areas — progress + outputs, create + run jobs, material management,
  pod / GPU / cost.
- **The VPS is the control plane.** The app never talks to the pod. The pod exists only during a work
  session (`make gpu-destroy` is the default), so an app wired to the pod's Express API would need a
  rented GPU just to open. The bot already lives on the VPS and already owns the pod lifecycle
  (`drain.py` + lease + `pod_watchdog.py`); the app joins it there.
- **Bot and app share one core.** No logic is reimplemented in Swift or duplicated in a second
  Python body.
- **Shared runs, separate drafts.** Runs, progress, outputs, materials and the pod are global (there
  is one pod). The job being composed — slots, pipeline, provider, the accumulating batch — is per
  client, so the app and Telegram never overwrite each other's half-built job.
- **Ingress is Cloudflare Tunnel**, the same mechanism the pod already uses.
- **No $99 Apple Developer account for now.** Consequences accepted: free provisioning (the app
  expires every 7 days and is reinstalled from Xcode), no APNs. **Telegram stays the notification
  channel**, which is why shared runs matter: a run started from the app still reports its progress
  and completion in Telegram.

## 2. What the code already does

- `scripts/tgbot/bot.py` (6,762 lines on 2026-09-21) mixes Telegram rendering with the domain logic:
  staging, drafts, Phase A, the rent panel, the money gate, kill/resume, GPU stock, migration.
  Per-chat state is keyed by `chat_id` (`_STATE`, `batch/tg-<chat_id>-*` files).
- `scripts/tgbot/job.py`, `ingest.py` and `run.py` are already Telegram-free.
- **The bot process owns running work in memory.** `run._RUNNING` and `run._PHASE_A` hold the
  `Popen` handles of drains and Phase A runs; `bot._MIGRATE_PROC` holds migrations. `tick_progress`,
  `tick_phase_a` and `tick_migration_progress` watch them from the poll loop; `_do_kill` signals
  their process groups. Progress survives a bot restart through files on disk
  (`_progress_path`), not through memory.
- **The money gate is one body.** `_do_confirm` is the only fresh-spend entry point and `_do_resume`
  the only other caller of `start_drain`; `grep -rn "start_drain" scripts/tgbot/bot.py` must show
  exactly those two call sites. `_run_token` invalidates stale panel buttons.
- The run flow the app must follow is the bot's:

  ```
  draft → [Run] → Phase A (local try-on; spends Gemini/Qwen quota, rents nothing)
        → try-on previews (per-image regenerate) → rent panel (GPU, RunPod/Vast, stock + price)
        → Confirm → drain (or queue onto the live pod's mailbox) → progress → outputs
  ```

- `scripts/**` pushed to `main` auto-deploys to `motion-vps` through GitHub Actions.
- `motion-vps` is a DigitalOcean `s-1vcpu-1gb` with a 2 GB swapfile (`scripts/vps/README.md`).

## 3. Approaches considered

| | Approach | Verdict |
|---|---|---|
| **A** | HTTP server as a thread **inside** the `motion-bot` process; logic extracted into a Telegram-free package both adapters call | **Chosen** |
| B | A separate daemon owns every run; the bot and the API are both HTTP clients of it | Rejected: rewrites all process ownership, including the money gate, for no user-visible gain |
| C | A separate API process coordinating with the bot through files on disk | Rejected: two processes able to rent a pod need a cross-process lock; killing a run started by the other process goes through process groups; the bot must adopt runs it did not start. Every one of those is a place to lose money |

A keeps `_RUNNING`, the lease and the double-rent guards in one process, so "shared runs" falls out
without new machinery. It also keeps the path to B open: once the core is its own package, moving it
into its own process later is a relocation, not a rewrite. Cost: a bot crash takes the API down with
it (systemd `Restart=always` brings both back).

## 4. Architecture

```
iPhone app ──HTTPS──▶ Cloudflare Tunnel (hostname → VPS 127.0.0.1:8787)
                                    │
             ┌──────────────── motion-bot process ────────────────┐
             │  tgbot/ (Telegram adapter)   httpapi/ (HTTP adapter)│
             │            └────────── control/ (core) ──────┘      │
             │   drafts · materials · runs · pod · outputs          │
             └───────────────────────────┬─────────────────────────┘
                   batchlib · drain.py · lease · pod_watchdog (unchanged)
```

### 4.1 `scripts/control/` — the core

No Telegram imports. One module per responsibility:

| Module | Owns |
|---|---|
| `drafts.py` | The job being composed, per owner: slots, pipeline, provider, the batch of jobs |
| `materials.py` | Staging, probing, HEIC→PNG, pruning, thumbnails |
| `runs.py` | Validate, Phase A, try-on regenerate, rent-panel data, **confirm**, progress, kill, resume |
| `pod.py` | Lease state, GPU stock, balance, Vast, migration |
| `outputs.py` | Listing `out/*/_final` and resolving a file inside it |
| `tryon_library.py` | Saved try-on entries, per owner (§5.10, slice 6): list, save, delete, and the image each one points to |

- **State is keyed by `owner: str`, not `chat_id`.** The bot uses `owner="tg-<chat_id>"`, the app
  `owner="app"`. Existing on-disk file names (`batch/tg-<chat_id>-*`) do not change for the bot, so no
  migration of live state is needed.
- **Refusals are return values.** Every `tg.send_message(chat_id, "<reason>"); return` in a gate
  becomes `return Refusal(code, message)`. The Telegram adapter sends `message` verbatim (no visible
  change in Telegram); the HTTP adapter maps it to a status code.
- **Shared runs.** When the app starts a run (Phase A or a drain), the core also registers progress
  for the Telegram chat of `TG_ALLOWED_USER_ID`, through the same `_start_progress` path, so
  Telegram shows progress and delivers the result exactly as for a run started there.

### 4.2 `scripts/httpapi/` — the HTTP adapter

- Started as a daemon thread from `bot.py`'s `main()`, bound to `127.0.0.1:8787` only.
- A thin layer: parse, authenticate, call `control/`, serialise. No domain logic.
- Stdlib `http.server` (`ThreadingHTTPServer`) unless a slice proves it insufficient; no new
  dependency on a 1 GB box without a reason.
- If the thread fails to start (port taken, bad config) the bot keeps polling and sends one
  Telegram message saying the API is down.

### 4.3 Concurrency

The bot is single-threaded today; nothing guards `_STATE`, `_RUNNING` or the progress files against
a second thread. The HTTP thread breaks that assumption, so:

- One module-level `control.LOCK` (`threading.RLock`) wraps every core read or write of state, and
  each `tick_*` round in the bot loop.
- Slow work runs **outside** the lock: chunk assembly, ffmpeg probes, thumbnail generation, file
  streaming.
- Holding the lock across `confirm` is acceptable: `Popen` returns in milliseconds, and it is exactly
  the section that must not interleave (two confirms must never both see `drain_running() == False`).

### 4.4 Ingress and authentication

- `cloudflared` runs as a systemd service on the VPS and maps one hostname to `127.0.0.1:8787`.
- Two layers, each sufficient to reject on its own:
  1. **Cloudflare Access service token.** The app sends `CF-Access-Client-Id` and
     `CF-Access-Client-Secret`; requests without them are rejected at Cloudflare's edge and never
     reach the VPS.
  2. **Bearer token** checked by the API (`Authorization: Bearer <CONTROL_API_TOKEN>`,
     constant-time compare), in case the Access policy is ever misconfigured.
- Secrets live in the VPS `.env` and in the iPhone Keychain. Never in the repo;
  `motions-studio/setup/scrub-secrets.sh --check` must exit 0.
- **Cloudflare's free plan caps a request body at 100 MB.** Uploads are therefore chunked (§5.1).

## 5. API contract (v1)

All under `/v1`, JSON bodies, errors as `{"error": {"code": "...", "message": "..."}}`.

### 5.1 Materials (global)

| Method + path | Behaviour |
|---|---|
| `POST /v1/uploads` `{file_name, size}` → `{upload_id, chunk_size}` | Open a chunked upload. `chunk_size` = 32 MB |
| `PUT /v1/uploads/{id}/chunks/{n}` | Idempotent; re-sending a chunk overwrites it |
| `GET /v1/uploads/{id}` | Which chunks are present, so an interrupted upload resumes |
| `POST /v1/uploads/{id}/complete` → `Material` | Assemble, HEIC→PNG (`to_png_if_heic`), probe, stage (`_stage_file` semantics). Probe includes kind, resolution, duration and the quality warning the bot shows |
| `GET /v1/materials` | List. *Amended 2026-09-25:* each item carries `role` (`driver`/`character`/`outfit`/`background`/`null`) from `control/material_roles.py`: a tag (written when a draft slot takes the material, or set by hand), else the role it filled in a rendered `batch/*.yaml` manifest, else `driver` for a video |
| `PUT /v1/materials/{id}/role` `{role}` → `{material}` | *Added 2026-09-25.* Tag a material's role; `null` drops the tag so history decides again. `400` for an unknown role, `404` outside staging. Deleting a material drops its tag |
| `GET /v1/materials/{id}` | *Added 2026-09-25.* The file itself, with Range (`send_file`), so the app can play a video material rather than only its poster. `404` outside staging, like `/outputs` |
| `GET /v1/materials/{id}/thumb` | ffmpeg poster frame / downscaled image, cached on disk |
| `DELETE /v1/materials/{id}` | `409` if a draft, a queued job, or a live run references it. *Amended 2026-09-25:* any owner, not just `app` — `409 in_use` now covers every `batch/*.draft.json` (the app's and each Telegram chat's, which was the reason non-app owners used to be `403`) and every mailbox file `batch/*<MAILBOX_SUFFIX>` (`<name>.next.yaml`, a job queued behind a running drain — its draft is already cleared and it is not yet `busy()`, so it needs its own unconditional check) |
| `POST /v1/materials/link` `{url}` → `Material` | *Added 2026-09-25.* Download a TikTok video with the bot's own `tgbot/tiktok.py` (yt-dlp, then tikwm) and stage it as an app material; same response shape as `complete`. `400 bad_request` for anything that is not a TikTok link, `409 busy` while another link downloads, `502 download_failed` with TikTok's reason, `422 unprobeable` for a video-less file. Held open for the whole download (40 s budget per path, under Cloudflare's 100 s cut); not idempotent — each call is a fresh download |

Unfinished uploads older than 24 h are deleted by `_tick_staging_prune`, extended to cover the
uploads directory (it currently only ages out staged files after `STAGING_MAX_AGE_DAYS`).

### 5.2 Drafts (per owner; the app is `owner="app"`)

| Method + path | Behaviour |
|---|---|
| `GET /v1/pipelines` | Pipelines with required/optional roles and providers, derived from `batchlib.pipelines.PIPELINES`/`STAGES` — the app never hardcodes a pipeline |
| `GET /v1/draft` | Current job, the batch so far, missing slots, the "next step" line |
| `PATCH /v1/draft` `{pipeline?, provider?, slots?: {role: material_id}, tryon_seed?: id \| null}` | Same rules as `_switch_pipeline` / `_switch_provider` / `_fill_slot`. `tryon_seed` names a try-on library entry to seed the job's try-on stage from (§5.10, slice 6) |
| `POST /v1/draft/add-to-batch` | `_add_to_batch` — already accepts any number of jobs; a "1 character × N outfits" cross build is the app calling `PATCH` + this once per outfit (§5.10) |
| `DELETE /v1/draft/batch/{digest}` | `_drop_from_batch` — free (nothing rented yet), so this is how the app drops one try-on from a batch before renting (§5.10) |
| `POST /v1/draft/clear` | `_clear_job` |
| `POST /v1/draft/validate` | `make batch-validate` — free, no pod |

### 5.3 Runs (global)

| Method + path | Behaviour |
|---|---|
| `POST /v1/runs/phase-a` → `Run` | `_do_phase_a` for the app's draft — already runs every job in the draft's basket, not just one (§5.10) |
| `POST /v1/runs/{id}/tryon/{index}/regen` `{provider?, guidance?: string[]}` | `_regen_tryon` / `_retry_tryon`. `guidance` is zero or more of `keep_face`, `tighter_crop`, `match_lighting` (§5.10, slice 6); unknown values are `400` |
| `GET /v1/runs/{id}/tryon/{index}` | The current try-on preview image |
| `GET /v1/runs/{id}/tryon/{index}/versions/{n}` | An earlier version of that image, oldest = 1 (`_tryon_versions`, already kept by every regenerate — §5.10) |
| `GET /v1/runs/{id}/rent-panel` | Per-GPU stock and price at the home datacenter, RunPod and Vast, Vast refusals, and a `panel_token` |
| `POST /v1/runs/{id}/confirm` `{gpu, provider, panel_token}` | **The money gate** (§5.5) |
| `GET /v1/runs` | Runs, newest first, from the on-disk journals |
| `GET /v1/runs/{id}` | Stages, per-job state, the lease's `provisioned_at` (epoch; the client computes elapsed), cost estimate (slice 5). Read from `state.json` (`progress_text`'s source), so it stays true after the pod is gone. Supports `ETag` / `If-None-Match`, compared weakly because Cloudflare turns ETags into `W/"…"` when it compresses. The body must hold no field derived from the current time, or the ETag changes on every poll |
| `POST /v1/runs/{id}/kill` | `_do_kill`, run on a worker thread; `202` at once (§5.9) — kills every job left in a batch run, not only one, since one manifest has one drain |
| `POST /v1/runs/{id}/resume` `{provider, run_token, gpu?}` | `_do_resume`, only for a run whose pod rental failed (§5.9) |

A run's `id` is the manifest stem (`batch/<id>.yaml`).

### 5.4 Pod, GPU, outputs

| Method + path | Behaviour |
|---|---|
| `GET /v1/pod` | Lease (provider, GPU, since, quoted $/h), the selected GPU, migration state, the last kill. Files and memory only — no network, so it is cheap to poll |
| `GET /v1/gpu/stock[?force=1]` | `_report_gpu_stock`'s data: per-GPU stock and price at the home datacenter, and other regions |
| `GET /v1/balance[?vast=1]` | RunPod prepaid balance and runway hours; the Vast credit only when asked (§5.9) |
| `PUT /v1/pod/gpu` `{gpu}` | Sets `.env`'s `GPU` to one of the bot's five |
| `POST /v1/pod/migrate/ask` `{to_dc}` | Step one of the two-step confirm: checks, then returns a single-use `confirm_token` |
| `POST /v1/pod/migrate` `{to_dc, confirm_token}` | Step two: `_start_migration`. **Deletes the source volume once the copy verifies** |
| `GET /v1/outputs` | Batches in `out/*/_final` with their files |
| `GET /v1/outputs/{batch}/{file}` | Streams the file with HTTP `Range` support so `AVPlayer` plays without a full download |
| `GET /v1/outputs/{batch}/{file}/poster` | A 480 px JPEG frame of the file, rendered once with ffmpeg (added 2026-09-25) |

Every path parameter that names a file goes through `_safe_child`; `..`, absolute paths and symlinks
out of the root are `404`.
Symlinked batch directories under `out/` (the runner's `out/latest`) are not batches: they are left out of
`GET /v1/outputs` and refused by the file route, so the newest batch never appears twice.

**Posters (2026-09-25).** The app's Outputs grid shows a frame per file instead of file names that
differ only at the end. Posters are rendered server-side through the same two ffmpeg slots as material
thumbnails (`materials.render_frame`) and stored in `out/<batch>/_final/.posters/`, beside the files:
deleting a batch directory deletes its posters, and the bot's daily prune sweep
(`outputs.prune_posters`) removes the poster of a single file deleted by hand. A subdirectory, not loose
`<name>.jpg` files, because a `.jpg` in `_final/` is itself an output. Each video entry in
`GET /v1/outputs` also carries `duration` (seconds, `null` when unprobeable), probed once per file
version and cached as `.posters/<name>.json`. Measured locally on a 36 MB, 15 s output: poster 22 KB in
0.15 s; the cached listing reads in under 10 ms. `updated_at` is now the newest file's mtime rather than
`_final/`'s, since creating `.posters/` touches the directory and would reorder old batches to the top.
An earlier version (#75) made posters on the phone with `AVAssetImageGenerator`; it worked but left
nothing tied to the file's lifetime.

### 5.5 Protecting money

A mobile client retries. A duplicated `confirm` must never become a second pod.

- `confirm` requires the `panel_token` from the most recent `rent-panel` read — the same idea as
  `_run_token`. A stale token is `409 stale_panel`; the app re-reads the panel.
- `confirm` requires an `Idempotency-Key` header. The server records `(key → response)` for 24 h on
  disk; a replay returns the stored response and calls nothing.
- `confirm` runs **the same `_do_confirm` / `_do_resume` bodies** the bot uses. There is no second
  gate. *Amended 2026-09-21 (slice 4):* the bodies stay in `bot.py` rather than moving to
  `control/runs.py` — 58 tests patch `tgbot.bot.start_drain` and 120 patch `tgbot.bot.drain_running`,
  and a moved body would silently escape those patches. The grep invariant is therefore:
  `start_drain` has exactly two call sites, both in `scripts/tgbot/bot.py` (`_do_confirm`,
  `_do_resume`), and none in `scripts/control/` or `scripts/httpapi/`. See §5.8.
- `phase-a` and `regen` spend Gemini/Qwen quota, so they take an `Idempotency-Key` too.

### 5.6 Status codes

| Code | When |
|---|---|
| `401` | Missing or wrong bearer token |
| `404` | Unknown id, or a path outside its root. A draft PATCH whose `tryon_seed` names a deleted library entry is `404 seed_not_found`, distinct from a gone material's `404 not_found` |
| `409` | World-state refusal: migration in progress, a run already live, Phase A still running, stale `panel_token`, material in use |
| `502` | `runpodctl` (or another upstream) could not answer a read the app asked for — never for a spend or a kill. Also `download_failed` from `POST /v1/materials/link` (TikTok refused), which the app shows with the server's text rather than the provider headline |
| `422` | Draft refusal: missing slots, validation failed, unanswered files |
| `500` | Unexpected exception — logged, and **never** propagated into the bot's poll loop |

*Amended 2026-09-25:* domain error codes map to statuses through `_DOMAIN_STATUS` in
`scripts/httpapi/server.py`, and **an unmapped code becomes `400`** — so a new code needs a table
entry, not just a raise. `seed_not_found` was added this way; see
`2026-09-25-seed-not-found-and-owner-coupling-design.md`.

### 5.7 Progress on the phone

The app polls `GET /v1/runs/{id}` every 5 s while in the foreground, using `ETag`. No SSE: Cloudflare
drops idle connections after ~100 s, and the app cannot hold a connection in the background without
push anyway. When the app is closed, Telegram reports.

### 5.8 One run slot (slice 4, decided 2026-09-21)

Mapping the code for slice 4 showed that a separate app run (`batch/app.yaml`) is unsafe:
`drain_running` is per manifest, so an app confirm during a Telegram drain would neither refuse nor
queue — it would provision a **second pod**, while the lease file, `.env`'s `GPU_INSTANCE_ID` and
`gpu-destroy` can each hold only one. The user chose **one run slot, shared**:

- Drafts stay separate (slice 3). **Runs are one slot:** Phase A and confirm from the app write the
  app draft's jobs into the Telegram chat's manifest, `batch/tg-<TG_ALLOWED_USER_ID>.yaml`, and go
  through the bot's own `_do_phase_a` / `_do_confirm` / `_do_resume` / `_regen_tryon`. Progress,
  try-on previews, the rent panel, `/kill` and `/status` in Telegram therefore work unchanged for a
  run started from the phone, and a confirm while a drain is live queues onto that same pod through
  the existing mailbox.
- Those functions return an outcome instead of `None`: a refusal carries a code and the same text the
  bot sends. A call made for the app does not send its refusal to Telegram (the phone shows it);
  successes (`Started`, progress) still post to Telegram. The Telegram draft (`_STATE`) is untouched
  by an app call; the app draft is cleared when its jobs start or queue.
- A `BOT_LOCK` (re-entrant) is held by the bot loop around each `handle()` and each tick round —
  never across `getUpdates` — and by the HTTP thread around every call into these functions. The
  HTTP side waits at most 60 s for it and then answers `503 bot_busy`.
- `{id}` in the run routes must be the live slot's id, `tg-<chat>`. The `panel_token` is the
  manifest's mtime (the bot's `_run_token`) joined with the app draft's generation.
- Idempotency records are written as `pending` **before** the call; a replay of a key whose call
  crashed answers `409 outcome_unknown` rather than risk a second rental.
- Not in slice 4: choosing the RunPod GPU type (`.env`'s `GPU`, global — slice 5), and retrying a
  try-on with a different provider.

### 5.9 Pod, kill and migrate (slice 5, decided 2026-09-22)

Nothing here spends new money except `resume`, which is `_do_resume` — the same body `confirm` reaches
after Phase A — and `migrate`, which rents two temporary CPU pods and then **deletes the volume that
holds the models, Postgres and MinIO**. Both sit behind the slice-4 machinery (`BOT_LOCK`,
`Idempotency-Key`, a token that goes stale).

- **`kill` never runs `make gpu-destroy` unless something is live.** `_do_kill` itself does not check:
  Telegram's `_ask_kill` does, one step earlier. Called with nothing running, `_do_kill` would destroy
  whatever pod `.env` happens to name. The app path repeats `_ask_kill`'s predicate (`drain_running` or
  `phase_a_running`) **inside** the worker, under `BOT_LOCK`, immediately before calling `_do_kill` —
  a drain can finish in the seconds between the request and the worker getting the lock.
- **`kill` answers `202` and works on a thread.** `_do_kill` waits up to 30 s for the drain to die and
  up to 180 s for `make gpu-destroy`; Cloudflare closes a request at ~100 s. The thread holds `BOT_LOCK`
  for the duration, exactly as the bot's own loop was blocked by it before. One kill at a time
  (`409 kill_in_progress`). The result is `GET /v1/pod`'s `last_kill`; a destroy that could not be
  verified is `ok: false, code: destroy_unverified` and is also posted to Telegram, because a pod may
  still be billing.
- **`resume` is for a failed rental only.** It requires an outstanding `provision-failed.json` for the
  run (the condition Telegram's recovery buttons are drawn under — without it, resuming a finished
  batch would rent a pod to do nothing), the run's current `run_token`, and — since 2026-09-24 — an
  unchanged draft. `confirm` stamps the app draft's `generation` when it is accepted
  (`batch/tg-<chat_id>.confirmed-generation.json`, `_kill_result_path`'s convention) — read after the
  confirm's own `clear()` of that draft (§5.8), so the clear does not itself trip the gate — and
  `resume` refuses `409 stale_run` when the draft has moved since, because it re-rents the manifest on
  disk and a draft edit rewrites no manifest. `_run_token` is the manifest's `mtime_ns`, so it cannot
  see one; this is the gap `panel_token` already closes for `confirm` by joining `.generation`.
  **Fails open when no stamp exists** — a Telegram-initiated confirm writes none, and `_do_confirm`
  must not write one, because it is shared with the Telegram flow where the app's draft is not what
  the user reviewed. So the latch covers app-initiated confirms only. **Fails closed on a draft this
  box cannot parse**, which is the case a phone-side reader cannot diagnose from the screen: `_load`
  quarantines the file as `<name>.<uuid>.bad` — moved aside, never deleted, since it is the only copy
  of what the user composed, and a unique suffix so a second corrupt file cannot overwrite the first —
  and returns generation 0, so the stamp can no longer match and `resume` answers the same
  `409 stale_run`. Nothing in the draft *appears* to have changed, because the draft the app last read
  is no longer the file on disk; the refusal is the only observable. No route and no request or
  response field changed, so §5.3's table does not move, and that is why both deploy orders are safe:
  an old app build is protected by the server, and a new build works against an old server. `provider`
  is required, as on `confirm`. `gpu`, when sent, must equal `.env`'s `GPU` or the answer is
  `409 stale_panel`; the same optional `gpu` is now checked on `confirm` — the price the app showed was
  for one GPU.
- **`GET /v1/balance` keeps the Vast credit opt-in.** `vast_credit()` is a ~30 s subprocess; the RunPod
  balance is one `runpodctl` call. `GET /v1/pod` is therefore free of both.
- **Migration is guarded more tightly than Telegram.** `ask` refuses (`409`) while a migration is
  running, a lease is live, or the chat's run is busy (Telegram lets a migration start under a live
  drain), when `to_dc` is the home datacenter, or when `to_dc` is not a datacenter the stock check
  currently lists (`502` if the stock check itself fails: the check fails closed). It returns a
  `confirm_token` bound to `to_dc` and the volume id, valid for 10 minutes, single use, held in memory
  (a bot restart voids it — the user asks again). `migrate` consumes the token and re-checks every guard
  under `BOT_LOCK` before `_start_migration`. Progress stays in Telegram (`tick_migration_progress`);
  the phone reads the same progress file through `GET /v1/pod`'s `migration`.
- **No test and no live check ever calls the real `volume_migrate.py` or `make gpu-destroy`.** The
  invariant test greps `scripts/control/` and `scripts/httpapi/` for both.
- Cost: `GET /v1/runs/{id}`'s `lease` gains `quoted_usd_per_hr` — the flat $0.99 the bot's own `/kill`
  text uses for RunPod, `null` for Vast (its lease does not carry the offer's price). A quote, not the
  invoice; it is a constant, so the ETag rule of §5.3 still holds and the client computes
  `elapsed × rate` itself.
- Not in slice 5: switching the *provider* on a resume from the phone (only RunPod's recovery button
  and the panel's Vast tab exist in Telegram; `provider` on `resume` picks between them), `/subscribe`
  stock watches, and cancelling a running migration.
- Also not covered: a rental confirmed from **Telegram** and retried from the phone is unstamped and so
  unchecked, and the stamp is per chat, so one slot's confirm overwrites another's. That matches the
  one-run-slot model every other `tg-<chat_id>.*` record uses.

### 5.10 Batch cross-mode, try-on library and guided regenerate (slice 6, decided 2026-09-22)

Triggered by mapping a SwiftUI prototype's screens (a Claude Design canvas, 17 artboards) onto the
API slices 1–5 already ship. Most of what the prototype's batch screens need turned out to already
exist; two pieces genuinely do not.

**§5.8's "one run slot" undersold what it built.** `_do_phase_a` and `_do_confirm` already take an
optional `jobs: list[Job]` that is *the app's whole draft basket*, not one job — `AppRuns.phase_a` and
`AppRuns.confirm` already pass `self.drafts.runnable()`'s jobs straight through. A manifest with N runs,
per-job progress (`run_detail`'s `jobs` array), one drain, and one kill for the whole batch were true
from slice 4 onward. Slice 6 adds no new machinery for this — it is the app actually driving what was
already there:

- **Cross build** (1 character × N outfits × 1 driver → N jobs): the app calls `PATCH /v1/draft`
  once per outfit (character and driver slots unchanged) followed by
  `POST /v1/draft/add-to-batch`, exactly as composing one job N times. No new route.
- **Drop before renting** (a bad try-on out of a kept batch): `DELETE /v1/draft/batch/{digest}`
  already removes a job from the basket, but that alone does not do what the prototype's `BatchTryon`
  screen needs. Once Phase A has run, `AppRuns.confirm` takes the *resume* branch
  (`_PHASE_A_OFFERED` matches the manifest's token), and `_do_resume` rents the manifest **already on
  disk** — it does not re-read the draft, so a job dropped from the basket after Phase A stays in what
  gets rented. This is a real gap, not just a missing test: `AppRuns.confirm` must compare the draft's
  current jobs (by `signature`) against the frozen manifest before choosing that branch, and when they
  disagree, fall through to its existing `else` branch — `_do_confirm` with the current draft's jobs,
  which re-writes the manifest and offers its own reuse/rerun chooser. Cheap, because every kept job's
  try-on stage is already journalled `done` under its stable, content-hashed run id and
  `local_tryon_reusable` skips it, so only the manifest changes and no provider is called again. The
  resume branch stays exactly as it is when the draft has not changed since Phase A.

**Try-on library (new).** Kept try-on images that outlive one draft/manifest, so the app can build a
video from a try-on generated in an earlier session without spending Gemini/Qwen quota again.
Opt-in — saved only when the app asks, never automatically on every preview, so quota spent on a
regenerate the user never liked does not silently become disk the user never asked to keep either.

- Storage: `control/tryon_library.py`, a new module shaped like `drafts.py`'s `DraftStore` — one
  JSON index per owner (`batch/tryon-library/{owner}.json`) plus the images themselves
  (`batch/tryon-library/{owner}/{id}.png`), under `control.LOCK`. Deliberately outside `out/`, so
  `batch-clean` and `_final` pruning can never remove a saved entry.
- `GET /v1/tryon-library` → `[{id, material_ids: {character, outfit, background?}, provider,
  saved_at}]`.
- `POST /v1/tryon-library` `{run_id, index}` → copies that run's current try-on image (the same file
  `GET /v1/runs/{id}/tryon/{index}` serves) into the library and records the job's material ids and
  provider. No `Idempotency-Key`: a file copy, not a spend, same as every other `/v1/draft/*` mutation.
- `GET /v1/tryon-library/{id}/image` → the stored image.
- `DELETE /v1/tryon-library/{id}` → removes the entry and its image.
- **Using an entry** is `PATCH /v1/draft {tryon_seed: id}`, recorded on the draft's `Job` (a new
  field, empty for every job composed the ordinary way). The app never touches a file path — the
  server does the copy, at the one point a job's stage-file path is knowable: inside
  `batchlib/runner.py`'s `run_local_phase`/`_one()`, which intercepts a `seedImage` stage param and
  copies that file to the stage's `dest` instead of calling `run_local_tryon`. `render_manifest`
  carries the resolved path there, because the manifest is the only channel that survives from "the
  app composed this job" to "the runner is about to run it" — the bot never knows a batch's
  `batch_id`/`out_dir` in advance. The shared journal write below it then records the stage `done`
  in exactly the shape a real provider call leaves, so `local_tryon_reusable` cannot tell the two
  apart on a later resume, and Phase A only calls Gemini/Qwen for jobs that are not seeded.
- Not in slice 6: editing a saved entry's material ids, and a size cap or expiry on the library (the
  user prunes it by hand, the way materials are pruned by hand today).

**Guided regenerate.** `_regen_tryon` already keeps every previous image as `<stem>.v<N><ext>`
(`_tryon_versions`) — slice 6 only exposes that history over HTTP
(`GET /v1/runs/{id}/tryon/{index}/versions/{n}`, above); no new storage. What is new: `regen`'s body
gains `guidance: string[]`, zero or more of `keep_face`, `tighter_crop`, `match_lighting` — a closed
vocabulary, not free text, so the phone never sends prose the provider prompt was not written to
expect. `_regen_tryon` threads `guidance` down to `local_tryon.py`'s Gemini/Qwen call, which appends a
fixed instruction fragment per flag to the prompt it already sends. Unknown values are `400
bad_request` before anything runs.

Not in slice 6, deferred on purpose: **stock-watch notifications** ("tell me on Telegram when GPU X is
back in datacenter Y"). Every other slice is one HTTP request answered from files already on disk or
one upstream call; a stock watch needs a background poller with its own schedule, independent of any
request — the first piece of always-on infrastructure this API would own. It gets its own slice once
slices 1–6 are live in the app, not folded in here to keep this slice's shape consistent with the rest.

## 6. Delivery in slices

Each slice deploys on its own; `scripts/tests/test_batch_bot.py` stays green after every slice.

| # | Slice | Extracted from `bot.py` | What the app can do after it |
|---|---|---|---|
| 1 | Skeleton: HTTP thread, auth, `cloudflared` + Access, `/v1/health`, `GET /v1/runs[/{id}]`, `/v1/outputs` with Range | Almost nothing — reads from disk | Watch progress, play and save outputs |
| 2 | Chunked upload + materials | `_stage_file`, `_prune_old_staged_files` | Manage material |
| 3 | Drafts keyed by owner | `_STATE`, `_job_for`, `_switch_*`, `_fill_slot`, batch and draft functions | Compose jobs |
| 4 | Phase A, regenerate, rent panel, **confirm** — one shared run slot (§5.8) | nothing moves; `_do_phase_a`, `_do_confirm`, `_do_resume`, `_regen_tryon` return outcomes; data half of the rent panel | Run jobs |
| 5 | Pod: kill, resume, GPU stock and choice, balance, migrate — §5.9 | nothing moves; `_do_kill` and `_start_migration` return outcomes; data halves of `_report_gpu_stock` and `_report_balance` | Pod / cost |
| 6 | Batch cross-build/drop over the existing basket, a new try-on library, guided regenerate — §5.10 | nothing moves for batch (already there); `tryon_library.py` is new; `_regen_tryon` gains `guidance` | Bulk try-on → N videos, reuse a saved try-on, steer a regenerate |

The SwiftUI app (sub-project 2) can start against slice 1.

## 7. Testing

All free, no pod. New modules are named `scripts/tests/test_batch_control_*.py` so `make batch-test`
picks them up.

- HTTP tests run a real `ThreadingHTTPServer` on an ephemeral port; `start_drain` and
  `start_phase_a` are stubbed.
- Required cases:
  - A `confirm` replayed with the same `Idempotency-Key` calls `start_drain` **once**.
  - Two concurrent `confirm`s from two threads produce one drain.
  - A stale `panel_token` is `409`.
  - Missing / wrong bearer token is `401`.
  - `..`, absolute paths and symlinks in `/outputs` and `/materials` are `404`.
  - A chunked upload interrupted after chunk *k* resumes and assembles byte-identical.
  - `Range` requests return `206` with correct `Content-Range`.
  - A refusal produces the same message text in Telegram as before the extraction.
- A test that greps `start_drain` call sites (§5.5).
- Slice 6: a batch of N jobs, one dropped via `DELETE /v1/draft/batch/{digest}` **after** Phase A has
  already recorded its try-on `done` but **before** `confirm`, then confirmed — asserts the resulting
  manifest has N-1 runs and that the kept runs' try-on stage is not re-run (no second call into the
  stubbed provider for them). A `tryon_seed` job whose seeded stage is journalled `done` before Phase A
  runs, asserting Phase A calls the stubbed provider for every *other* job in the same batch but not
  that one. `regen` with an unknown `guidance` value is `400` and calls nothing.

## 8. Deploy and verification on the VPS

- Code under `scripts/**` auto-deploys on push to `main`.
- `cloudflared`, the tunnel hostname and the Access application + service token are set up by hand
  once and documented in `scripts/vps/README.md`, next to the existing services.
- New `.env` key: `CONTROL_API_TOKEN` (and the port, `CONTROL_API_PORT`, default 8787). Added to
  `.env.example` with an empty value.
- Smoke through the tunnel after slice 1: no Access headers → `403` from Cloudflare; Access headers
  but no bearer → `401`; both → `200`.
- Measure `motion-bot` RSS before and after slice 1 and record it in `scripts/vps/README.md`. The
  claim that a thread is cheap on a 1 GB box is an assumption until then.

## 9. Out of scope

- The SwiftUI app itself (sub-project 2).
- The `@vue-flow` node-graph workflow builder — not a phone task.
- Push notifications (needs the paid Apple account; Telegram covers it).
- Multi-user: there is one user; `owner` is a namespace, not an account system.
- Moving the core into its own process (approach B) — kept possible, not done.
- Stock-watch notifications (§5.10) — needs a background poller, a different shape of work than every
  slice so far; a later slice once the app covers slices 1–6.
- Editing or expiring a try-on library entry automatically (§5.10) — pruned by hand, like materials.
