# VPS setup

The VPS exists for one reason: something has to be awake to rent a pod and to guarantee it dies.
The Mac is a laptop that sleeps and travels, so it cannot be that something.

## Box

Hetzner CX22 or equivalent — 2 vCPU / 4 GB / 40 GB, ~EUR 4/month. Cheaper than any RunPod CPU pod,
and not billed by the hour. 40 GB is enough for material plus `out/`; keep it that way with
`make batch-clean KEEP=3`.

## Install

```bash
apt update && apt install -y python3 git rsync ffmpeg make
# runpodctl: see https://docs.runpod.io/cli/install
git clone <this repo> /opt/motion-clone && cd /opt/motion-clone
```

## Secrets — copy, never commit

`.env` and `motions/.env` are gitignored and must be copied from the Mac by hand:

```bash
scp .env       vps:/opt/motion-clone/.env
scp motions/.env vps:/opt/motion-clone/motions/.env
```

The VPS needs `GEMINI_API_KEY` (local try-on), `RUNPOD_API_KEY`, `DOMAIN`, `POD_VOLUME_ID`, and the
SSH private key `pod-bootstrap.sh` uses to reach the pod. Nothing here is ever committed — the repo
is public and `motions-studio/setup/scrub-secrets.sh --check` gates every commit.

## First run must be manual

`load_settings()` needs `NUXT_MOTION_API_KEY` in `motions/.env`, and that value is written by
`gpu-bootstrap` when it configures a pod. On a brand-new VPS it does not exist yet, so
`drain.py` would fail before renting anything. Do one manual cycle first:

```bash
make gpu-preflight
CONFIRM=yes bash scripts/pod-provision.sh && make gpu-wait && make gpu-bootstrap
make gpu-destroy
```

After that, `motions/.env` has the key and drains are unattended.

## Watchdog

```bash
cp scripts/vps/pod-watchdog.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now pod-watchdog
systemctl status pod-watchdog          # must be active (running)
make watchdog-dry                      # must report no lease and destroy nothing
journalctl -u pod-watchdog -f          # watch it tick
```

## Pod ownership — the rule that matters

**From this point the VPS is the sole owner of pod lifecycle.** Do not run `gpu-provision`,
`gpu-up` or `gpu-destroy` on the Mac. Two machines each tracking a pod in their own `.env` means
double provisioning, or the Mac destroying a pod mid-batch — at $0.99/hour.

What actually protects you, and what does not:

- **`make gpu-preflight` on the Mac warns when a live pod named `motion-transfer` exists** — it asks
  `runpodctl pod list -o json`, which is the only state both machines share. It is a warning, not a
  block: it cannot tell a pod the VPS rented from one you rented yourself, and it says nothing about
  whether a batch is running on it. Check `make watchdog-dry` on the VPS before acting on it.
- **It does not read `batch/pod-lease.json`.** That file is written by `drain.py` on the VPS and is
  gitignored, so it never exists on the Mac. A guard that checked it (as this one did until
  2026-08-31) could never fire on the machine it was written for.
- **Nothing prevents double ownership.** There is no lock, no shared lease, no handshake. The rule
  above is a convention enforced by one warning and by tier-3 reconciliation — and tier 3 catches
  the collision only after the money is spent, and only for pods named `motion-transfer` that no
  lease claims, after a 10-minute grace window.

The Mac keeps `make batch` for debugging against a pod the VPS already rented. That is read-only
with respect to ownership and is fine.

## Running a batch

```bash
make drain FILE=batch/2026-08-30.yaml               # dry run: prints the plan, rents nothing
make drain FILE=batch/2026-08-30.yaml CONFIRM=yes   # rents, runs, destroys
```

## Telegram bot

### Prerequisites, in this order

1. `scripts/vps/bot-api.env` **must exist before `docker compose up`** — the server exits
   immediately without it. It is gitignored; write it by hand on the VPS:
   ```bash
   cat > scripts/vps/bot-api.env <<'EOF'
   TELEGRAM_API_ID=<from https://my.telegram.org>
   TELEGRAM_API_HASH=<from https://my.telegram.org>
   EOF
   ```
   These are an **app registration**, not the bot token. The bot token lives in the root `.env` as
   `TG_BOT_TOKEN`, along with `TG_ALLOWED_USER_ID` and (optionally) `TG_API_BASE`
   and `TG_PIPELINE`.

   `TG_PIPELINE` picks which pipeline a new job starts on — one of the names
   `/pipeline` lists, default `tryon-motion-enhance`. It is validated at
   startup and an unknown name exits 2, because the alternative is a
   confusing manifest-validation failure on the phone hours later, after the
   material has already been uploaded. `/pipeline <name>` overrides it for the
   current job, and that choice is saved with the draft — so a restart resumes
   the flow you picked, and only a brand-new job starts from this value. Set it
   to whichever flow you use most.

