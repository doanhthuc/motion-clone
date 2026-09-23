# Motion iPhone app — Phase 4 run flow design

Date: 2026-09-23 · Status: approved design; not implemented

This spec refines Phase 4 of
`docs/superpowers/specs/2026-09-22-swiftui-app-design.md`. Phases 1–3 are shipped
(`docs/superpowers/swiftui-app-progress.md`). Phase 4 is the app's first money boundary: it starts
Phase A (Gemini/Qwen quota), regenerates a try-on (quota), and confirms or resumes a run (a GPU pod).
It consumes the live control-plane API (`2026-09-21-vps-control-plane-api-design.md` §5.3, §5.5,
§5.8–§5.10) and adds **no** backend route or field.

## 1. Scope and success criteria

Phase 4 is complete when the installed app can:

- open a run flow for the live slot from New Job, from the Runs hero card of a `phase_a` run, and
  offer **Retry rental** on the live slot's run detail when a rental failed;
- start Phase A, show per-job try-on progress, display each preview and its earlier versions;
- regenerate one try-on with the closed guidance vocabulary (`keep_face`, `tighter_crop`,
  `match_lighting`);
- save the current preview to the try-on library (**Keep**, free);
- read and display the rent panel — RunPod row, Vast row with its blockers, job count, estimate and a
  price quote — including the out-of-stock state with no spend button;
- confirm (including the reuse/rerun try-on chooser) or resume, each only after a new explicit tap;
- survive a dropped connection, a busy bot or an app kill mid-spend without ever minting a second key
  for one tap; and
- pass every free gate plus a live zero-spend refusal smoke, without renting a pod.

Out of Phase 4: changing the RunPod GPU (`PUT /v1/pod/gpu`), kill, balances, migrate ("Migrate →" on
the out-of-stock panel is not drawn) — Phase 5. Browsing, deleting or seeding from the try-on library,
cross builds and batch progress — Phase 6. Regenerating with a different provider — the API does not
offer it (`AppRuns.regen` reads only `run_token` and `guidance`).

## 2. What the live server actually does

Read from `scripts/tgbot/bot.py` (`AppRuns`, `AppPod`, `_rent_panel_data`, `_regen_tryon`,
`_do_phase_a`, `_do_confirm`) and `scripts/control/idempotency.py` on 2026-09-23. Facts the parent
design does not spell out, and that this design depends on:

- **`GET /v1/runs/{id}/tryon`** → `{run_id, run_token, phase_a_running, previews: [{index, run,
  status, has_image}]}`. `index` is the run's position in the manifest, as a string. `regen` and
  `resume` both require this `run_token`.
- **`GET /v1/pod`** already carries the live slot's `run_id`, the configured `gpu`, and
  `failed_rental {gpu, datacenter, stock_out, detail}` (read from `provision-failed.json`). This is how
  the app learns the run id before any spend and when to offer Retry rental.
- **Phase A is optional.** `confirm` without it takes `_do_confirm` with the draft's jobs. After Phase
  A, `confirm` takes the resume branch only when the draft still matches the manifest
  (`_phase_a_matches_draft`).
- **`confirm`** body: `{provider: "runpod"|"vast", panel_token, gpu?, tryon?: "reuse"|"rerun"}`. When
  try-on already ran for these inputs it answers `409 choice_required` whose error envelope carries an
  extra top-level `panel_token`; the app confirms again with `tryon` set.
- **Token checks come first.** `confirm` checks `panel_token` (then `gpu`) before anything else;
  `regen` checks `run_token` first (`stale_panel`); `resume` checks `run_token` first (`stale_run`),
  then `no_failure`, then `gpu`. `phase-a` has no token.
- **Idempotency records every final answer, refusals included.** `begin` writes `pending`; `finish`
  stores the response; a replay of a `pending` key is `409 outcome_unknown`. Only `503 bot_busy`
  (`BOT_LOCK` wait of 60 s timed out) is `forget`-ed. Records are pruned after **24 h** — a replay of
  an older key is a fresh request.
- Success is `202 {run_id, outcome}` with `outcome` in `started`, `queued`, … . Refusal status comes
  from `control/runs.py` `OUTCOME_STATUS`: `422` for draft refusals, `502` upstream, `400` bad
  request, `503` bot busy, `409` otherwise.

## 3. Architecture

```
MotionKit/Sources/MotionKit/
  Money/
    SpendIntent.swift        enum of the four spend calls; Codable; path + body per case
    Guidance.swift           enum keepFace | tighterCrop | matchLighting
    IdempotencyLedger.swift  one-entry JSON journal in Application Support
    SpendGate.swift          actor: the only sender of phase-a / regen / confirm / resume
  Models/RunFlow.swift       TryonPreviews, RentPanel, SpendAccepted
  Stores/RunFlow.swift       @MainActor @Observable; phases, reads, SpendResult → UI state
MotionApp/RunFlow/           thin views over RunFlow; no APIClient
```

