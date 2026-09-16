# QWEN.md

Context for Qwen Code when working in this repository.

`AGENTS.md` is also loaded automatically and covers structure, style, and commit rules.
`CLAUDE.md` carries a longer narrative of the same ground plus deployment history. This file is the
operational summary: what the repo is, what costs money, which commands exist, and which failures are
silent. Read `docs/gpu-pod.md` §Runbook before anything that starts a pod.

## What this repo is

A monorepo for **Motion**, a Vietnamese AI video/image generation product. The two halves are deployed
to different machines on purpose:

- `motions/` — **Nuxt 4 frontend.** Runs locally (`make dev` → http://localhost:2030), optionally also
  on the pod. No GPU needed.
- `motions-studio/` — **the entire backend**: Express API + Postgres + MinIO + ComfyUI + Python worker.
  Needs an NVIDIA GPU with ≥24GB VRAM (32GB recommended), so it runs on a **rented GPU pod**
  (RunPod / vast.ai), never on the dev machine.
- Root `Makefile` + `scripts/` — pod lifecycle, drift gates, the batch runner, the Telegram bot. This
  is repo-specific glue and where most infrastructure work happens.

**There is no local backend.** Any change to `motions-studio/` is verified by rsyncing it to a rented
pod (`make gpu-bootstrap`), which bills by the hour.

## Money is a first-class constraint

- The GPU pod bills ~$1/hour **while it exists**, including while stopped (container disk persists).
  `make gpu-destroy` is the default "done for now" action, not `make gpu-down`. Both targets verify the
  pod is actually gone and fail loudly if it is still billing.
- The Network Volume bills monthly even with no pod attached. That is deliberate — it holds ~33GB of
  models, Postgres data, and MinIO. Never suggest deleting it casually.
- Never assert cost from `currentSpendPerHr`. Use `runpodctl billing pods` (the real invoice).
- **Run the free gates instead of "just trying it on the pod":**

  ```bash
  make gpu-preflight      # validate root .env is complete before renting
  make batch-validate     # check a manifest without spending GPU
  make batch-test         # python unittest for the batch runner
  make check-job-types    # job-type lists must agree
  make check-comfy-nodes  # ComfyUI custom-node lists must agree
  make check-batch-params # scripts/batch-params.json vs linux.py
  make scrub-check        # no credential or personal email is tracked
  ```

## Directory map

```
Makefile                 pod lifecycle + gates + batch targets; `make help` lists them all
scripts/                 pod-*.sh (provision/wait/bootstrap/fe/smoke), gpu-preflight.sh,
                         drain.py, pod_watchdog.py, volume_migrate.py, check-*.mjs gates
scripts/batchlib/        batch runner library (manifest, runner, client, pipelines, params, mcp_tools)
scripts/tgbot/           Telegram bot that drives batches and volume migrations remotely
scripts/vps/             systemd units + compose for the always-on VPS side (bot, Bot API, watchdog)
scripts/tests/           python unittest suite for everything above (`make batch-test`)
docs/gpu-pod.md          the full pod walkthrough: costs, Cloudflare Tunnel, Network Volume, runbook
docs/batch-runner.md     batch runner + MCP guide, param tables, silent traps
docs/superpowers/specs/  design docs, dated; record what was measured, including rejected approaches
motions/                 Nuxt 4 frontend (app/, server/, modules/, shared/, public/)
motions-studio/api/      Express API + wf-worker (no-code workflow engine)
motions-studio/worker/   Python worker; worker_runtime/linux.py is the pipeline dispatch table
motions-studio/comfyui/  ComfyUI setup, model catalogs (catalog*.json)
motions-studio/setup/    setup-*.sh profiles, pod-volume.sh, pod-pgdump.sh, scrub-secrets.sh
batch/                   manifests (<name>.yaml) + machine state (<name>.state.json) — see boundary below
out/                     gitignored results; out/latest/_final/ holds finished videos
```

## Architecture

### Job flow

```
FE (Nuxt) ──X-API-Key / JWT──▶ Express API ──jobs table (Postgres)──▶ Python worker polls /worker/claim
                                    │                                        │
                                MinIO (S3, presigned URLs)          HTTP ──▶ ComfyUI (Wan 2.2 Animate, Qwen, LTX)
```

One generic `jobs` table: `type` · `inputs` (MinIO storage keys, one per upload field) · `params` ·
`output_key`. Workers claim atomically with `SKIP LOCKED`. Motion transfer, try-on, upscale, and
lip-sync are all the same API with a different `type`.

### The three registries that must stay in sync

1. `motions-studio/worker/worker_runtime/linux.py` — a ~10k-line file ending in `PIPELINES = {...}`,
   mapping job type → `run_xxx(job)`. This is the real dispatch table.
2. `JOB_TYPES` (env, per box/worker) — which types that worker will *claim*. **A type missing here
   fails silently**: the job sits `queued` forever with no error and no log. This is the single most
   common silent failure in the repo.
3. Setup profiles (`motions-studio/setup/setup-*.sh`), ComfyUI catalogs (`comfyui/catalog*.json`), and
   the serverless images — each locks a box to a subset of types/models.

`make check-job-types` and `make check-comfy-nodes` exist because these lists were hand-copied into
4–5 places and drifted, repeatedly and expensively. Adding a handler to `PIPELINES` turns them red **on
purpose** — that forces a decision instead of a silent omission. Types intentionally excluded from a
profile are recorded in the gate's own `EXCLUDED` map with the reason; add new exclusions there, not to
someone's memory.

**Adding a pipeline:** write `run_xxx(job)` in `linux.py`, register it in `PIPELINES`, add the type to
`JOB_TYPES` in the relevant setup profile and `.env.example`, then run `make check-job-types`.

### No-code workflow layer

The FE is a node-graph builder (`@vue-flow`). Each FE node → a handler in
`motions-studio/api/src/wf-worker/handlers.js` (`NODE_HANDLERS`) → creates a job of some type.
`wf-worker` is a separate PM2 process; `api/src/wf-worker/engine.js` is the executor. So a "node" is
FE config + handler mapping + worker pipeline — **three layers, all three required**.

### Deploy shape — three env vars

```
COMPUTE_TYPE=gpu|cpu
SETUP_PROFILE=motion-transfer|full|create-image|tryon|cpu-box   # which features the box installs (locked catalog)
WORKER_SOURCE=local|serverless|both                             # who runs jobs
```

Current shape (settled 2026-08-04): **GPU pod + `local`**. This is a personal, per-session tool, not a
24/7 service. Serverless loses because you have already paid for the GPU, plus ~155s cold start and
observed indefinite `IN_QUEUE` throttling. `docs/gpu-pod.md#deploy-shapes` has the comparison and the
crossover math (~79 jobs/day). Don't re-litigate it from first principles — the numbers are measured.

### On-pod runtime

PM2, **not Docker** (`motions-studio/ecosystem.config.cjs`): `api` · `worker` · `wf-worker` · `comfyui` ·
`minio` · optionally `motions` (FE) and `mc-dispatcher` (serverless). Postgres is native. `api` and
`wf-worker` read only `process.env`, so **every variable must be passed through `ecosystem.config.cjs`**
— setting it in the pod's `.env` alone does nothing.

Rented pods are NAT'd, so ingress is a Cloudflare Tunnel with two hostnames (`DOMAIN`→:8080,
`FE_DOMAIN`→:2030), not open ports. Models, `PGDATA`, and MinIO are symlinked onto the Network Volume
**before** setup runs — otherwise Postgres builds a cluster on container disk and it dies with the pod.

