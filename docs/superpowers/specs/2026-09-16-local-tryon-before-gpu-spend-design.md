# Local try-on before the GPU spend decision — design

Date: 2026-09-16 · Status: approved in chat; pending written-spec review · Supersedes nothing

Completes the `Defer means preserve, never discard` section of
[2026-08-30-telegram-batch-control-design.md](2026-08-30-telegram-batch-control-design.md#defer-means-preserve-never-discard),
which specified this behaviour and was only half implemented.

Two changes, shipped as two commits because they are independently testable and the first one stops
a live money leak:

- **A** — retrying after a GPU stock-out must not call Gemini a second time.
- **B** — Phase A (local try-on) runs *before* the GPU panel, so the stock the user decides on is
  measured at the moment of the decision instead of minutes earlier.

Line numbers below are as of 2026-09-16 and will drift; the function names are the durable reference.

---

## 1. What the user actually asked for

> Hiện trong telegram bot khi bấm run spend, nếu có các job trong batch có local-tryon-api với gemini
> hay qwen-max thì phải đợi cho bước đó xong rồi mới tiếp tục thuê pod. Có thể lúc chạy xong các bước
> local-tryon-api rồi mới thuê GPU thì GPU stock ở datacenter hiện tại đã hết mất, làm cho batch dừng
> lại luôn. Mà sau đó bấm again lại thì lại chạy lại bước local-tryon-api.
>
> Tôi đề xuất: khi bấm run batch thì chạy các bước local-tryon-api đi, xong có kết quả rồi mới hiện
> ra panel hỏi thuê GPU. Nhưng lỡ nếu hết GPU và bấm đợi thì lần sau chạy lại vẫn giữ kết quả
> local-tryon-api trước đó để chạy tiếp.

Two distinct pains, and it matters that they are distinct — fixing only the ordering would leave the
money leak reachable:

1. **Stock is measured too early.** The user decides to spend against a stock reading taken before
   Phase A, but the rental happens after it.
2. **Deferring loses work that was already paid for.** After a stock-out, the retry path re-runs
   every local try-on and bills Gemini again.

---

## 2. What the code already does

The ordering the user proposes is **already implemented one layer down**. `scripts/drain.py:324-334`
runs Phase A (`batch_run.py --file X --no-start`) first and only calls `provision()` when Phase A
returns `EXIT_NEEDS_POD = 3` (`scripts/batch_run.py:34`). `batch_run.py:181-190` even prints the
right advice when it stops there:

```
Đã xong try-on local — ảnh đã lưu, KHÔNG mất khi thuê pod xong.
Thuê/bật pod rồi chạy tiếp: make batch FILE=… RESUME=1
```

So a stock-out after Phase A is inherent to the current design, not a bot defect. The defect is
*where the stock reading is taken*: `_offer_run_confirm` (`scripts/tgbot/bot.py:3866`) reads stock
while rendering the panel, the user taps spend, Phase A runs for several minutes, and only then does
`drain.py` rent. Stock can vanish inside that window.

The re-billing has one precise cause. `_do_confirm` (`bot.py:4301`) is the only path a fresh spend
takes, and it calls:

```python
start_drain(manifest_path, dry_run=dry_run)      # bot.py:4420 — no resume
```

while `_do_resume` (`bot.py:4253`) calls `start_drain(…, resume=True)` (`bot.py:4297`). Without
`--resume`, `resolve_batch_id(resume=False)` mints a **new** batch id (`batch_run.py:68`) and
`prepare_batch(resume=False)` resets the journal to `{"version": 1, "runs": {}}` (`runner.py:315`) —
overwriting the very file that recorded the finished try-ons. The images survive on disk under
`out/<old-id>/runs/<run>/`, but nothing reads them. Gemini is billed twice for the same work.

The `Đợi` button on the stock-out card points at exactly that losing path
(`bot.py:1617`):

> "OK — dismissed, nothing changed. Use the other buttons above, or **/confirm again later**, when
> you want to retry."

"The other buttons above" (`Đổi sang GPU`, `Migrate`) route through `_do_resume` and *do* preserve
Phase A. `/confirm again later` routes through `_do_confirm` and does not. `/again`
(`bot.py:4196`) rebuilds the job into `_STATE` and lands on the same `_do_confirm`.

### Spec vs. implementation

| 2026-08-30 spec | Implementation today |
|---|---|
| `[ RTX PRO 4500 · $0.49/h ]` switch, resumes | ✅ `_CB_RECOVER_SWITCH` → `_do_resume(resume=True)` |
| EU-CZ-1 migration, resumes | ✅ `_CB_RECOVER_MIGRATE` → `_do_resume` |
| `Try-on 4/4 finished and is preserved` | ⚠️ card says "your batch is safe" without naming the count or the mechanism |
| `[ Defer — resume later via /batches ]` | ❌ no `/batches`; `Đợi` is a dead-end dismissal |
| `[ Wait for a 5090 and start automatically · give up after 6h ]` | ❌ `_tick_gpu_subs` (`bot.py:3558`) only *notifies*, never starts |
| "Gemini is not called twice" | ❌ broken, via `_do_confirm` |

---

## 3. Non-goals

| Cut | Why |
|---|---|
| `/batches` deferred-batch list | Real spec debt, and the right fix for "the card is buried in scrollback three days later". Cut to keep commit A reviewable; it needs its own state file and retention rules. Tracked as follow-up. |
| Auto-start a pod when stock appears | The spec asked for it. It is an unattended spend of $0.99/h with no fresh human tap, which contradicts the repo's money rule — QWEN.md: the batch MCP "errors out and tells you to run `make gpu-up`" rather than starting a pod, "because starting a pod begins billing, so that stays a human decision". Revisit only with an explicit decision to change that rule. |
| Params-aware skip in Phase B (`run_one`) | See §5 — the false-invalidation risk on a *paid* stage is worse than the bug it would fix. |
| Per-run granularity for "re-run try-on" | See §4.4. |

---

## 4. Commit A — retry must not re-bill Gemini

### 4.1 The Phase A skip check becomes params-aware

`run_local_phase._one` (`runner.py:482`) today:

```python
if recorded.get("status") == "done" and dest.is_file():
```

becomes:

```python
if recorded.get("status") == "done" and dest.is_file() \
        and recorded.get("params_manifest") == params:
```

Both sides come from the same `effective_stage_params(stage_name, run.stage_params.get(stage_name))`
derivation, so `==` is the right comparison — no normalisation layer, no string/bool coercion.
`params_manifest` is already written by Phase A itself (`runner.py:524-526`), so no journal migration
is needed: an entry written before this change carries the field already.

This is the root fix. Without it, `gemini → qwen-max` produces the **same** `run_id`, because
`run_id_for` (`job.py:90`) hashes only the material file stems, and the try-on image generated by
Gemini is silently reused for a request that asked for qwen-max. The manifest is valid, nothing
errors, only the output is wrong — the same failure shape `_local_tryon_eligible`'s own docstring
warns about for `cleanOnly`.

Why manifest *bytes* cannot be the staleness check instead: `render_manifest` writes
`# Generated by the Telegram bot at {now}` (`job.py:151`), so the file differs on every render.
Any byte-comparison would always report "changed".

Cost of a false negative here (params judged different when they are really the same) is one extra
Gemini call — cents. That is why this check is safe at Phase A and not at Phase B.

**Express it as one helper, not an inlined condition.** Something like
`local_tryon_reusable(run, stage_name, recorded, dest) -> bool`, living in `runner.py` beside
`_local_tryon_stage`, with three callers: the Phase A skip itself, `_do_confirm`'s chooser (§4.4) and
the stock-out card's preserved count (§4.3). Three callers is not premature abstraction — it is the
precedent `_local_tryon_stage` already sets, whose docstring states the rule this follows: two places
answering the same question differently is how a batch either calls Gemini for the wrong run or waits
for a pod it does not need. Here the divergence would be a card promising preservation that the
runner then declines to honour.

### 4.2 A narrow provenance guard in Phase B, instead of full params-awareness

Phase A stamps `"phase": "local"` into the journal entry it writes. `run_one`'s skip check
(`runner.py:172`) additionally refuses to reuse an entry carrying that stamp when
`_local_tryon_stage(run)` no longer names that stage — i.e. the user switched `/provider` away from
a local provider, so the stage now belongs to the pod and the local image must not stand in for it.

A journal written before this change has no `phase` key. Missing key means unknown provenance and
gets **today's** behaviour (reuse), so an in-flight batch resumed across the upgrade is unaffected.

### 4.3 The stock-out card gets a Retry button that actually resumes

- New `_CB_RECOVER_RETRY = "rec:retry:"` + manifest stem, handled next to `_CB_RECOVER_SWITCH`
  (`bot.py:1622`), calling `_do_resume` with the GPU unchanged. This is the missing third recovery
  path: today the only same-GPU option is `Đợi`, which does nothing.
- `Đợi`'s copy stops recommending `/confirm again later`. It names the Retry button instead.
- The card states the preserved try-on count, per the spec's own wording — and it must count with the
  **same predicate §4.1 skips with**, not a looser one. Counting `done` + file-on-disk without the
  params comparison would announce "Try-on 4/4 preserved" for a batch whose provider changed and
  which is therefore about to re-run all four. One shared helper, three callers (§4.1).

`test_stock_out_offers_wait_switch_migrate_and_subscribe_buttons`
(`scripts/tests/test_batch_bot.py:4515`) pins the current button set and must be updated.

### 4.4 `_do_confirm` asks instead of guessing

`_do_confirm` gains one keyword, `phase_a_choice: "reuse" | "rerun" | None = None`.

**First entry (`phase_a_choice is None`)** runs today's guards, writes the manifest as it already
does (`bot.py:4402`), then loads that file back and derives each run's local try-on params with
`_local_tryon_stage` + `effective_stage_params` — the same derivation Phase A itself uses, so the
comparison is between two values produced by one code path. If the journal for that manifest has a
batch id and at least one local try-on stage `done` with **matching** `params_manifest`, it sends a
chooser and returns:

```
Try-on already ran for these exact inputs (3/3 runs).
[ Reuse — no Gemini spend ]   [ Re-run try-on ]
```

Both buttons carry `_run_token(chat_id)`, the same `mtime_ns` staleness stamp the spend button uses
(`bot.py:1394`), so a manifest rewritten afterwards kills them.

**That early return must happen before `_freeze_panel` and before the `_STATE` clear** at the end of
`_do_confirm`. Freezing marks the panel "submitted" when nothing has been submitted, and clearing
`_STATE` would leave the second entry with no job to confirm.

**Second entry (`phase_a_choice` set)** skips `write_manifest` entirely. This is not an optimisation:
rewriting the file would bump its `mtime_ns`, and `_run_token` *is* that mtime — so the buttons the
user just tapped would fail their own staleness check and be rejected as "from an older version of
the bot". Skipping the rewrite is what keeps the token valid, and it is safe because the bytes on
disk are exactly the ones the chooser was minted from.

It also skips re-offering the chooser, and calls `start_drain` with:

- **Reuse** → `resume=True`
- **Re-run** → `resume=True, force_local=True`

`resume=True` in *both* branches: the batch id and any already-completed pod stages must survive
either choice. What differs is only whether Phase A's skip check is bypassed.

`force_local` threads through the same chain `RESUME=1` already does —
`bot.py` → `run.py` → `Makefile` → `drain.py` → `batch_run.py` → `run_local_phase(force=True)`, six
files. An explicit flag rather than deleting journal entries from the bot: the journal is the runner's
file, nothing outside `batchlib` writes it today, and that should stay true.

Two judgement calls, flagged because a reviewer should look at them rather than inherit them:

- **"Re-run try-on" forces every run in the batch**, not only the ones whose params changed.
  Per-run forcing would need a notion of "which run did the user actually edit", and guessing wrong
  there reuses a bad image silently — worse than one extra Gemini call.
- **`_do_confirm` stays the single money gate.** The chooser adds keyword arguments, not a second
  path to `CONFIRM=yes`. `start_drain` keeps exactly two call sites.

`test_confirm_calls_start_drain_once_with_dry_run_false` (`test_batch_bot.py:3349`) pins "one tap and
the drain runs" and must be updated for the chooser.

---

## 5. Why Phase B is not params-aware

Making `run_one`'s skip check (`runner.py:172`) compare params looks symmetric with §4.1 and is not.

The journal stores `params_manifest` as computed at submit time; the check would compare it against
`effective_stage_params` recomputed at resume time. Those two agree only as long as the *code* that
derives defaults does not change. Update `params.py`'s defaults, or pull a repo change that adds a
key, and every stage recorded "done" stops matching — on a resume that was supposed to skip them.
At Phase A that costs a Gemini call. At Phase B it re-submits a 40-minute `enhance` to a GPU billing
$0.99/h, on a batch the user resumed precisely to avoid paying twice.

`_try_reattach`'s docstring already names this as the bug the resume machinery exists to prevent.
A silent false-invalidation there is a regression in the one guarantee the runner makes.

§4.2's provenance stamp closes the one Phase B hole that is actually reachable from the bot
(a `/provider` switch away from a local provider) without touching the general case.

---

## 6. Commit B — Phase A before the spend panel

### 6.1 `drain.py --phase-a-only`

Runs Phase A and exits with its return code, never reaching `provision()`. Independent of `--yes`:
the flag means "may run Phase A, may never rent". `run.py:261` stays the only line in the repo that
appends `CONFIRM=yes`, and the new `start_phase_a` never appends it.

The flag must be checked **before** `main()`'s existing `if not args.yes` early return
(`drain.py:310-313`), which today prints `DRY RUN.` and returns 0 without running anything. Phase A
is not a dry run — it spends Gemini quota and writes the journal — so a `--phase-a-only` invocation
without `--yes` must still execute it. Getting this backwards yields a flag that silently does
nothing, which is the worst shape for a money-adjacent control.

### 6.2 `start_phase_a` gets its own registry, not `_RUNNING`

`drain_running()` (`run.py:293`) returning True makes `_do_confirm` route the job into the **mailbox**
(`bot.py:4392-4394`) so `chain_or_teardown` picks it up on the pod already paid for. Phase A has no
pod. Reusing `_RUNNING` would queue a job into its own mailbox.

So: a separate `_PHASE_A: dict[Path, Popen]`, a `phase_a_running()`, and
`busy() = drain_running(p) or phase_a_running(p)`. The guards that exist because `drain.py` *reads the
manifest file* — `/clear` (`bot.py:3120`), `/wipe` (`bot.py:3194`), `_render_and_validate`'s write
guard — switch to `busy()`. The guard that exists because *a pod is billed* stays on `drain_running()`.

### 6.3 Flow

```
[Run] → _offer_run_confirm            (stock, unchanged)
      → [Yes, spend] → start_phase_a  ← Gemini quota only, no GPU
      → tick_phase_a sees exit 3 → send the GPU panel, stock RE-MEASURED now
      → [Yes, spend $X/h] → start_drain(resume=True)
                            → Phase A skips → provision → bootstrap → Phase B
```

- exit `0` — the manifest was entirely local; deliver results, no panel.
- exit `3` (`EXIT_NEEDS_POD`) — show the panel.
- anything else — Phase A failed; report it, no panel. Renting after a failed local phase is what
  `drain.py:331` already refuses to do, and the bot must not become a second, laxer copy of that rule.

**The post-Phase-A panel's spend button routes to `_do_resume`, not `_do_confirm`.** This is forced
rather than chosen: `_do_confirm` begins from `job = _STATE.get(chat_id)` and
`queued = _jobs_for(chat_id)`, and `_do_confirm` *clears both* before returning. By the time Phase A
finishes minutes later the draft job is gone, so a panel wired to `_CB_RUN_GO` would answer "no
complete job yet — send the required files first" for a batch whose try-on images are sitting on
disk. `_do_resume` (`bot.py:4253`) needs no `_STATE`: it loads the manifest from disk and requires
only that the journal already has a batch id — which Phase A writes immediately, before its first
Gemini call (`runner.py`, the `save_state` right after `state["batch"] = batch_id`).

This is also why `_offer_run_confirm` needs to stop hardcoding `_CB_RUN_GO + _run_token(chat_id)`
(`bot.py:3913`, `:3973`) and take the callback to mint as a parameter. Two panels, same stock
rendering, different destination — and `start_drain` still has exactly two call sites, because
`_do_resume` is already one of them.

`resume=True` is mandatory on the post-Phase-A drain: Phase A has already journalled, and resume is
what makes it skipped rather than paid for twice — the same reasoning recorded in `drain.py`'s own
comment at :315-323.

`tick_phase_a` joins the existing poll loop beside `tick_progress` and `tick_migration_progress`
(`bot.py:4973-4974`).

### 6.4 Manifests with no local try-on keep today's single-tap flow

The predicate is `_local_tryon_stage` (`runner.py:382`) — the same function `run_local_phase` and
`needs_pod` already share, and whose docstring states that two places answering this differently is
how a batch either calls Gemini for the wrong run or waits for a pod it does not need. This design
gives it several more callers (§4.2, §4.3, §4.4 and here) and must not give it a second opinion:
every one of them asks `_local_tryon_stage`, none of them re-derives the answer.

### 6.5 The progress message must know which phase it is in

`_elapsed(lease)` returns `""` with no lease, and `progress_text`'s fallback line is
"waiting for the pod — nothing recorded yet" (`run.py`). During Phase A there is no pod to wait for
and the line is false. The renderer needs a phase label, supplied by the caller, rather than
inferring one from the absence of a lease.

### 6.6 Copy that becomes wrong

- The `[Run]` button now spends Gemini quota before any money confirmation. It has to say so.
- `BOT_COMMANDS`' `("confirm", "SPENDS MONEY - rents a GPU at $0.99/h and starts")` (`bot.py:4898`)
  no longer describes a single step.

---

## 7. Rejected approaches

**Make `/confirm` resume silently, without asking.** Fewer taps, and correct whenever the user is
retrying a stock-out. Wrong whenever they are re-rolling a try-on they did not like — which is a
real workflow here, it has its own command (`/tryon`, "just the try-on image, when the result looks
wrong") — and the failure is silent: the bot hands back the image the user was trying to get away
from. The two intents are indistinguishable from inside `_do_confirm`, so it asks. The extra tap
only appears when a reusable journal exists, which is rare.

**Patch only the retry button, leave the ordering alone.** Cheapest, and it does stop the
re-billing for users who tap the card's own buttons. It does not survive scrollback: three days
later the card is gone, `/again` + `/confirm` is the only path left, and stock is still measured
minutes before the rental. This was the "minimal patch" option and lost.

**Delete the try-on journal entries from the bot to force a re-run.** Local to `bot.py`, no flag
threaded through six files. Rejected because it puts a second writer on the journal: `batchlib`
owns that file, `run_local_phase` and `run_one` both mutate it under a lock, and a bot-side edit
races whichever of them is running. The flag is more plumbing and no race.

**Auto-start when stock appears.** Spec'd, and genuinely useful on an always-on VPS. Cut because it
is unattended spending — see §3.

---

## 8. Testing

All of this runs without a GPU: `make batch-test`.

| Layer | Test |
|---|---|
| `runner` | done + file + same params → skipped; done + file + different `provider` → re-run |
| `runner` | an entry stamped `phase: local` is not reused by `run_one` once the stage stops being local-eligible; an entry with no `phase` key still is |
| `runner` | `force=True` bypasses the skip check without touching the batch id or other stages' entries |
| `drain` | `--phase-a-only` never calls `provision`, including when Phase A exits 3 |
| `drain` | `--phase-a-only` **without** `--yes` still runs Phase A rather than printing `DRY RUN.` (§6.1's ordering trap) |
| `run` | `start_phase_a` never appends `CONFIRM=yes`; a Phase A in flight does not make `drain_running()` True |
| `bot` | the stock-out card offers Retry; Retry reaches `_do_resume`; `Đợi`'s copy no longer says "/confirm again" |
| `bot` | the card's "N/M preserved" count agrees with what `local_tryon_reusable` would actually skip, including when params changed |
| `bot` | with a matching journal, `_do_confirm` sends the chooser and has **not** called `start_drain`; "Reuse" → `resume=True`; "Re-run" → `resume=True, force_local=True`; the second entry does not rewrite the manifest |
| `bot` | the post-Phase-A panel reaches `start_drain(resume=True)` with `_STATE` **cleared** — the §6.3 condition that rules out `_do_confirm` |
| `bot` | a live Phase A blocks `/clear` and does not route the job to the mailbox |
| `bot` | a manifest with no local try-on reaches `start_drain` on the first tap, unchanged |

The `CONFIRM=yes` invariant is currently only *documented* (`run.py:246` asks for a grep). It should
become a test that greps, since this change adds a second subprocess launcher next to the one that
holds it.

Two existing tests must change, both pinning behaviour this design deliberately alters:
`test_stock_out_offers_wait_switch_migrate_and_subscribe_buttons` (`test_batch_bot.py:4515`) and
`test_confirm_calls_start_drain_once_with_dry_run_false` (`:3349`).

### Verification boundary

Unit tests prove the journal logic, the button wiring and the subprocess argv. They do **not** prove
that a real drain rents a pod, runs Phase B and destroys it — that needs `make gpu-smoke` or a real
batch, which costs money. Anything reported as verified here distinguishes local unit coverage from
paid end-to-end validation, per QWEN.md's testing rule.

---

## 9. Follow-ups recorded, not done

- `/batches` — the spec's answer to a deferred batch being lost in scrollback. Until it exists, the
  Retry button lives only on a card that scrolls away.
- Auto-start on stock, if the repo ever decides unattended renting is acceptable.
- Whether `run_one` can get a params check that is immune to default-drift — e.g. comparing only keys
  the manifest explicitly set, rather than the full effective param dict.
