# Image Studio (Flow-style image generation and editing) — design

Date: 2026-09-26. Status: approved in chat section by section; this document awaits review before
the implementation plan.

## Goal

A second "space" in the iOS app, modelled on Google Flow's image mode: type a prompt to generate an
image, or attach reference images and describe an edit. Typical use: a try-on image came out almost
right, and a few spots need fixing by prompt ("keep everything, make the left hand relaxed") instead
of re-running try-on.

Decisions made in chat (2026-09-26):

| Question | Decision |
|---|---|
| What are the results for? | Standalone by default; each image can be promoted to a material or to the try-on library. |
| Conversation model | **Flow-style, stateless.** Each send = prompt + the references currently attached. To keep editing, attach the result and prompt again. No multi-turn context (Qwen cannot do it, and resending history makes each turn cost more). |
| Organisation | **Many projects**, like Flow. New project is titled with its creation date/time, renamable. |
| Placement | **Not a new tab.** A slide-out sidebar (like the Claude app's code/chat/design spaces) switches between the Motion space (today's 5 tabs, unchanged) and the Studio space. |
| Models | Nano Banana Pro, Nano Banana 2 (default), Nano Banana 2 Lite, Qwen Image 3.0 Pro (the model try-on already uses — `QWEN_IMAGE_MODEL=qwen-image-3.0-pro` in `.env`). Every model does both text-to-image and editing. Aspect 16:9 / 4:3 / 1:1 / 3:4 / 9:16, count x1–x4. |
| Execution | **Approach A:** background thread on the VPS, app polls. |
| Prompt language | Sent verbatim to every provider, Vietnamese included. **No translation**, Qwen too. |

## What already exists (read before building)

- `scripts/batchlib/local_tryon.py`:
  - `gemini_edit(images, prompt, key, out_path, aspect_ratio=, model=, image_size=)` — one
    `generateContent` call, 300 s timeout, raises `JobError` with the provider's HTTP code/body.
  - `qwen_max_edit(images, prompt, key, out_path, negative_prompt=, model=, size=)` — synchronous
    DashScope multimodal-generation call; uses only `images[:3]`, reads only the first returned
    image. Needs `DASHSCOPE_API_KEY` plus `QWEN_IMAGE_WORKSPACE` (or `QWEN_IMAGE_BASE`);
    `qwen_max_configured()` reads that live. The model is `QWEN_IMAGE_MODEL`, which `.env` sets to
    `qwen-image-3.0-pro` (the code default `qwen-image-edit-plus` and the comment saying 3.0 access
    was not yet granted are stale).
- Qwen Image 3.0 API (Bailian docs, `qwen-image-generation-and-editing-api-reference.md`, read
  2026-09-26): the same sync endpoint does **text-to-image** (content = one `{"text"}` only) and
  editing (1–3 `{"image"}` + one `{"text"}`); `parameters.n` 1–6; total pixels 512²–2048², aspect
  1:8–8:1, no `size` = model picks; input images ≤10 MB, 384–2048 px per side recommended; prompts
  in any language, sent as-is.
  - Measured 2026-09-16: `gemini-3-pro-image` without `imageConfig.imageSize` falls back to 1K
    (768×1376 for 9:16), whatever the input size.
- `scripts/control/tryon_library.py` — the pattern to copy: one JSON index per owner, rewritten
  atomically under `control.LOCK`, images outside `out/`.
- `scripts/httpapi/server.py` — hand-rolled router, `ApiError(status, code, message)`,
  `_idempotency_key()`, `_send_json`. Owner for app-created things is `materials.APP_OWNER`.
- Uploads (`control/uploads.py`) always land as **materials**; materials under
  `batch/tg-staging/` are pruned after `STAGING_MAX_AGE_DAYS = 7` (`scripts/tgbot/bot.py`).
- Models the Gemini key can call, listed 2026-09-26 via `GET /v1beta/models`:
  `gemini-3-pro-image` (Nano Banana Pro), `gemini-3.1-flash-image` (Nano Banana 2),
  `gemini-3.1-flash-lite-image` (Nano Banana 2 Lite), `gemini-2.5-flash-image` (not offered).
- iOS: `RootView` is a 5-tab `TabView` with `KillBanner`/`SpendBanner` in the top safe area;
  `MediaActions` (save/share), `StackLoader`, existing material/try-on pickers.

## Rejected approaches

- **Synchronous request held open until the image is back.** Nano Banana Pro takes tens of seconds
  per image and x4 multiplies the risk; Cloudflare Tunnel cuts requests near 100 s, and a request
  that dies after the provider already billed loses a paid result.
- **App calls Gemini directly.** Puts provider keys on the phone and breaks the rule that the app
  talks only to the control plane.
- **Multi-turn Gemini chat.** See the decision table; can be added later as a Gemini-only toggle.
- **`NavigationSplitView` for the sidebar.** On iPhone it collapses to a push stack, not a
  slide-out panel.