### Batch runner (`scripts/batchlib/`)

Runs many jobs from a YAML manifest. Hard boundary: **`batch/<name>.yaml` is yours, everything else
under `batch/` is the machine's.** The runner journals to `batch/<name>.state.json` and never rewrites
the YAML, because `safe_dump` would strip your comments.

`RESUME=1` first re-attaches to the *existing* `job_id` before resubmitting, so a batch interrupted at
minute 39 of a 40-minute job does not restart it. Try-on with `provider: gemini` or `provider: qwen-max`
runs locally with no pod at all (both are pure hosted-API calls — `scripts/batchlib/local_tryon.py`'s
`LOCAL_PROVIDERS`) and is the only stage allowed to run concurrently. Everything else is serial: the pod
has one GPU and `run_enhance` calls `comfy_recycle`, which assumes exclusive use.

Two remote-control surfaces sit on top of it:

- **MCP server** (`.mcp.json` → `scripts/batch_mcp.py`) exposing `batch_validate` / `batch_run` /
  `batch_status` / `batch_rerun`. It differs from the CLI in exactly one money-relevant way: if the pod
  is stopped it **errors out and tells you to run `make gpu-up`** rather than starting the pod itself,
  unless you pass `allow_start=true`. Starting a pod begins billing, so that stays a human decision.