2. The server's storage is bind-mounted at `/var/lib/telegram-bot-api` **on both sides**. With
   `TELEGRAM_LOCAL=1`, `getFile` returns an absolute path on the container's filesystem and `bot.py`
   opens that exact string on the host, so the two namespaces have to agree — see the comment in
   `telegram-bot-api.yml`. Create it and make it readable by whoever runs `bot.py`:
   ```bash
   mkdir -p /var/lib/telegram-bot-api
   ```

3. `ffmpeg` (for `ffprobe`) is in the `apt install` line above. The bot refuses any file it cannot
   probe, so a missing `ffprobe` means nothing is ever accepted.

### Bring it up

```bash
docker compose -f scripts/vps/telegram-bot-api.yml up -d
curl -s http://127.0.0.1:8081/bot<token>/getMe        # must contain "ok":true
```

**One-time and irreversible in one direction:** call `logOut` against `api.telegram.org` for this
bot *before* pointing it at the local server, or it will silently stop receiving updates:

```bash
curl -s "https://api.telegram.org/bot<token>/logOut"
cp scripts/vps/motion-bot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now motion-bot
```

### Commands

| Command | What it does |
|---|---|
| (send a File) | measures it, replies with the description, byte count and short `sha256`, and asks which slot if it is an image — as tappable buttons |
| `character` / `outfit` / `background` | answers the slot question by typing, for the file at the head of the queue. The buttons do the same thing; both run `_answer_slot` |
| `/job` | what is assembled: each filled slot with its measurements, what is still missing, what is waiting for a label — plus re-label and Start over buttons |
| `/pipeline` | lists the pipelines and shows the current one |
| `/pipeline <name>` | switches this job's pipeline, keeping the slots the new one still uses and naming any it drops |
| `/confirm` | **spends money.** Rents an RTX 5090 at $0.99/hour and runs the drain |
| `/status` | progress for this chat's job, read from the journal — still works after the pod is destroyed |
| `/result <manifest>.yaml` | the finished video(s), or the failure logs already pulled to disk |
| `/tryon <batch-id>` | just `01-tryon.png`, when the final video looks wrong |
| `/again` | rebuild the last submitted job from the same files — change one thing (usually the pipeline) and run it again |
| `/clear` | drop the job being assembled and delete its staged copies. Always asks first, and refuses while a drain is running |

`/status` with nothing running falls through to `/job` rather than dead-ending on "nothing
started" — which is true, but useless in the state `/status` is most often asked in.

### The low-bitrate warning

`ingest.quality_warning` flags a video whose bitrate is too low to be original material, in kbps
per megapixel so resolutions are comparable. It never blocks.

The threshold, 1000, comes from measuring all 64 videos on this machine on 2026-08-31:

| kbps/Mpx | file | |
|---|---|---|
| 417 | `IMG_6783.MP4` | sent as a File, so Telegram preserved it exactly — it was already a re-compressed copy before it was sent |
| 1397 | `s1.mp4` | the **lowest legitimate driver** in the set |
| 3227 | — | median of the 64 |
| 8421 | `nhanvat3__dandong8.mp4` | highest |

1000 sits between the two that matter: 1.4x below the lowest real driver, 2.4x above the known-bad
one. A first attempt at 1500 was rejected by this same survey because it flags `s1.mp4`, and a
warning that cries wolf on real material gets ignored on the file that matters.

This closes a gap spec section 4.3 left open. "Measure on arrival, do not trust the channel" was
implemented as far as `describe()` — the numbers were shown, and nothing judged them. The File rule
guarantees Telegram did no damage; it cannot guarantee the bytes were good before they were sent.

### The control panel

**One message per chat holds the whole job being assembled.** Every change —
a file accepted, a slot answered, a pipeline switched, a slot re-labelled —
re-edits that message instead of sending a new one. Before 2026-09-01 each step
was its own message, so a three-file job left eight fragments and the only way
to see the current state was to scroll or type `/job`.

```
🎬 tryon-character-swap-enhance          ← the panel (assembling)

✅ 👤 character · 1536×2720 · 4.9 MB
✅ 👗 outfit · 1080×1440 · 2.1 MB
⬜ 🎬 driver
⬜ 🖼 background — optional

▰▰▱ 2/3 · send the driver as a File
▸ details                                ← collapsed measurements
[🔁 character] [🔁 outfit]  [🗑 clear]
```