Rejected: retry and ledger logic inside `RunFlow` (money rules tangled with UI state, and launch
replay would need a store before any screen exists); idempotency inside `APIClient` (spend policy in
the transport, where a read path could inherit retries and the "Checking the earlier Confirm…" state
has no owner).

## 4. Run flow states and screens

`RunFlow` is built for the live slot id from `GET /v1/pod`'s `run_id`. On appear it reads
`GET /v1/pod`, `GET /v1/runs/{id}/tryon` and `GET /v1/draft` concurrently and derives a phase:

| Phase | When | Screen |
|---|---|---|
| `compose` | no Phase A running or on offer | **Preview try-on** (primary when any basket job has a local try-on stage — provider `gemini` or `qwen-max`) and **Rent without preview** (secondary; the only action when no job has one) |
| `phaseARunning` | `phase_a_running` | Per-preview status rows |
| `previews` | Phase A finished, previews exist | Per job: image, version strip, **Regenerate** sheet (three guidance toggles), **Keep**, then **Continue to rent** |
| `rentPanel` | the user continues or skipped preview | RunPod row (GPU, datacenter, stock, $/h); Vast row, disabled with its `blockers` when `can_spend` is false; `jobs`; `estimate_min`; **Confirm · ~$X.XX quote** |
| `choiceRequired` | confirm answered `choice_required` | **Reuse try-on (no quota)** and **Re-run try-on** — each a new tap, new key |
| `started` | `202` | Hands off to the existing `RunDetailView` for the run id |
| `outcomeUnknown` | `409 outcome_unknown` | "Couldn't tell whether this went through — check the pod", switch to the Runs tab (its pod strip; the Pod tab arrives in Phase 5) and refresh the pod |

- **Entry points.** New Job gains **Continue to run →**, enabled when the draft is validated and
  runnable; `NewJobView` itself still holds no spend button. The Runs hero card of a `phase_a` run
  opens the flow. `RunDetailView` for the live slot shows **Retry rental** only when
  `pod.failedRental != nil`, beside its `detail`; the tap sends `resume {provider: "runpod",
  run_token, gpu}` through `SpendGate`. A `no_failure` refusal (the failure cleared meanwhile) is shown
  verbatim.
- **Out of stock.** RunPod `sold_out` and Vast `can_spend == false` → both reasons shown, no spend
  button. GPU change and migration are Phase 5.
- **Refusal text.** `409`/`422` messages are shown verbatim, as the parent design §4 requires.

## 5. Money: `SpendGate` and the idempotency ledger

**Intent.** `SpendIntent` cases: `.phaseA`, `.regen(runId, index, runToken, guidance: [Guidance])`,
`.confirm(runId, provider, panelToken, gpu, tryon: TryonChoice?)`, `.resume(runId, provider, runToken,
gpu)`. Each case produces its path and JSON body; `Guidance` encodes only the three wire values, sent in
`Guidance.allCases` order so one selection always yields one body.

**Ledger.** `IdempotencyLedger` holds **at most one** entry `{key, intent, label, createdAt}` —
`label` is what the user tapped, e.g. "Confirm · RTX 5090 · ~$1.40". Written to a temp file, synced,
then renamed over the journal, before the request leaves. One run slot means one in-flight spend:
`perform` refuses with `.spendInFlight` while an entry exists, and `RunFlow` exposes that as "every
spend button disabled, the in-flight label shown with a spinner".

**`perform(intent, label)`** — called once per tap, the only place a key is minted:

1. New UUID key → ledger entry written → request sent with `Idempotency-Key`, a 90 s timeout
   (under Cloudflare's ~100 s).
2. Classify the answer:
   - **Definitive** — any response with the JSON error envelope or a `2xx`, except `bot_busy`:
     clear the ledger, return a result. Includes `409 stale_panel`, `choice_required`,
     `outcome_unknown`, `422`, and a JSON `502 upstream_unavailable`.
   - **`503 bot_busy`** — the server forgot the key. Resend the same key after 5 s, at most 3 times,
     publishing the attempt count. Still busy → clear the ledger, return `.busy` (nothing was
     recorded server-side, so the next tap's new key is safe).
   - **Ambiguous** — transport error, timeout, or a non-JSON 5xx (Cloudflare 502/504/524; the origin
     may still be working). Keep the entry; resend the same key twice more after 2 s and 5 s. Still
     unreachable → `.unreachable`; the UI offers **Check again**, which calls `recheck()` — same key,
     never `perform`.

**Launch replay.** `replayPending()` runs once, the first time the scene becomes active. An entry
younger than **20 h** is resent once with its key under "Checking the earlier Confirm…" — a single
attempt, no retry loop — and the result is handled as above. An entry **20 h or older is never resent** — the server's 24 h prune would
turn it into a fresh spend — and is discarded with "An earlier Confirm from 14:02 couldn't be
verified. Check Runs and Pod before trying again."

**Results.** `SpendResult`: `.accepted(runId, outcome)`, `.refused(status, code, message,
panelToken?)`, `.outcomeUnknown`, `.busy(attempts)`, `.unreachable`, `.expired`. `RunFlow` maps them
to §4's phases. `stale_panel` on confirm re-reads the rent panel and waits for a new tap; on regen it
re-reads `/tryon`. Nothing ever calls `perform` automatically.

`SpendGate` decodes the error envelope itself (including `choice_required`'s top-level
`panel_token`), so `APIError` is unchanged. Transport goes through one new `APIClient.spendPost`
that returns the raw status and body, or a transport failure, and never throws.

## 6. Reads and models

- `TryonPreviews {runId, runToken, phaseARunning, previews[{index, run, status, hasImage}]}`.
- `RentPanel {runId, panelToken, afterPhaseA, jobs, estimateMin, runpod{gpu, datacenter?, stock?,
  usdPerHr?, soldOut}, vast{enabled, usdPerHr?, sessionUsd?, blockers[], canSpend}}`.
- `SpendAccepted {runId, outcome}`.
- **Quote** = `estimateMin / 60 × usdPerHr` of the selected row, labelled "~$1.40 quote"; Vast also
  shows `sessionUsd` when present. A `nil` rate hides Confirm for that row — no spend button without a
  price.
- **Polling.** `/tryon` every 5 s only while the flow is visible, the scene is active, and Phase A is
  running or any preview is `pending`/`running` — the `RunDetailStore` pattern.
- **Rent panel** read on entering `rentPanel`; pull-to-refresh uses `?force=1`. Every read replaces
  the held `panelToken`.
- **Images** via `APIClient.data`, cached per `(index, imageGeneration)`. `imageGeneration` increments
  whenever `/tryon` goes from `phase_a_running: true` to `false`. The `run_token` is not a usable cache
  key: `_regen_tryon` re-runs through `start_phase_a(resume=True)` and leaves the manifest (whose mtime
  is the token) untouched.
- **Versions** probed lazily when the strip opens: `versions/1, 2, …` until `404`, capped at 10 (the
  API returns no count).
- **Keep** = `POST /v1/tryon-library {run_id, index}` through plain `APIClient.post` — a free file
  copy, not a spend. Shows "Saved" for that `(index, imageGeneration)`.
- **`make ios-contract`** adds GET `/runs/{id}/tryon` and `/runs/{id}/rent-panel` (no `force`, cached
  stock; 120 s timeout because the Vast quote can be slow). Both free; nothing stored.

## 7. Testing and validation boundary

**`make ios-test`** — URLProtocol fakes and an injected clock, no network:

- The ledger entry exists on disk when the fake server receives the request; at most one entry;
  `perform` refuses while one exists; an atomic rewrite survives an interrupted write.
- Transport failure and non-JSON 5xx resend the same key; `recheck()` reuses it; only `perform` mints.
- `503 bot_busy` → three same-key retries 5 s apart, then `.busy` and an empty ledger.
- `409 outcome_unknown` → sent once, not retried, phase `outcomeUnknown`.
- `409 stale_panel` on confirm → one panel re-read, zero further confirm requests, new `panelToken`.
- `choice_required` → chooser; extra `panel_token` decoded; each choice uses a new key.
- Launch replay: entry < 20 h resent once with the same key; entry ≥ 20 h discarded, never sent.
- `Guidance` encodes only the three wire values; quote maths; `nil` rate hides Confirm.
- `RunFlow` phase derivation from fixtures: compose, Phase A running, previews, rent panel, sold-out
  with Vast blocked (no spend button); Retry rental visible only with `failedRental`.
- Handwritten fixtures shaped on `AppRuns.tryon` / `rent_panel` / `AppPod.pod`, with made-up ids.

**`make ios-ui-test`** — extended, zero-spend: a launch argument replaces `SpendGate` with a recording
fake in the UI-test build, so a stray tap cannot reach the VPS. The test opens the run flow from a
seeded draft and asserts the rent panel renders a quote and a spend button, which it does not tap.

**`make ios-refusal-smoke`** — new, opt-in, never part of other gates; implemented as
`motion-contract --refusal-smoke` so nothing under `scripts/**` changes (a push there redeploys the
bot). Through the real `SpendGate`
against the live API: `confirm` with a bogus `panel_token` → `409 stale_panel`; `regen` with a bogus
`run_token` → `409 stale_panel`; `resume` with a bogus `run_token` → `409 stale_run`. It asserts
`GET /v1/pod` has no lease before and after. Each refusal is a token check that runs before any
provider or pod call (§2). **`phase-a` is never called live** during Phase 4 development — it has no
token to refuse on.

**Spend.** No live Phase A, regen, confirm or resume during development. One real run (Phase A +
pod) happens only after the user approves the exact test and its quoted cost, then `make gpu-destroy`,
with cost from `runpodctl billing`.

**Acceptance.** `make ios-test`, `make ios-build`, `make ios-contract`, `make ios-ui-test`,
`make ios-refusal-smoke` (with no lease before or after) and `scrub-secrets.sh --check` all pass; the
progress handoff is updated. Live spend coverage is reported separately and only if it ran.