- **Telegram bot** (`scripts/tgbot/`), hosted on a small always-on VPS (`scripts/vps/`), for launching
  and watching batches and volume migrations from a phone. `make bot-dry` runs one polling round with
  `--once --dry-run`, invoking no jobs.

`make drain FILE=batch/….yaml` rents a pod, runs the manifest, and destroys the pod in one shot
(dry-run unless `CONFIRM=yes`). `make watchdog-dry` reports what the pod watchdog would destroy right
now and destroys nothing.

## Commands

```bash
# Frontend (local)
make setup                                  # npm install + create motions/.env if absent
make dev                                    # nuxt dev on :2030
make down                                   # stop the dev server
make clean                                  # remove node_modules/.nuxt/.output (keeps motions/.env)
cd motions && npm run build                 # production bundle
cd motions && npm run typecheck             # Nuxt/Vue TS checks

# Pod lifecycle (in order — docs/gpu-pod.md §Runbook)
make gpu-preflight                          # validate .env — free, always first
make gpu-provision                          # DRY RUN: prints the create command + price
CONFIRM=yes make gpu-provision              # actually rents — the clock starts
make gpu-wait                               # wait for SSH, write GPU_SSH_HOST/PORT into .env
make gpu-bootstrap                          # rsync motions-studio/ + run setup-<SETUP_PROFILE>.sh (idempotent)
make gpu-fe                                 # deploy ONLY the frontend to the pod (separate step)
make gpu-smoke                              # 9-layer end-to-end proof — /health alone lies
make gpu-status
make gpu-logs LOG=api|worker|comfyui|wf-worker|minio
make gpu-up                                 # start an existing (stopped) pod, wait until /health answers
make gpu-down                               # pause; container disk KEEPS billing — prefer destroy
make gpu-destroy                            # DEFAULT when done; verifies gone, clears pod keys from .env

# Network Volume + database
make gpu-volume                             # wire models/PGDATA/MinIO onto the volume (idempotent)
make gpu-volume-adopt                       # ONE-TIME: move data already on the pod onto the volume
make gpu-volume-check                       # prove the volume is really in use
make gpu-db-dump / make gpu-db-check        # backup to the volume / verify the latest dump restores

# Batch runner
make batch-scan DIR=~/materials MODE=pair|cross   # emit a DRAFT batch/<date>.yaml — review before running
make batch-validate FILE=batch/….yaml             # no GPU spend
make batch FILE=batch/….yaml [RESUME=1] [FAIL_FAST=1]
make batch-params TYPE=motion|tryon|enhance       # which params a job type actually accepts
make batch-clean [KEEP=3] [DRY=1]                 # deletes runs/ only, never _final/
make batch-mcp-check                              # handshake with the MCP server, list its 4 tools

# Gates and tests (all free, no pod)
make batch-test
python3 -m unittest scripts.tests.test_batch_run                 # one module
python3 -m unittest discover -s scripts/tests -p 'test_batch_*.py'
make batch-coverage [FULL=1]                                     # which runner lines no test touches
make check-job-types && make check-comfy-nodes && make check-batch-params
make scrub-check                                                 # MUST pass before every commit
cd motions-studio/worker && python3 -m unittest discover -s tests   # backend unit tests, no GPU
```

## Conventions

- **Write in English** — code, comments, docs, commit messages, PR bodies. Much of the existing repo is
  Vietnamese; that is legacy, not a pattern to copy. Don't translate it in passing — a conversion pass
  is its own task.
- **No `# #region ALD <DD/MM/YYYY> - …` markers.** That style came with the purchased source and is
  retired. Existing ones stay where they are; write new comments as plain comments.
- Keep the habit those markers encoded, though: explain **why**, with the number that was measured and
  the date it was measured. When you change behavior a comment justifies, replace its measurement with
  your own rather than deleting the reasoning.
- Two-space indent in Vue/JS, four in Python. `camelCase` for JS, `PascalCase` for Vue components,
  `snake_case` for Python. Python tests are `test_<behavior>.py` with `test_<scenario>` methods.
- Claims about performance, cost, or quality are expected to be backed by a real run, not a plausible
  argument. Record what was measured — including rejected approaches — in `docs/superpowers/specs/`.
