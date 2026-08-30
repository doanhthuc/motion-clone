# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A monorepo for a Vietnamese AI video/image generation product ("Motion"). Two halves that are
deployed to different machines:

- `motions/` — Nuxt 4 frontend. Runs **locally** (`make dev` → localhost:2030), or optionally on the pod.
- `motions-studio/` — the whole backend (Express API + Postgres + MinIO + ComfyUI + Python worker).
  Needs an NVIDIA GPU ≥24GB VRAM, so it runs on a **rented GPU pod** (RunPod), never on the dev machine.
- Root `Makefile` + `scripts/` — pod lifecycle, gates, and the batch runner. This layer is repo-specific
  glue and is where most infra work happens.

There is no local backend. Any change to `motions-studio/` is verified by rsyncing it to a rented pod
(`make gpu-bootstrap`) — which costs money by the hour. Read `docs/gpu-pod.md` §Runbook before doing
anything that starts a pod.

## Money is a first-class constraint

- The GPU pod bills ~$1/hour **while it exists**, including while stopped (container disk).
  `make gpu-destroy` is the default "done for now" action, not `gpu-down`. See `docs/gpu-pod.md#destroy-first`.
- The Network Volume bills monthly even with no pod. That is deliberate — it holds ~33GB of models,
  Postgres data and MinIO. Never suggest deleting it casually.
- Never assert cost from `currentSpendPerHr`. Use `runpodctl billing pods` (real invoice).
- Free gates that catch mistakes **before** spending: `make gpu-preflight`, `make batch-validate`,
  `make batch-test`, `make check-job-types`, `make check-comfy-nodes`, `make check-batch-params`.
  Run them instead of "just trying it on the pod".

## Commands

```bash
# Frontend (local)
make setup                 # npm install + create motions/.env
make dev                   # nuxt dev on :2030
cd motions && npm run build / npm run typecheck

# Pod lifecycle (in order; see docs/gpu-pod.md §Runbook)
make gpu-preflight                          # validate .env — free, do this first
bash scripts/pod-provision.sh               # DRY RUN: prints the create command + price
CONFIRM=yes bash scripts/pod-provision.sh   # actually rents — clock starts
make gpu-wait                               # waits for SSH, writes GPU_SSH_HOST/PORT into .env
make gpu-bootstrap                          # rsync motions-studio/ + run setup-<SETUP_PROFILE>.sh (idempotent)
make gpu-fe                                 # deploy frontend to the pod (separate step, ~15s via CI artifact)
make gpu-smoke                              # 9-layer end-to-end proof; /health alone lies
make gpu-status / gpu-logs (LOG=api|worker|comfyui|wf-worker|minio)
make gpu-destroy                            # DEFAULT when done — verifies the pod is really gone, clears .env

# Batch runner (many jobs without clicking the UI)
make batch-scan DIR=~/materials MODE=pair|cross   # emits a DRAFT batch/<date>.yaml to review
make batch-validate FILE=batch/….yaml             # no GPU spend
make batch FILE=batch/….yaml [RESUME=1] [FAIL_FAST=1]
make batch-params TYPE=motion|tryon|enhance       # which params a job type actually accepts
make batch-clean [KEEP=3] [DRY=1]                 # only deletes runs/, never _final/

# Gates (all free, no pod)
make batch-test                                   # python unittest, scripts/tests/
python3 -m unittest discover -s scripts/tests -p 'test_batch_run.py'   # single module
make check-job-types                              # the 4 job-type lists must agree
make check-comfy-nodes                            # the 4 ComfyUI custom-node lists must agree
make check-batch-params                           # scripts/batch-params.json vs linux.py
make batch-coverage [FULL=1]
motions-studio/setup/scrub-secrets.sh --check     # MUST exit 0 before every commit — repo is public

# Backend unit tests (pure-python, no GPU)
cd motions-studio/worker && python3 -m unittest discover -s tests
```

## Architecture

### Job flow

```
FE (Nuxt) ──X-API-Key / JWT──▶ Express API ──jobs table (Postgres)──▶ Python worker polls /worker/claim
                                    │                                        │
                                MinIO (S3, presigned URLs)          HTTP ──▶ ComfyUI (Wan 2.2 Animate, Qwen, LTX)
```

One generic `jobs` table: `type` · `inputs` (MinIO storage keys, one per upload field) · `params` ·
`output_key`. Workers claim atomically with `SKIP LOCKED`. Everything — motion transfer, try-on,
upscale, lip-sync — is the same API with a different `type`.

### The three registries you must keep in sync

1. `motions-studio/worker/worker_runtime/linux.py` — a ~10k-line file ending in `PIPELINES = {...}`,
   mapping job type → `run_xxx(job)`. This is the real dispatch table.
2. `JOB_TYPES` (env, per box/worker) — which types that worker will *claim*. **A type missing here
   fails silently**: the job sits `queued` forever, no error, no log. This is the single most common
   silent failure in this repo.