The bar counts **required** roles only: an unfilled optional slot must not make
the job look unfinished, because nothing is waiting on it.

Four rules hold it together:

- **It is frozen, never deleted, when money is committed.** The invariant is
  that nothing may spend $0.99/hour without the exact inputs it spent on being
  in the transcript — and an edited message keeps only its latest version. So
  `_freeze_panel` stops editing it and strips its keyboard, leaving the
  submitted job permanently in the chat above the progress message. Stripping
  works by *omitting* `reply_markup` from `editMessageText`, verified against
  the real API 2026-09-01: the returned `Message` carries `reply_markup` when
  the call passes one and does not when it omits one. (`forwardMessage` is not
  a way to check this — it drops inline keyboards from every message, so a
  control forwarded alongside reports "no keyboard" too.) `/clear` is
  the opposite case and *does* delete it: nothing was spent, so there is no
  record to keep, and a panel describing files that no longer exist is the
  stalest thing in the chat.
- **It moves down when it drifts.** Editing is silent and preferred, but a
  panel five messages up is one the user has to hunt for. Message ids increment
  by one per message in a private chat, so `newest_seen - panel_id` *is* the
  drift in messages — no guessing. Past `_PANEL_DRIFT_MAX` (3) it is deleted and
  re-sent at the bottom. `/job` forces that move, because an explicit "show me
  now" answered by a silent edit somewhere above reads as the command doing
  nothing.
- **Run appears only once `make batch-validate` has actually passed.** Stricter
  than the screen it replaced, which offered Run beside a manifest that had just
  failed and leaned on `_do_confirm` to refuse the tap. A button that cannot
  work is not offered.
- **Its message id is persisted in the draft.** `motion-bot.service` is
  `Restart=always`; a bot that came back without the id would send a *second*
  panel while the first sat above it with live buttons — two keyboards for one
  job. The key is read with `.get()`, so a draft written by an older bot still
  loads rather than being set aside as corrupt.