- `motions/` and `motions-studio/` originated from a purchased source (`ALD-Project`) but as of
  2026-08-02 are **fully owned here**. Edit them directly; there is no upstream to preserve and no
  "don't touch, it's upstream" file.
- Commits are short and imperative, often scoped by subsystem (`Volume migrate: …`, `docs(vps): …`).
  Keep each commit focused; never `git add -A` when unrelated changes are present.

## Testing

- `scripts/tests/` — python `unittest`, the batch runner / bot / watchdog / migration surface. This is
  the suite that runs without a GPU: `make batch-test`.
- `motions-studio/worker/tests/` — pure-python worker tests (frame budget, box RAM, camera-aware
  try-on, character swap).
- Frontend: `cd motions && npm run typecheck`. There is no FE unit-test suite.
- **Backend GPU behavior cannot be unit-tested.** It requires the documented pod smoke flow
  (`make gpu-smoke`). Always distinguish local unit coverage from paid end-to-end validation when
  reporting what you verified.

## Secrets — this repo is public

```bash
motions-studio/setup/scrub-secrets.sh --check    # must exit 0 before every commit
```

It scans every tracked file, `docs/` included, for credentials and personal email.

Never committed: `.env` (root and `motions/`), `motions-studio/setup/templates.json`,
`motions-studio/setup/pod.env`. Also gitignored as personal media or machine state: `out/`, `.smoke/`,
`ab-results/`, `batch/*.yaml` (except `example.yaml`), and all the bot/lease/progress files under
`batch/`. Note that `out/` holds the only evidence behind past A/B measurements — clean disk with
`make batch-clean`, which touches `runs/` only, rather than deleting `out/`.

## Known traps

- **A job stuck in `queued` forever with no error** means its type is missing from that worker's
  `JOB_TYPES`. Not a code bug, not a crash — just an omission in an env list. Check there first.
- **Editing `scripts/batchlib/mcp_tools.py` does nothing until the MCP server restarts.** The agent
  session keeps one `scripts/batch_mcp.py` process alive and the old module stays in memory, silently.
  `make batch-mcp-check` will *not* catch this — it spawns a fresh process. Confirm with
  `ps -o pid,lstart -p $(pgrep -f batch_mcp.py)`: a start time older than your edit is the trap.
- **`/health` returning 200 proves almost nothing.** Use `make gpu-smoke`.
- **`gpu-down` is not free.** The container disk keeps billing while the pod exists.
- **`vastai destroy` prompts `[y/N]` and answers N on EOF, then exits 0.** The `gpu-destroy` target
  pipes `y` and then verifies the instance is really gone; don't "fix" that by removing the verify.
- **Local `NODE_HANDLERS` can lag the deployed box.** `motions-studio/README.md` records `wan-i2v` and
  `voiceover` as offered by the FE palette but missing from the local handler map. Verify on the box
  before trusting "supported".
- **`ICON_WARN` and every `ICON_*_CE` in `scripts/tgbot/bot.py` are HTML, not glyphs.** They expand to
  `<tg-emoji emoji-id="…">` entities, so any `send_message` / `edit_message` that interpolates one
  **must** pass `parse_mode=PARSE_HTML`. Without it Telegram renders the raw tag as literal text; with
  it, a single unescaped `<` or `>` anywhere in the body makes Telegram reject the *entire* message, so
  the user sees nothing. Both failure modes are invisible in production. The test harness's
  `_check_markup` catches them, but only on paths a test actually exercises — so when touching an icon
  constant or adding a message, escape every interpolated value with `_esc()` and run
  `make batch-test`. Button labels can never carry these tags; they take a numeric
  `icon_custom_emoji_id` via `_ce_id()` as an optional 3rd tuple element instead.
- **`ecosystem.config.cjs` is the env passthrough.** A variable set only in the pod's `.env` never
  reaches `api` or `wf-worker`.

## Where to read more

| Topic | File |
|---|---|
| Full pod walkthrough, costs, Cloudflare Tunnel, runbook | `docs/gpu-pod.md` |
| Batch runner + MCP, param tables, silent traps | `docs/batch-runner.md` |
| Backend deploy reference (official) | `motions-studio/DEPLOY.md` |
| Backend overview, node catalog, hardware sizing, API usage | `motions-studio/README.md` |
| Design decisions with measurements, including rejected options | `docs/superpowers/specs/` |
| Repo-wide contributor guidelines | `AGENTS.md` |