## Backend

### Storage — `scripts/control/studio.py`

`StudioStore(studio_dir, owner)`, shaped like `TryonLibrary`: `studio_dir/<owner>.json` is the
index; files live under `studio_dir/<owner>/<project_id>/`. `studio_dir = batch/studio/` (outside
`out/`, so `batch-clean` and `_final` pruning never touch it).

```
project    {id, title, created_at, updated_at, generations: [generation]}
generation {id, created_at, prompt, model, aspect, count,
            refs: [{kind, id, file}],          # file = snapshot name inside refs/
            status: queued|running|done|error, # aggregate over slots
            slots: [{status, image?, error?}], # one per requested image
            est_cost_usd}
```

- **References are snapshotted at submit time** into `<project>/refs/`. Materials are pruned after
  7 days and try-on entries can be deleted; history and Retry must not break when that happens.
- Images are written as `<project>/img/<generation_id>-<slot>.<ext>`; image id =
  `<generation_id>-<slot>`.
- On bot start, every `queued`/`running` slot becomes `error: "interrupted"` (the thread that owned
  it is gone).
- Deleting a project removes its directory.

### Reference kinds

The app sends `{kind, id}`; the server resolves the file — the app never sends a path.

| kind | id | resolved by |
|---|---|---|
| `material` | `<owner>/<name>` | `materials.resolve_material` (device uploads arrive here via the existing upload flow) |
| `tryon` | library entry id | `TryonLibrary.resolve_image` |
| `run_tryon` | `<run_id>/<index>` | `app_runs.tryon_image` (only while that run is the live one) |
| `studio` | `<project_id>/<image_id>` | `StudioStore` |

Unresolvable → `422 ref_not_found` naming the ref. Only images are accepted (`422 ref_not_image`).

### Providers

| model key | call | refs max | notes |
|---|---|---|---|
| `nano-banana-pro` | `gemini_edit(model="gemini-3-pro-image", image_size="2K")` | 14 | |
| `nano-banana-2` (default) | `gemini_edit(model="gemini-3.1-flash-image", image_size="2K")` | 14 | |
| `nano-banana-2-lite` | `gemini_edit(model="gemini-3.1-flash-lite-image")` | 14 | whether it accepts `imageSize` is checked during implementation; record the answer here |
| `qwen-image-3` | Qwen call with `model=QWEN_IMAGE_MODEL` (`qwen-image-3.0-pro`) | 3 | text-to-image with 0 refs, edit with 1–3; unavailable when `qwen_max_configured()` is false |

The 14-reference cap for Gemini is checked against Google's docs per model during implementation.
Exceeding a cap → `422 too_many_refs`; **never silently truncate** (today `qwen_max_edit` slices
`[:3]` — the API check sits in front of it). Aspect maps to Gemini `aspectRatio` and to a Qwen
`size` of about 2K total pixels (e.g. 1536×2048 for 3:4), inside the 2048² cap.

The Qwen helper is extended, not forked: accept zero images (text-only content), pass `n`, and
download every returned image rather than only the first. Try-on's existing calls keep working
unchanged. Reference images are downscaled server-side to ≤2048 px per side before sending (Qwen
caps inputs at 10 MB; Gemini inline payloads also grow with size).

Gemini: each slot is one call; x4 = four calls on a small thread pool (max 4 concurrent per
generation), and a slot failure does not fail its siblings. Qwen: one call with `n = count`
(the API returns up to 6); if that call fails, every slot of the generation shows the error.

Gemini sometimes returns no image but a text part or a `finishReason` (e.g. a safety block).
`gemini_edit` today raises "không trả ảnh" with a 300-char dump; the Studio wrapper extracts the
text/finishReason into `slot.error` so the app can show *why*.

### Cost

A server-side price table (`STUDIO_PRICES`, USD per image, source URL and date in a comment) feeds
`est_cost_usd` and the `GET /v1/studio/models` response. The app never hardcodes prices. Qwen Image 3.0 Pro's Bailian model card lists, per image,
¥0.02 per input image and ¥0.25 (1K) / ¥0.5 (2K) per output image — mainland CNY pricing; the
Singapore-workspace USD price is taken from the international pricing page during implementation. After the
first live run of each model, compare against the Google/DashScope billing pages and record the
measurement in this spec. No spend cap — personal tool — but the price is shown before sending.

### Endpoints (all under the existing bearer + Cloudflare Access auth)