The one-line acknowledgement that each step used to send ("replaced the previous
outfit — o.png") now lives on the panel as an italic note. The fidelity line
(bytes in == bytes out) stays a real message: it is evidence about one file at
the moment it arrived, and acceptance A6 compares it against the delivered
digest, so it must not be overwritten by the next upload.

### Presentation

Messages use `parse_mode="HTML"`, ordinary emoji as icons, and one collapsed
`<blockquote expandable>` per screen for the measurements. Verified against the
running server on 2026-08-31: bold/italic/`code`, `<pre>`, `<blockquote>`,
`<blockquote expandable>`, spoilers, emoji and `sendChatAction` all work on this
image.

Three rules, each with a failure mode behind it:

- **HTML, never MarkdownV2.** MarkdownV2 requires escaping fifteen characters
  including `.`, `-` and `!` — all of which occur in ordinary filenames and
  measurements. One unescaped character makes Telegram reject the whole message,
  so the user sees *nothing*. HTML needs only `&`, `<` and `>`.
- **Escape every interpolated value** with `bot._esc`, not just the ones that
  look risky. `_safe_name` already strips brackets from staged names, so nothing
  can carry one today; escaping keeps that from being load-bearing.
- **The headline line carries resolution and size only.** Duration and bitrate
  are diagnostic and live in the collapsed block — except a *warning*, which is
  repeated outside it on the review screen. That screen is the last thing read
  before $0.99/hour is committed, and anything needing a tap to reveal is
  something that gets skipped.

`FakeTg._check_markup` in the tests gates every message the suite produces
against both failure modes: HTML tags with no `parse_mode` (the user reads the
raw tags), and an unescaped bracket under `parse_mode=HTML` (Telegram rejects
the whole send, and nothing arrives).

**Image previews do not work from a local path.** Measured 2026-08-31:
`sendPhoto` with an absolute path returns `invalid file HTTP URL specified`, and
with a `file://` URI returns `can't find real file path` — because the path has
to exist *inside* the container, and only `/var/lib/telegram-bot-api` is
mounted, not the repo. Showing staged images back would need a real upload.

### Buttons

Every message offering a fixed set of choices carries an inline keyboard: the slot question, the
`/pipeline` listing, and the review screen's **Run**. Typing still works everywhere — a shared body
(`_answer_slot`, `_switch_pipeline_and_report`, `_do_confirm`) serves both, so the two cannot drift.

Two things worth knowing before changing any of it:

- **`callback_data` is capped at 64 bytes**, and one over-long button makes the whole `sendMessage`
  fail — the user sees no message at all, not a broken button. `Tg.keyboard` raises instead, naming
  the button, and a test walks every pipeline name through it.
- **Telegram never expires an inline keyboard.** There is no expiry and no way to make one
  single-use, so an old Run button stays tappable forever. That is why `run:go` carries the
  manifest's `mtime_ns`: any change to the job invalidates every button minted before it, so a Run
  offered for inputs the user has since replaced cannot rent a GPU. Tapping a stale one says the job
  changed and spends nothing.

`answerCallbackQuery` is called in a `finally` for every press. Skipping it leaves the client
spinning on the button and eventually reporting the bot as unresponsive, even when the work
succeeded.

### Progress, and what happens when it ends

`/confirm` sends one progress message and then keeps **editing that same
message** — it never posts a new one. `main()`'s poll loop calls `tick_progress`
after each `getUpdates`, and the long-poll timeout *is* the animation timer:
**2s while a drain is running, 50s otherwise.** It is keyed on the progress file
existing, so a bot restarted mid-render picks the fast cadence straight back up.

The message opens with ⚙️, not the panel's 🎬. The two sit next to each other
for the whole of a render and mean different things — what was submitted, versus
what is happening.

#### Animation: what is actually possible

Measured against this bot on the real API, 2026-09-01. The conclusion is narrow
and worth not re-deriving:

| Approach | Works? | Why it matters |
|---|---|---|
| `<tg-emoji>` custom emoji | **No — silently** | `sendMessage` returns `ok:true`; the message comes back with `entities:null` and only the fallback glyph. The Bot API grants custom emoji to bots that bought a username on Fragment. There is no error to catch and nothing renders wrong; the animation simply never exists. A test gates the string literal so it cannot be reached for again. |
| Animated `.tgs` stickers | Yes, but | `getStickerSet("AnimatedEmojies")` has 599 animated stickers including ⏳ ✅ ❌ 🎬. But a sticker message **cannot be edited** (`message can't be edited`) — only deleted — so it can never carry state. |
| `sendDice` | Yes | Genuinely animated, semantically wrong for progress. |
| **Re-editing the text** | **Yes** | The only way to get motion *inside* a message. This is what the bot does. |

Rate, measured the same day: 0.48, 0.91 and 2.02 edits/s all completed with
**zero** rejections. 2s was chosen over 1s because the animation reads the same
either way and half the calls is half the exposure to a flood limit that is not
published. A 429 is still handled — `TgError.retry_after` carries Telegram's own
number, `_ANIM_PAUSE` honours it, and the pause is checked *after* the
completion check so a cosmetic rate limit can never delay delivering a result.

**The spinner glyph was chosen on a real phone, not from a Unicode table.**
Seven candidate sets were sent to the user's iPhone on 2026-09-01: none rendered
as tofu, but the braille spinner `⠋⠙⠹⠸…` — the obvious choice on paper — is a
few faint dots that all but vanish against a dark chat background. It renders
and you still cannot see it, which fails the only job a liveness indicator has.
`_SPIN` is `◐◓◑◒`: four frames rather than ten is also the better trade at a 2s
cadence, since the eye reads *"that changed"* rather than *"that is rotating"*,
and a 90° jump per tick carries further than a one-tenth shift.

`frame` may change the spinner and the hourglass and **nothing else**. A test
strips those glyphs from two consecutive frames and asserts the remainder is
byte-identical. The bar stays discrete (`done/len(planned)`) because the journal
is discrete; smoothing it into a percentage would be inventing progress the
runner never reported. What the motion says is "this process is alive", which is
the one thing a journal genuinely cannot say — a drain that died mid-stage
leaves exactly the same `running` record as one still working.

The spinner also rides the `waiting for the pod` line. That is the ~10-minute
provision-and-bootstrap window where the journal says nothing at all: without it
the text is byte-identical every poll, every edit is swallowed as `message is
not modified`, and the fast cadence costs 25× the API calls to show nothing —
during the one phase where the only real question is whether it is alive.

If the user deletes the progress message, `edit_message` returns `False` and the
next tick rebuilds it and records the new id, rather than editing into the void
for the rest of a paid render.

A 5-minute throttle was added here and then removed the same day, on the user's
instruction. An edit sends no notification and adds no message to the chat, so
there is nothing to be spammed by — the throttle was solving a problem that does
not exist, and a knob that never fires is worse than no knob. (The claim that
prompted it, that an edit bumps the chat to the top of the list, was asserted
without being verified and is probably wrong.)

```
🎬 2026-08-31-2140
▰▱▱ 1/3 · character-swap running
✅ tryon · 351s
⏳ character-swap
⬜ enhance

⏱ 42 min on the pod · 💸 $0.69 so far
```

The bar's **denominator comes from the pipeline, not the journal**. The journal
records only stages that have already begun, so a bar computed from it alone
would read 1/1 while the first of three ran and never move; the stage list is
captured into `batch/tg-<chat>.progress.json` when the drain starts. That file
also holds the `message_id`, and it is on disk rather than in memory because a
68-minute job plus `Restart=always` means the process that sent the message is
often not the one that finishes it.

The money line is **elapsed, never predicted**: the pod bills from
`provisioned_at` whether or not a stage is moving.

When the drain ends, `tick_progress` edits the message one last time and then
calls `deliver_result` **unasked**. That closes the gap `deliver_result`'s own
docstring described — "nothing in this bot polls a drain to completion and fires
a callback when it finishes ... the reachable close-the-loop hook until a
completion poll exists". Before this the user had to remember to type `/result`,
with nothing telling them the job had finished, or failed.

On failure the report names **which stage broke**, from the journal, and inlines
the last 1200 characters of `pod-job.log` in a collapsed block as well as
attaching the file — opening a `.log` document on a phone is several taps and an
app switch, and the last few lines are usually the whole answer. The tail is
capped because a Telegram message is 4096 characters and a pod log is megabytes:
sending the whole thing would make the send fail, which is how an error report
becomes a second error.

Three ways a job can end quietly are each given a message rather than silence: no
batch recorded at all (the drain died before starting), outputs present, and
neither outputs nor any run marked failed.

### Where an unfinished job lives

`batch/tg-<chat_id>.draft.json` (gitignored) holds the slots, their probes, the pipeline and the
queue of files still awaiting a label. It is written after every update and deleted when `/confirm`
submits, so a restart — including the automatic one `Restart=always` gives you — resumes the job
being assembled instead of discarding it.

This was memory-only until 2026-08-31. The staged **files** always survived a restart; their slot
**labels** did not, so material the user had already answered questions about became unreachable
with no message and no way back except sending it again. Three restarts in one session were enough
to justify the file.

`_LAST_VALIDATE` is deliberately *not* in it: `/confirm` treats a missing verdict as "never
attempted", re-runs the free `make batch-validate` and re-sends the manifest before anything spends.
A cached pass carried across a restart would be a verdict about a process that no longer exists.

An **unreadable** draft is moved to `batch/tg-<chat_id>.draft.json.bad` rather than skipped. Skipping
looked harmless and was not: it leaves the in-memory state empty, and the save that runs after every
update then sees no state and deletes the file — so the one record of the job was destroyed by the
line after the one that failed to read it, silently. Reconstructing a draft by hand from the staged
files is possible, but only while the file still exists. The bot also says so in the chat, because a
log line on a box nobody is looking at is not a report.

To discard a half-assembled job, use `/clear` — or delete that file and its
`batch/tg-staging/<chat_id>/` directory by hand.

`batch/tg-<chat_id>.last.json` (also gitignored) is the job most recently submitted, written by
`/confirm` just before it clears the draft. `/again` copies it back. It is a **separate file on
purpose**: `_load_draft` reads only `.draft.json`, so a submitted job can never be rehydrated by a
restart and re-confirmed by accident.

## Acceptance — needs a real phone

This validates the byte-fidelity assumption: does Telegram preserve bytes for a `.MP4` sent via
`File`? The design in spec section 4 assumes yes. This is the measurement that proves it.

**A1/A2 — arrival.** With the bot running, send **the same file twice** from the iPhone: once via
the Photos tab, once via `paperclip -> File`.

- The Photos one must be refused with the compression message.
- The File one is accepted, and the reply carries the description, the byte count and the `sha256`.
  (There is no `/sha` command; the digest is folded into the accepted-file reply, so nothing has to
  be asked for a second time.)

Compare against the original on the Mac:

```bash
shasum -a 256 <the original file>
stat -f %z <the original file>
```

Do this for an image and for a ~25MB `.mp4`. Record both digests per file, whether they match, and
the byte counts.

**If the digests match:** the design's central assumption holds.
**If they do not:** stop work. Spec section 4 is invalidated and the ingest channel has to change
before anything else is built — the rest of the plan would be built on sand. Do not work around it.

**A6 — delivery.** After a run, compare the digest of the file the bot sent back against the one on
the VPS. The arrival digest from A1 is already in the chat, so this is a comparison, not a second
procedure:

```bash
shasum -a 256 out/<batch>/_final/job.mp4
```

They must match. If they do not, `sendDocument` is not doing what this design claims and results are
being re-encoded — which invalidates every quality measurement made from a delivered file.
