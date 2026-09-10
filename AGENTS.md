# Repository Guidelines

## Project Structure & Module Organization

This repository separates the local frontend from GPU-backed services. `motions/` contains the Nuxt 4 frontend; its pages, components, composables, server routes, and public assets live under their standard Nuxt directories. `motions-studio/` contains the Express API, Python worker, ComfyUI integration, deployment setup, and backend tests. Root-level `scripts/` and `Makefile` targets manage GPU pods and batch jobs. Batch manifests belong in `batch/`, generated results in ignored `out/`, and operational or design documentation in `docs/`.

## Build, Test, and Development Commands

- `make setup`: install frontend dependencies and create `motions/.env` if absent.
- `make dev`: run Nuxt locally at `http://localhost:2030`.
- `cd motions && npm run build`: produce the production frontend bundle.
- `cd motions && npm run typecheck`: run Nuxt/Vue TypeScript checks.
- `make batch-test`: run the Python batch-runner unit suite without a GPU.
- `make check-job-types check-comfy-nodes check-batch-params`: detect drift between worker and deployment registries.
- `make gpu-preflight`: validate configuration before renting GPU capacity.

Run `make help` for the full pod lifecycle. Do not start a paid pod merely to perform checks that have a local gate.

## Coding Style & Naming Conventions

Write new code, documentation, comments, commits, and PR descriptions in English. Follow existing formatting: two-space indentation in Vue/JavaScript and four spaces in Python. Use `camelCase` for JavaScript variables and functions, `PascalCase` for Vue components, and `snake_case` for Python identifiers. Name Python tests `test_<behavior>.py` with `test_<scenario>` methods. Comments should explain why, especially for measured performance or cost decisions; do not add legacy `#region ALD` markers.

## Testing Guidelines

Batch tests use Python `unittest` under `scripts/tests/`; worker tests live in `motions-studio/worker/tests/`. Run a focused module with `python3 -m unittest scripts.tests.test_batch_run`, then run `make batch-test` and relevant registry gates before submitting. Backend GPU behavior requires the documented pod smoke flow; distinguish local unit coverage from paid end-to-end validation.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries, often scoped by subsystem, such as `Telegram bot: ...` or `docs(vps): ...`. Keep each commit focused. PRs should explain the behavior change, validation performed, cost/GPU implications, and linked issue. Include screenshots for frontend changes and logs or measurements for operational claims.

## Security & Configuration

This repository is public. Never commit `.env`, credentials, personal media, or generated outputs. Before every commit, run `motions-studio/setup/scrub-secrets.sh --check` and require exit code 0.