```
GET    /v1/studio/models                              models, prices, caps, availability
GET    /v1/studio/projects                            list (id, title, updated_at, cover image, spent)
POST   /v1/studio/projects                            {title?} → project
GET    /v1/studio/projects/{pid}                      full project with generations
PATCH  /v1/studio/projects/{pid}                      {title}
DELETE /v1/studio/projects/{pid}
POST   /v1/studio/projects/{pid}/generations          {prompt, model, aspect, count, refs[]} → 202 generation
                                                      Idempotency-Key required (it spends money)
GET    /v1/studio/projects/{pid}/images/{image_id}    the image file
GET    /v1/studio/projects/{pid}/refs/{file}          a reference snapshot (for the history view)
POST   /v1/studio/projects/{pid}/images/{image_id}/promote   {to: "material"|"tryon"} → the new item
```

Retry = the app re-posts the failed generation's parameters as a new generation; nothing is
overwritten. Empty prompt → `400` (every provider requires a text prompt).

## iOS

### Shell and sidebar

- New `SpaceShell` wraps the current `RootView` content. It holds `selectedSpace`
  (`.motion` / `.studio`, persisted with `@AppStorage`).
- A custom slide-out panel from the left (edge swipe or ☰ button); the main content slides right
  and dims. ☰ goes into every Motion tab's navigation bar and the Studio bar.
- Sidebar content, top to bottom: the two spaces · Studio project list (newest first; long-press →
  rename / delete) · "+ New project" · Settings. Tapping a project switches to Studio and opens it.
- `KillBanner` and `SpendBanner` stay visible in both spaces — a running pod bills either way.

### Project screen

- Two-column masonry grid, newest first. Running slots show `StackLoader`; failed slots show the
  error text and **Retry**.
- Tap an image → full-screen viewer: **Edit this** (attach as reference), **Use as material**,
  **Save to try-on library**, Save/Share (`MediaActions`), and the prompt/model that made it.
- Composer pinned at the bottom:
  - Attached reference thumbnails row. **The ✕ is hidden by default.** Tap a thumbnail →
    backdrop dims, an enlarged preview appears above the composer, and ⓧ shows on the thumbnail;
    tap ⓧ to remove, tap the thumbnail again or elsewhere to dismiss. (Flow uses a long-press; the
    user asked for a plain tap after using it on the phone, 2026-09-26.)
  - Prompt field growing to about 5 lines.
  - ＋ → source menu: Photos / Camera (goes through the existing upload → material), Materials,
    Try-on library, current run's try-on previews, Studio images. Photos is multi-select, capped at
    the selected model's remaining reference slots; the picks upload one after another and all
    attach (added 2026-09-26).
  - A send whose project was deleted elsewhere (404 `not_found`, which bought nothing) creates a
    fresh project and resends the same prompt and references there once, instead of an alert —
    hit on the phone 2026-09-26 when an empty project was deleted from another client mid-compose.
  - Settings pill "model · aspect · xN" → bottom sheet (like Flow's): aspect row, x1–x4 row, model
    list with per-image price. Qwen is disabled with an explanation only when unavailable
    server-side, or when more than 3 references are attached.
  - Send button shows the total estimate ("x4 · ~$0.54").

### Entry points from the Motion space

Two places gain **"Edit in Studio"**: `TryonPreviewCard` and the Saved try-ons context menu → a
sheet to pick an existing project or create one → the image is attached to that project's composer
and the app switches to Studio. (An earlier draft also listed a try-on image viewer; the app has no
such viewer, so the line was amended 2026-09-26 to name the two entry points that exist.)

### MotionKit

`StudioAPI` + Codable models + `StudioStore` (`@Observable`). The store polls the open project
every 3 s while any slot is `queued`/`running` and stops when none is. Price totals and the
"Qwen caps references at 3" rule live in MotionKit so they are unit-tested.

## Testing

All free unless marked.

- Python `unittest` (`scripts/tests/`): `StudioStore` CRUD, ref snapshotting, interrupted-on-start;
  routes with the provider calls faked — ref resolution per kind, cap/`422`s, idempotent generation
  POST, per-slot failure isolation, Gemini no-image text surfaced in `slot.error`, promote to
  material and to try-on library.
- `make ios-test`: pricing, model gating, polling start/stop.
- `make ios-build`, and a UI smoke test against the live API using GETs only.
- `make ios-contract` extended with the Studio GET endpoints.
- **Live (costs money, ask first):** one x1 generation per model on the VPS, Vietnamese prompt
  included; Qwen tested both text-to-image and edit. Expected under ~$0.50 total. Record observed latency and billed cost here.

## Deploy

Merging to `main` under `scripts/**` auto-deploys and restarts `motion-bot`. Before merging, check
the VPS for a live drain / Phase A / lease / migration (`batch/*.state.json`, `.env`'s
`GPU_INSTANCE_ID`, `pgrep -af 'drain.py|batch_run.py'`). The iOS app is built and installed as in
earlier phases. `motions-studio/setup/scrub-secrets.sh --check` must pass before each commit.

## Out of scope

- Multi-turn Gemini context, masks/inpainting brushes, video in Studio, the Telegram bot and the
  Nuxt FE.
