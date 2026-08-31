# Telegram batch control — design

Date: 2026-08-30 · Status: approved for planning · Supersedes nothing

Run batches from a phone. Today `make batch` requires sitting at the Mac: sorting material into
`~/materials/<slot>/`, editing a YAML, and typing `make`. This design moves the trigger and the
runner onto an always-on VPS driven by a Telegram bot, so a batch can be assembled and started from
an iPhone while the Mac is asleep or elsewhere.

Why *why* this shape and not others: see [Rejected approaches](#rejected-approaches). Every number
below is either measured (with its date and source) or explicitly marked as unverified.

---

## 1. What the user actually asked for

Two pains, both confirmed in the design conversation:

1. **Tied to the Mac.** The trigger has to be reachable from a phone. The Mac is a laptop that
   sleeps and travels, so it cannot be in the critical path.
2. **Sorting folders and writing YAML by hand.** Assembling a run is dropping four files into four
   labelled slots, repeated N times. That is a form, not a folder tree.

One reframe came out of the conversation and drives the whole design:

> The user does not think in *batches*. They think in *jobs*, arrived at throughout the day. Batches
> exist only because a pod costs money and work has to be amortised over one rental.

So the phone adds **individual jobs to a basket**. The machine assembles a batch out of the basket
at drain time. The YAML manifest still exists — it is just generated rather than typed.

---

## 2. Non-goals (v1)

Deliberately cut, each for a stated reason:

| Cut | Why |
|---|---|
| Web UI | The user chose Telegram after seeing both. Revisit only if the chat card proves inadequate in real use. |
| Cloudflare R2 / external object storage | Material arrives via Telegram and leaves via Telegram. R2 would be a second vendor solving nothing. Telegram itself is the archive: it stores originals indefinitely and `file_id` lets the bot reuse a file without re-uploading. |
| Auto-drain on a queue threshold or a schedule | The user presses Run. Spending is a decision, not a cron job. |
| Multi-user | One `TG_ALLOWED_USER_ID`. |
| Automated EU-CZ-1 failover | It creates and deletes network volumes, and deleting the old one has a known failure mode involving `THROTTLED` serverless workers ([gpu-pod.md](../../gpu-pod.md#serverless-throttled)). Unattended volume surgery is the wrong thing to automate first. The bot surfaces the runbook instead. |
| Running the runner on the pod itself | Considered and dropped — see [Rejected approaches](#rejected-approaches). |

---

## 3. Architecture

```
iPhone · Telegram  (attach as File, never as Photo)
      │
      ▼
VPS — always on, ~EUR 4/month
      ├── telegram-bot-api      self-hosted, Docker. Lifts 20MB/50MB to 2GB.
      ├── bot                   card state machine · basket · commands
      ├── scripts/batchlib/     the existing runner, UNMODIFIED
      ├── watchdog              separate process, separate clock
      └── .env                  GEMINI_API_KEY · RUNPOD_API_KEY · TG_BOT_TOKEN
      │                         · TG_ALLOWED_USER_ID · POD_VOLUME_ID · …
      ▼  HTTPS via Cloudflare Tunnel
RunPod pod — rented at drain, destroyed at the end
```

`batchlib` is imported as a library and not modified. Its 239 unit tests keep their value, and
`make batch` on the Mac keeps working unchanged for debugging.

### Why an always-on host is unavoidable

The pod is destroyed when idle, so something must be alive to receive the trigger and rent it. The
Mac is not that something (it sleeps). Hetzner CX22 (2 vCPU / 4 GB / 40 GB, ~EUR 4/month) is cheaper
than any RunPod CPU pod and is not billed by the hour.

---

## 4. Ingest and the quality gate

The user's stated worry was that Telegram degrades media. It splits into two separate problems with
different answers.

### 4.1 Compression — a discipline problem, enforced by refusal

Telegram only re-encodes media sent as *photo* or *video*. Sent as **File / Document** the bytes are
untouched. On iOS that means `📎 → File`, not the Photos tab.

The bot **rejects every non-File message kind outright** (`bot.NON_FILE_MEDIA`) and accepts only
`message.document`. This converts a silent quality loss into a loud error — the same principle as
the four silent param traps in [batch-runner.md §2.4](../../batch-runner.md).

> **Corrected 2026-08-31 after measuring.** This section originally specified rejecting
> `message.photo` alone, and the implementation matched it. But the iOS Photo/Video tab — the
> default tab, whose "1080p" option reads as lossless — delivers a driver as **`message.video`**,
> not `message.photo`. That matched neither the photo branch nor the document branch, fell through
> to the text handlers as `""`, matched no command, and **the bot replied nothing at all**. The
> most likely wrong move a user can make produced silence, which is worse than the loss it hides:
> a refusal can be acted on, silence looks like a broken bot.
>
> It is the first mistake made while taking the §13 measurement, on the first attempt. The refusal
> now covers `photo`, `video`, `animation`, `audio`, `voice`, `video_note` and `sticker`, and the
> message carries the measured cost so the reason is not a matter of trust. See §13 item 1 for the
> numbers.

### 4.2 The size caps — corrected 2026-08-31, and the binding one is the OUTPUT

The public Bot API caps bot **downloads at 20MB** and **uploads at 50MB**.

> **This section originally claimed "the user's drivers are 20-30MB, so the standard API fails on
> the common case." That was wrong, and it was never measured** — it came from the user's own
> off-hand estimate in the first conversation, which this document then restated as a fact and
> illustrated with an invented `22.8 MB` figure. Measured 2026-08-31 across every file in
> `~/Desktop/materials/drivers/`: **1.3MB to 15MB**, all 720x1280 or smaller, every one of them
> comfortably under the 20MB download cap. On ingest, the public API would have worked.

The cap that actually binds is on the way **out**. The largest video this repo has delivered is
**41MB** (`out/2026-08-23-1732/_final/nhanvat3__dandong8.mp4`, measured 2026-08-31) against a 50MB
upload cap — an 18% margin, and `targetRes: 2k` or a longer driver crosses it. Self-hosting is
therefore still right, but for delivery, not for ingest.

The second reason is not about size at all: with a local server `getFile` returns an **absolute
path on disk**, so the bot reads the file the client uploaded instead of downloading it back. That
removes a whole transfer from every job regardless of any cap.

Self-hosting `telegram-bot-api` (tdlib) lifts both to 2GB, and gives a better property: with a local
server, **`getFile` returns an absolute path on disk**. The file Telegram receives lands directly in
the server's directory and the bot reads it — no download step at all.

One-time manual step: call `logOut` against `api.telegram.org` before pointing the bot at the local
server, or the bot silently stops receiving updates.

### 4.3 Measure on arrival, do not trust the channel

Neither Telegram nor a browser upload can be trusted by faith — iOS Safari's `<input type=file>`
transcodes unpredictably depending on the `accept` attribute and whether the source is the camera or
the library. The answer is not to pick a "safe" channel but to **verify on arrival**.

`ffprobe`/`exiftool` runs the moment a file lands, and the result goes into the job card:

```
driver  m4.MP4   1080x1920 · H.264 · 12.4 Mbps · 30.1s · 22.8 MB
```

If a 4K driver arrives as 720p at 1.1 Mbps, that is visible **before** any GPU is rented. This is the
same principle as `batch-validate` and `gpu-preflight`: free gates that fire before spending.

Two things fall out of measuring anyway:

- **Preset is derived, not typed.** [batch-runner.md](../../batch-runner.md) currently warns that
  "preset is chosen from each driver's *real* `ffprobe`, make does not measure it". The bot measures,
  so it proposes the preset and flags over-length drivers (`m4` at 30.1s exceeds `drv-30s`; swap
  trims the tail, motion lowers fps and keeps the length).
- **`.HEIC` from iPhone** is converted to PNG on the VPS, and the card says so explicitly.

---

## 5. Assembling a job — a live card, not a transcript

The known weakness of chat is that a transcript is the wrong data structure for slot assignment:
correcting the outfit of job 3 inside a 25-message scroll is miserable. The fix is that **the bot
keeps one message and edits it in place**. The user never scrolls back.

```
Job in progress          recipe: swap-30s-720p

character   OK  c1.jpeg    1024x1536 · 412 KB
outfit      OK  o6.jpeg    1200x1600 · 288 KB
background  --  not used by this recipe
driver      OK  m4.MP4     1080x1920 · H.264 · 12.4 Mbps · 30.1s · 22.8 MB
                WARN 30.1s > drv-30s ceiling, swap trims 0.1s off the tail

[ change recipe ]  [ replace driver ]  [ + add to basket ]
```

Four rules keep the tap count low:

- **Recipe first.** The recipe decides which slots exist — `character-swap` has no `background`.
- **A video is always the driver, never asked.** This is structural, not a guess: `driver` is the
  only slot that takes video. Guessing from filenames would not be safe; this is.
- **An image is asked once**, with inline buttons `[character] [outfit] [background]`. A caption of
  `outfit` on the file skips even that.
- **Shared material is carried over.** In the user's most recent real manifest
  (`batch/2026-08-28-lanczos-6cap.yaml`) `c1` appears in 5 of 6 runs. After adding to the basket the
  next card pre-fills `character: c1 (kept)` and reuses the `file_id` — no second upload.

```
Basket — 4 jobs
1  c1 · o4 · b1 · m1    tryon-motion-1080p60
2  c1 · o8 · b1 · m2    tryon-motion-1080p60
3  c1 · o6 · m4         swap-30s-720p
4  c2 · s3              swap-15s-720p

est. ~52 min GPU ~ $0.86
[ Run ]  [ edit 3 ]  [ delete 4 ]
```

### 5.1 Recipes

A recipe is a named template of `defaults` living in `batch/recipes/*.yaml`, reviewed through git,
comments intact. The phone never touches the 39 motion params.

Per-job, exactly one field is exposed: **`preset`** (proposed from `ffprobe`, overridable). This
matches the user's real manifests, where everything sits in `defaults` and only `preset` (and
occasionally `quality`) varies per run — confirmed by the user during design.

Consequence: [trap #4](../../batch-runner.md) — mistyping a third variant of a param name so
`params.get()` returns `None`, the job runs anyway, bills anyway, and silently uses the default —
becomes **structurally impossible** rather than merely caught by validation.

---

## 6. The manifest gate

`[ Run ]` does **not** rent a pod. It builds the manifest, runs `batch-validate` (free), and shows
the result for approval:

```
batch/2026-08-30-2140.yaml     validate clean · 0 errors
4 runs · est. ~52 min GPU ~ $0.86
```
```yaml
# Generated by bot 2026-08-30 21:40. ffprobe measured at ingest:
#   m1 14.8s 1080x1920 12.4Mbps -> drv-15s
#   m2 34.1s 1080x1920 11.8Mbps -> drv-30s (OVER by 4.1s; motion lowers fps, keeps length)
#   m4 30.1s 1080x1920 12.4Mbps -> drv-30s (over by 0.1s; swap trims tail)
#   s3 15.0s 1080x1920  9.2Mbps -> drv-15s
defaults:
  ...
```
```
[ Confirm and run ]   [ Edit ]   [ Back to basket ]
```

Four deliberate properties:

- **The file written to disk is byte-identical to what was displayed.** Not a prettified rendering.
- **The comments the bot writes are numbers it measured**, with the date — matching the repo's
  existing convention of recording the measurement rather than the argument. Today the user runs
  `ffprobe` by hand and types these comments in. Six weeks later the manifest is still self-contained.
- **`batch-validate` runs before display**, so what is reviewed is already syntactically clean.
- **The ownership boundary survives.** [batch-runner.md §0](../../batch-runner.md) states the `.yaml`
  belongs to the user and nothing writes to it. The bot is a **new** author, but it writes exactly
  once, before the run, and only on Confirm. The runner still never touches it; the journal still
  goes to a separate `state.json`. The invariant holds; only authorship changed.

**`[ Edit ]` has two paths:** go back and fix cards, **or send a `.yaml` file back to the bot** — it
validates and uses it verbatim. That second path is the escape hatch: the phone can never box the
user in, and it doubles as "re-run yesterday's manifest".

This gate is also what makes the user's choice of full autonomy safe: the spending decision is the
Confirm button, and everything after it is automatic.

---

## 7. Drain sequence

| # | Step | GPU billing |
|---|---|---|
| 1 | Write `batch/<ts>.yaml` (the approved bytes) | — |
| 2 | **Local Gemini try-on** for `provider: gemini` runs — pure HTTP, no GPU | no |
| 3 | Provision pod → `gpu-wait` → `gpu-bootstrap` | **starts** |
| 4 | `batchlib` runs the batch, sequentially | yes |
| 5 | Pull diagnostics (§9) | yes |
| 6 | `gpu-destroy` + verify the pod is really gone | stops |

Step 2 before step 3 preserves the existing design ([batch-runner.md §2.9](../../batch-runner.md)):
a Gemini 429 is discovered before the clock starts.

**Known trade-off carried over unchanged:** try-on run off-pod does not get
`TRYON_PRODUCT_AUTOCROP`, because the background-removal service only exists on the pod. Same
manifest can therefore produce different framing local vs on-pod, worst when the product is small in
frame. Unchanged from today; not made worse by this design.

Progress is again **one edited message** (edited about every 30s, well under Telegram's flood
limits):

```
Running batch/2026-08-30-2140.yaml
pod RTX 5090 · EU-RO-1 · $0.99/h · alive 12m

1 c1-o4-m1-b1   OK tryon  OK motion  .. enhance 3m
2 c1-o8-m2-b1   OK tryon  .. motion 1m
3 c1-o6-m4      queued
4 c2-s3         queued

watchdog: destroy at 00:40
[ Stop and destroy now ]
```

---

## 8. Money safety — four tiers

The user chose "rent automatically, destroy automatically". A forgotten pod is therefore the
top new failure mode: $0.99/h for the GPU, and container disk keeps billing even while stopped.
Reference point from [gpu-pod.md](../../gpu-pod.md#pod-max-hours): real usage measured 2026-07-24 →
2026-08-07 was 0.85 h/day ≈ $25/month, but **one** forgotten pod left for a month is **$713**.

### Tier 0 — `POD_MAX_HOURS` (already exists, RunPod-side)

`pod-provision.sh` already passes `--stop-after` to `runpodctl pod create`, default 8 hours. This
fires **inside RunPod**, so it survives the VPS being destroyed, unplugged, or never recovering. It
is the strongest net precisely because none of our code is involved.

Two caveats it comes with: `--stop-after` *stops*, it does not terminate, so container disk keeps
billing; and it is **not available for CPU pods** (no auto-stop field in `POST /v1/pods`) — another
reason automated EU-CZ-1 failover, which uses temp CPU pods, stays out of v1.

**Change proposed:** 8 hours was sized for a human who might work a long session. A bot-driven drain
has an estimate, so it should pass a tighter `--stop-after` of `ceil(estimate x 2 + 1h)`, still
capped by `POD_MAX_HOURS`.

### Tier 1 — dead-man's switch

A `pod-lease.json` is written at provision time. The runner **extends** it after each completed
stage. If the runner dies, extensions stop and the pod dies at the last extension. This is why it is
not a `finally: destroy` inside the runner: if the runner hangs or is OOM-killed, `finally` never
runs. It has to be a separate process with its own clock to be able to kill something already dead.

The extension length is derived, not guessed: **the timeout of the stage currently running, plus 15
minutes of slack.** Stage timeouts are already declared in `scripts/batchlib/pipelines.py` — tryon
20, motion 60, character-swap 60, enhance 90 minutes. Anything shorter would let the watchdog kill a
stage the runner still legitimately considers alive; anything longer wastes money after a crash.
A runner that dies during enhance therefore costs at most 105 minutes, not a night.

### Tier 2 — absolute ceiling

`WATCHDOG_ABS_MAX = sum of the timeouts of all stages not yet done, + 30 minutes`, ignoring
extensions entirely. Catches the runner that is *alive* but stuck in a loop, happily extending its
own lease while burning money.

For a large batch this converges on `POD_MAX_HOURS` and stops adding a tighter deadline — that is
acceptable, because tier 1 is the tight one and tier 2 still does something tier 0 cannot: it
**destroys** rather than stops, so container disk billing ends too.

### Tier 3 — reconciliation against RunPod, every 10 minutes

Ask the RunPod API "do I own any pods?". A pod with no lease file is an orphan: notify, grace
period, destroy. This is the net for the worst case — the state file was lost — and it runs on VPS
boot too, covering a reboot mid-batch.

### Reporting cost

The pre-run figure is labelled an **estimate** (measured minutes × known hourly rate). The number
reported after destroy comes from `runpodctl billing pods` — the real invoice. Never from
`currentSpendPerHr`: that path already produced a 33x error in this repo
([gpu-pod.md](../../gpu-pod.md#hoa-don-that)).

### A new risk this design creates: who owns the pod

Today the Mac's root `.env` holds `GPU_SSH_HOST/PORT` and the Mac is the only place `make gpu-*` is
typed. Adding the VPS creates **two machines that each believe they own the pod** — double
provisioning, or the Mac running `gpu-destroy` mid-batch. This is the same class of drift that made
`make check-job-types` necessary (four hand-copied lists that diverged).

**Decision: the VPS is the sole owner of pod lifecycle.** `make gpu-preflight` on the Mac gains a
warning when the VPS holds a live lease. Tier 3 would catch the collision eventually, but only after
money was spent; blocking beforehand is better.

---

## 9. Failure handling

### Pull diagnostics *before* destroying

[batch-runner.md §4](../../batch-runner.md) says plainly: "if the batch fails, don't destroy the pod
— that is exactly when you need it, to read the worker log." Auto-destroy contradicts that directly.

Resolution: on any failed run, the bot collects diagnostics **before** killing the pod:

- `make gpu-logs LOG=worker` tail → `out/<batch>/runs/<run>/pod-worker.log`
- `GET /jobs/:id/logs` for every failed job — the only place `face_crop=vitpose` vs the DWPose
  fallback is recorded, because it is written with `api_log` (`linux.py:3962`) and therefore is
  **not** in `~/.pm2/logs/worker-out.log`. This exact trap has already cost a debugging session once.

Plus a `[ keep the pod 30 more minutes ]` button, which pushes the watchdog deadline, for when the
user wants to poke at it live.

Net effect: this is **better than today**, because today the user has to *remember* to pull logs
before typing `gpu-destroy`, and the bot does not forget.

A failed run still does not kill the batch (unchanged default). The bot reports which run failed,
with `[ view log ]` and `[ re-run this run ]` — the latter mapping to the existing `batch_rerun`.

### Out of RTX 5090 stock

RTX 5090 exists in exactly two RunPod data centres, EU-RO-1 and EU-CZ-1 (measured via
`get-gpu-type`, product=POD, 2026-08-29). The repo does **not** rotate GPU types automatically.

```
No RTX 5090 in EU-RO-1. No GPU time spent yet.
Try-on 4/4 finished and is preserved.

[ Wait for a 5090 and start automatically · give up after 6h ]
[ RTX PRO 4500 · $0.49/h · ~104 min ~ $0.85 ]
[ Defer — resume later via /batches ]
[ EU-CZ-1 failover -> send me the manual runbook ]
```

The wait option costs $0 and is what an always-on VPS is for: poll capacity every 15 min, start when
stock appears, notify. It replaces a "retry in 15 minutes" button that would have made the user come
back and press it again.

Failover economics, corrected 2026-08-30 (the previously quoted 25-30 min was a stale single-stream
extrapolation; see [gpu-pod.md](../../gpu-pod.md#volume-migrate)):

| Option | Wall-clock for a 52-min batch |
|---|---|
| RTX PRO 4500 in EU-RO-1 | ~104 min (2x slower, $0.49/h) |
| EU-CZ-1 failover | ~20 min setup + 52 min = ~72 min, plus ~$0.13/h temp CPU pods and a second volume at ~$7/month until cleaned up |

Break-even is around a 20-minute batch: longer than that and failover is faster in wall-clock. The
~3-minute figure in the measurement table is the **transfer** at 8 parallel `dd|ssh cat` streams
(~427 MB/s for 79GB); the rest of the ~20 min is booting two temp CPU pods, the `rsync -avnc`
checksum verify, and provisioning the real GPU pod. Do not use 16 streams — it gains ~20 MB/s and
drops 3 of 16 connections to OpenSSH `MaxStartups`.

### Defer means preserve, never discard

"Cancel" must not appear twice with two meanings. At the manifest gate nothing has been spent, so it
is `[ Back to basket ]`. At the stockout prompt **Gemini money has already been spent**, so the
button is `[ Defer ]` and it preserves everything:

| Item | On defer |
|---|---|
| `batch/<ts>.yaml` | kept — the approved bytes |
| `01-tryon.png` per run | **kept** — `RESUME=1` skips it, Gemini is not called twice |
| `state.json` | kept — the journal knows which stages finished |
| Material on VPS disk | kept — retention **must not** collect files referenced by a pending batch |

This is not new behaviour; it is reaching the behaviour that already exists
([batch-runner.md §2.9](../../batch-runner.md): try-on images survive a batch that stops at the pod
check, and are not re-billed on `RESUME=1`). Resuming later is `RESUME=1`, jumping straight to
provisioning.

**`/batches`** lists deferred batches with `[ resume ]`. Without it a deferred batch is lost in
scrollback — the exact transcript failure mode this design otherwise avoids.

`[ Stop and destroy now ]` mid-run follows the same rule: completed stages are kept, resume is
`RESUME=1`. One honest caveat the bot must state before the user taps it: the **in-flight** stage is
lost with the pod. Normally `RESUME` re-attaches to the existing `job_id`, but a destroyed pod
returns 404 and the stage is resubmitted. The bot shows how many minutes that costs.

Genuine deletion is separate: `[ delete batch ]` inside `/batches`, with one confirmation.

---

## 10. Delivery

`_final/*.mp4` is sent with **`sendDocument`**, not `sendVideo`.

Reason: `sendVideo` may let Telegram re-encode for streaming, and the quality work in this repo
measures background chroma, skin exposure and hair jitter. **Quality must never be judged on a
re-encode.** A document still plays when tapped — it just plays the original bytes. That keeps the
"judge motion on video, not stills" lesson intact while not judging the wrong file.

Alongside: a summary from `_index.tsv` — seconds and bytes per stage, total GPU minutes, and the
real invoiced cost. Plus a `[ show 01-tryon ]` button, because when the final video looks wrong the
first question is always "what did try-on produce", and that should be one tap rather than an SSH
session.

---

## 11. Security

The VPS holds `RUNPOD_API_KEY`, `GEMINI_API_KEY`, the bot token, and the SSH key to the pod. Bot code
goes in the repo; `.env` does not. `motions-studio/setup/scrub-secrets.sh --check` remains the gate
before every commit — the repo is public.

The only thing between a stranger and a button that rents a $1/hour GPU is the
**`TG_ALLOWED_USER_ID` whitelist**. Any other sender is ignored silently. The bot is never added to
a group.

---

## 12. Scope and cost

**Build:** `scripts/tgbot/` (imports `batchlib`, does not modify it) · `batch/recipes/*.yaml` ·
`scripts/vps/` (docker-compose for `telegram-bot-api`, systemd units for bot and watchdog) · unit tests
for the card state machine and the `ffprobe` → preset mapping, wired into `make batch-test` (free, no
pod).

**Recurring cost added:** VPS ~EUR 4/month. Nothing else. Network Volume and GPU pricing unchanged.

**One-time manual step:** `logOut` the bot from `api.telegram.org` before switching it to the local
Bot API server.

---

## 13. Unknowns — measure before building on them

1. ~~**Does Telegram preserve bytes for a `.MP4` sent as File?**~~ **Answered 2026-08-31: yes,
   exactly.** Sent `out/2026-08-23-1540/_final/nhanvat1__dandong-3.mp4` (24,558,897 bytes,
   1088x1920, 13,275 kbps) from the iPhone client through a local `telegram-bot-api` server. It
   arrived as `message.document` at **24,558,897 bytes, sha256 `fce94a50…b217` — identical**, and
   `ingest.probe()` read back the same 1088x1920 / 14.8s / 13,275 kbps.

   The same file, same phone, sent via **Photo/Video with the "1080p" option** instead: resolution
   and frame count survive untouched (1088x1920, 444 frames, 14.8s) but the bitrate is **halved,
   13,196 → 6,603 kbps, and the file loses 49.8%** of its bytes. So the damage the File rule
   prevents is not a smaller frame — it is every frame re-compressed, which is invisible in a file
   listing and invisible in a still, and is exactly what the chroma/exposure/jitter measurements in
   this repo are sensitive to. §4.1's "attach as File, never as Photo" now has a number behind it.

   Two side findings from the same run, both worth keeping:
   - **Telegram's own `video.width`/`video.height` are not the stream's.** It reported `480x848`
     for a stream that ffprobe reads as `1088x1920`. Any code trusting those fields is wrong by
     nearly 2x per axis; `ingest.probe()` shells out to ffprobe and so never sees them.
   - **`getFile` really does return a path containing the bot token**
     (`/var/lib/telegram-bot-api/<token>/documents/file_1.mp4`), confirming finding I5 was real
     rather than theoretical. Staging into `batch/tg-staging/` is what keeps it out of messages.
2. **How long does `gpu-bootstrap` take when models are already on the Network Volume?** It is added
   to every single drain and is currently unmeasured. It also sets the floor for the stockout
   economics table in §9.

---

<a id="rejected-approaches"></a>
## 14. Rejected approaches

**n8n.** Its Telegram node calls the same `getFile` and hits the same 20MB wall, so it does not solve
the actual blocker. It adds a ~500MB Docker service and a second place where logic lives, while the
hard parts here — slot assignment, pod lifecycle, resume journalling — are custom code it can only
shell out to. n8n earns its keep connecting SaaS you do not control; here everything is controlled.

**OpenClaw / Hermes Agent.** Genuinely close fit for "message me from anywhere", and this repo already
exposes an MCP server (`scripts/batch_mcp.py`, four tools) that either could drive. Rejected for v1
on three grounds: an LLM holding the RunPod key and deciding when to destroy is a different risk from
a watchdog counting seconds; both are moving very fast right now; and neither solves media quality —
still Telegram, still 20MB, and an LLM mislabelling an outfit as a background burns a real $1.
Strictly additive later: v1 produces an HTTP surface either could be pointed at.

**Web mini-app for upload.** Better for slot assignment on paper, but iOS Safari's `<input type=file>`
transcodes unpredictably, so it does not actually buy a clean channel — and the user chose Telegram
after seeing the comparison. The live-editing card in §5 is the mitigation for chat's real weakness.

**Cloudflare R2 + iOS Shortcut upload.** Would bypass Telegram's limits and Cloudflare Tunnel's 100MB
request-body cap on the free plan. Dropped once the surface became Telegram: it splits the workflow
across two apps and adds a vendor, while the self-hosted Bot API server already lifts the ceiling to
2GB.

**Running the runner on the pod.** Attractive: `out/` and the journal would live on the Network Volume
and survive `gpu-destroy`, and material uploads would go over `localhost` instead of through
Cloudflare Tunnel's 100MB limit. Dropped as premature — material is ~30MB today, nowhere near the
cap, and the journal survives equally well on an always-on VPS. The cost was two deploy targets and
debugging over SSH. Revisit if material approaches 100MB.

**A `finally: destroy` in the runner instead of a watchdog.** Does not survive the case it exists for:
a hung or OOM-killed runner never reaches `finally`.
