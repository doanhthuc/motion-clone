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
| `GET /v1/materials` | List |
| `GET /v1/materials/{id}/thumb` | ffmpeg poster frame / downscaled image, cached on disk |
| `DELETE /v1/materials/{id}` | `409` if a draft or a live run references it |

Unfinished uploads older than 24 h are deleted by `_tick_staging_prune`, extended to cover the
uploads directory (it currently only ages out staged files after `STAGING_MAX_AGE_DAYS`).

### 5.2 Drafts (per owner; the app is `owner="app"`)

| Method + path | Behaviour |
|---|---|
| `GET /v1/pipelines` | Pipelines with required/optional roles and providers, derived from `batchlib.pipelines.PIPELINES`/`STAGES` — the app never hardcodes a pipeline |
| `GET /v1/draft` | Current job, the batch so far, missing slots, the "next step" line |
| `PATCH /v1/draft` `{pipeline?, provider?, slots?: {role: material_id}}` | Same rules as `_switch_pipeline` / `_switch_provider` / `_fill_slot` |
| `POST /v1/draft/add-to-batch` | `_add_to_batch` |
| `DELETE /v1/draft/batch/{digest}` | `_drop_from_batch` |
| `POST /v1/draft/clear` | `_clear_job` |
| `POST /v1/draft/validate` | `make batch-validate` — free, no pod |

### 5.3 Runs (global)

| Method + path | Behaviour |
|---|---|
| `POST /v1/runs/phase-a` → `Run` | `_do_phase_a` for the app's draft |
| `POST /v1/runs/{id}/tryon/{index}/regen` `{provider?}` | `_regen_tryon` / `_retry_tryon` |
| `GET /v1/runs/{id}/tryon/{index}` | The try-on preview image |
| `GET /v1/runs/{id}/rent-panel` | Per-GPU stock and price at the home datacenter, RunPod and Vast, Vast refusals, and a `panel_token` |
| `POST /v1/runs/{id}/confirm` `{gpu, provider, panel_token}` | **The money gate** (§5.5) |
| `GET /v1/runs` | Runs, newest first, from the on-disk journals |
| `GET /v1/runs/{id}` | Stages, per-job state, the lease's `provisioned_at` (epoch; the client computes elapsed), cost estimate (slice 5). Read from `state.json` (`progress_text`'s source), so it stays true after the pod is gone. Supports `ETag` / `If-None-Match`, compared weakly because Cloudflare turns ETags into `W/"…"` when it compresses. The body must hold no field derived from the current time, or the ETag changes on every poll |
| `POST /v1/runs/{id}/kill` | `_do_kill` |
| `POST /v1/runs/{id}/resume` | `_do_resume` |

A run's `id` is the manifest stem (`batch/<id>.yaml`).

### 5.4 Pod, GPU, outputs

| Method + path | Behaviour |
|---|---|
| `GET /v1/pod` | Lease (provider, GPU, since, quoted $/h), migration state, balance |
| `GET /v1/gpu/stock` | `_report_gpu_stock`'s data |
| `POST /v1/pod/migrate` `{to_dc, confirm_token}` | `_start_migration`, behind a two-step confirm as in the bot |
| `GET /v1/outputs` | Batches in `out/*/_final` with their files |
| `GET /v1/outputs/{batch}/{file}` | Streams the file with HTTP `Range` support so `AVPlayer` plays without a full download |

Every path parameter that names a file goes through `_safe_child`; `..`, absolute paths and symlinks
out of the root are `404`.
Symlinked batch directories under `out/` (the runner's `out/latest`) are not batches: they are left out of
`GET /v1/outputs` and refused by the file route, so the newest batch never appears twice.

### 5.5 Protecting money

A mobile client retries. A duplicated `confirm` must never become a second pod.

- `confirm` requires the `panel_token` from the most recent `rent-panel` read — the same idea as
  `_run_token`. A stale token is `409 stale_panel`; the app re-reads the panel.
- `confirm` requires an `Idempotency-Key` header. The server records `(key → response)` for 24 h on
  disk; a replay returns the stored response and calls nothing.
- `confirm` runs **the same `_do_confirm` body** the bot uses, moved to `control/runs.py`. There is
  no second gate. The grep invariant becomes: `start_drain` has exactly two call sites in
  `scripts/control/runs.py` (confirm and resume) and none anywhere else.
- `phase-a` and `regen` spend Gemini/Qwen quota, so they take an `Idempotency-Key` too.

### 5.6 Status codes

| Code | When |
|---|---|
| `401` | Missing or wrong bearer token |
| `404` | Unknown id, or a path outside its root |
| `409` | World-state refusal: migration in progress, a run already live, Phase A still running, stale `panel_token`, material in use |
| `422` | Draft refusal: missing slots, validation failed, unanswered files |
| `500` | Unexpected exception — logged, and **never** propagated into the bot's poll loop |

### 5.7 Progress on the phone

The app polls `GET /v1/runs/{id}` every 5 s while in the foreground, using `ETag`. No SSE: Cloudflare
drops idle connections after ~100 s, and the app cannot hold a connection in the background without
push anyway. When the app is closed, Telegram reports.

## 6. Delivery in slices

Each slice deploys on its own; `scripts/tests/test_batch_bot.py` stays green after every slice.

| # | Slice | Extracted from `bot.py` | What the app can do after it |
|---|---|---|---|
| 1 | Skeleton: HTTP thread, auth, `cloudflared` + Access, `/v1/health`, `GET /v1/runs[/{id}]`, `/v1/outputs` with Range | Almost nothing — reads from disk | Watch progress, play and save outputs |
| 2 | Chunked upload + materials | `_stage_file`, `_prune_old_staged_files` | Manage material |
| 3 | Drafts keyed by owner | `_STATE`, `_job_for`, `_switch_*`, `_fill_slot`, batch and draft functions | Compose jobs |
| 4 | Phase A, regenerate, rent panel, **confirm** | `_do_phase_a`, `_do_confirm`, data half of `_offer_run_confirm`, `_regen_tryon`, `_retry_tryon` | Run jobs |
| 5 | Pod: kill, resume, GPU stock, balance, migrate | `_do_kill`, `_do_resume`, `_report_gpu_stock`, `_report_balance`, `_start_migration` | Pod / cost |

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