3. Setup profiles (`motions-studio/setup/setup-*.sh`), ComfyUI catalogs
   (`comfyui/catalog*.json`) and the serverless images — each locks a box to a subset of types/models.

`make check-job-types` and `make check-comfy-nodes` exist precisely because these lists were copied by
hand into 4–5 places and drifted. Adding a handler to `PIPELINES` turns them red on purpose — that
forces a decision instead of a silent omission.

**Adding a pipeline:** write `run_xxx(job)` in `linux.py`, register in `PIPELINES`, add the type to
`JOB_TYPES` in the relevant setup profile / `.env.example`, then run `make check-job-types`.

### No-code workflow layer

The FE is a node-graph builder (`@vue-flow`). Each FE node → a handler in
`api/src/wf-worker/handlers.js` → creates a job of some type. `wf-worker` (PM2 process) runs graphs;
`api/src/wf-worker/engine.js` is the executor. So a "node" is FE config + handler mapping + a worker
pipeline — three layers, all three needed.

### Deploy shape — three env vars

```
COMPUTE_TYPE=gpu|cpu
SETUP_PROFILE=motion-transfer|full|create-image|tryon|cpu-box   # which features the box installs (locked catalog)
WORKER_SOURCE=local|serverless|both                             # who runs jobs
```

Current shape (settled 2026-08-04): **GPU pod + `local`** — this is a personal, per-session tool, not a
24/7 service. Serverless loses here because you already paid for the GPU, plus ~155s cold start and
observed indefinite `IN_QUEUE` throttling. `docs/gpu-pod.md#deploy-shapes` has the full comparison and
the crossover math (~79 jobs/day). Don't re-litigate it from first principles; the numbers are measured.

### On-pod runtime

PM2, **not Docker** (`ecosystem.config.cjs`): `api` · `worker` · `wf-worker` · `comfyui` · `minio` ·
optionally `motions` (FE) and `mc-dispatcher` (serverless). Postgres is native. `api` and `wf-worker`
read only `process.env`, so every variable must be passed through `ecosystem.config.cjs`.

Rented pods are NAT'd, so ingress is a Cloudflare Tunnel with two hostnames (`DOMAIN`→:8080,
`FE_DOMAIN`→:2030), not open ports. Models, `PGDATA` and MinIO are symlinked onto the Network Volume
**before** setup runs — otherwise Postgres builds a cluster on container disk and it dies with the pod.

### Batch runner (`scripts/batchlib/`)

Runs many jobs from a YAML manifest. Hard boundary: **`batch/<name>.yaml` is yours, everything else is
the machine's** — the runner journals to `batch/<name>.state.json` (never rewrites the YAML, because
`safe_dump` would strip your comments). `RESUME=1` first re-attaches to the *existing* `job_id` before
resubmitting, so a batch interrupted at minute 39 of a 40-minute job doesn't restart it. Try-on with
`provider: gemini` runs locally (no pod) and is the only stage allowed to run concurrently — the pod
has one GPU and `run_enhance` calls `comfy_recycle`, which assumes exclusive use.

An MCP server (`.mcp.json` → `scripts/batch_mcp.py`) exposes `batch_validate` / `batch_run` /
`batch_status` / `batch_rerun`. **Editing `batchlib/mcp_tools.py` requires restarting Claude Code** —
the server process stays alive for the session and keeps the old module in memory. `make batch-mcp-check`
will NOT catch this (it spawns a fresh process). Full guide: `docs/batch-runner.md`.

## Conventions

- **Write in English** — docs, code comments, commit messages, PR bodies. Much of the existing repo is
  Vietnamese; that is legacy, not a pattern to copy. Don't translate it in passing — a conversion pass
  is its own task.
- **Don't add `# #region ALD <DD/MM/YYYY> - …` markers.** That style came with the purchased source and
  is retired. Existing ones stay where they are; write new comments as plain comments.
- Keep the habit those comments encoded, though: explain **why**, with the number that was measured and
  the date it was measured. When you change behavior a comment justifies, replace its measurement with
  your own rather than deleting the reasoning.
- Claims about performance/cost/quality in this repo are expected to be backed by a real run, not a
  plausible argument. Design docs live in `docs/superpowers/specs/` and record what was measured,
  including approaches that were tried and rejected.
- `motions/` and `motions-studio/` originated from a purchased source (`ALD-Project`) but as of
  2026-08-02 are fully owned here. Edit them directly; there is no upstream to preserve.

## Secrets — repo is public

`motions-studio/setup/scrub-secrets.sh --check` must exit 0 before any commit. It scans every tracked
file, including `docs/`. Never-committed files: `.env` (root and `motions/`),
`motions-studio/setup/templates.json`, `motions-studio/setup/pod.env`. Batch material, `out/`,
`.smoke/` and `ab-results/` are gitignored personal media — but `out/` also holds the only evidence
behind past A/B measurements, so don't delete it when cleaning disk.
