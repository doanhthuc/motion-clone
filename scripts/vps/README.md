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
