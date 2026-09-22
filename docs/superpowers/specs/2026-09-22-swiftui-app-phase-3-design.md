# Motion iPhone app — Phase 3 New Job design

Date: 2026-09-22 · Status: implemented

This spec refines Phase 3 of
`docs/superpowers/specs/2026-09-22-swiftui-app-design.md`. Phases 1–2 are shipped. Phase 3 adds the
server-backed single-job composer: pipeline selection, material assignment, provider selection,
basket management and free validation. It consumes the existing draft contract in
`docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md`; it adds no backend routes and
never starts Phase A, confirms a run, rents a pod or talks to a GPU pod.

## 1. Scope and success criteria

Phase 3 is complete when the installed app can:

- open a central New Job tab and load `GET /v1/pipelines`, `GET /v1/draft` and the global material
  library;
- switch pipelines without hardcoding their names, stages, roles or providers;
- show required and optional role slots, filter the material picker by the catalog's image/video
  kind, and assign or clear each slot through `PATCH /v1/draft`;
- show provider selection only when the selected pipeline advertises providers;
- render the server's current missing slots, warnings, job count, validation state and batch entries;
- add one complete job to the batch, remove an existing batch entry, clear the draft, and validate
  the jobs through the existing free endpoints;
- keep the server response as the authoritative state after every mutation, including a concurrent
  edit that makes a validation result stale; and
- pass MotionKit unit tests and the simulator build without renting a pod.

Phase 3 does not start Phase A, regenerate a try-on, keep a try-on-library image, show a rent panel,
confirm or resume a run, compose a cross batch automatically, or add any pod control. Those remain
Phases 4–6. It also does not change or delete materials from inside the composer; the Material tab
continues to own those actions.

## 2. Decisions and rejected alternatives

### 2.1 Server-authoritative composer

`DraftStore` owns the last `PipelineCatalogResponse` and `Draft`. Views never construct a parallel
local job. A pipeline, provider or slot change sends one patch and replaces the whole draft with the
response. Add, drop, clear and validate do the same. This preserves the API's generation, probe,
warning, duplicate and missing-file rules and makes edits from another client visible after refresh.

A locally staged draft with a later Save action was rejected. It would duplicate the backend's
pipeline-switch slot dropping, media probing and validation invalidation rules, and could show a job
that the server would not run.

### 2.2 One scrollable composer, not a wizard

The screen is one scrollable form ordered as pipeline, provider when applicable, role slots, batch
summary and validation action. Users can correct any missing role in place and see the server's
updated readiness immediately. A multi-page wizard was rejected because pipelines have different
role counts, optional roles and provider availability; back-navigation would create another layer of
partially committed state.

The central tab uses the design's `+` treatment and opens this screen directly. It is a persistent
tab, not a modal, so leaving for Material to upload a missing input and returning preserves the
server-backed draft.

### 2.3 Catalog-driven roles and provider visibility

The app decodes the catalog verbatim:

```text
Pipeline: id, stages, required, optional, roles[role] = image|video, providers[]
Draft: owner, pipeline, provider, generation, slots, required, optional, missing,
       validated, batch, jobs, estimate_min
```

Pipeline ids and role ids are displayed with a small presentation-only formatter that replaces
underscores and hyphens with spaces and title-cases the result. The raw ids are always sent back to
the API. Provider labels come from the catalog; the app never invents labels or assumes that Gemini,
Qwen or any pipeline exists.

When a pipeline switch returns `dropped`, the screen uses the returned draft immediately and shows a
short informational banner naming the roles the server removed. If the new pipeline has no provider
choices, the provider section is absent. The stored provider may still exist in the response but is
not editable because the backend does not read it for that pipeline.

### 2.4 Role-scoped material picker

Tapping a slot opens a sheet containing the already loaded global material list, filtered by the
role kind from the selected pipeline. The sheet shows authenticated thumbnails, owner and file name,
and includes a clear action for an occupied optional or required slot. Required slots may be cleared;
the resulting server response marks them missing and disables Add to batch until refilled.

The picker reuses `MaterialsStore` rather than creating a second material cache or making view-level
requests. If materials have not loaded, it refreshes them. Empty and offline states retain the same
treatment as the Material tab. An item can be selected even when its thumbnail is unavailable.

The draft's slot response contains its own probe and warning. The composer shows that warning beside
the assigned slot even after a cold launch; it does not rely on Phase 2's process-local upload-warning
cache.

### 2.5 Basket and validation semantics

The current editor is complete when the server's `missing` array is empty. `Add to batch` is enabled
only then. The endpoint deliberately leaves a copy of the added job in the editor, so the UI does not
pretend the form was reset. The exact duplicate error is shown verbatim.

The basket section renders the server's stable `digest`, `run_id`, pipeline, provider and slot ids.
Drop sends the digest from that row; it does not use an array index. Phase 3 exposes drop because it
is free and necessary to correct the batch before any later spend action.

`Validate` applies to the server's `jobs` set: basket entries plus the complete current editor when it
is not an exact duplicate. It is enabled only when `jobs > 0` and no mutation or validation is in
flight. A successful non-stale response marks the draft Ready and shows `estimate_min` when present.
Any subsequent mutation replaces the state with `validated: null`, removing Ready immediately.

If validation returns `{stale: true}`, the app accepts the nested current draft but does not claim
that draft is valid. It shows that the draft changed during validation and asks the user to validate
again. A `422 invalid` displays the server's validation output/message verbatim. Phase 3 never turns a
successful validation into a Run or spend action.

## 3. MotionKit structure and interfaces

New focused units:

```text
ios/MotionKit/Sources/MotionKit/
  Models/Drafts.swift       catalog, slot, batch and validation response models
  Stores/DraftStore.swift   catalog/draft loading and all free draft mutations

ios/MotionApp/NewJob/
  NewJobView.swift          composer, readiness and batch summary
  PipelinePicker.swift      catalog-driven pipeline/provider selection
  SlotRow.swift             assigned/missing/optional role presentation
  MaterialPicker.swift      kind-filtered global material selection sheet
```

`APIClient` gains only the generic primitives required by the existing routes:

```swift
patch(Response.self, body: Encodable, path...)
post(Response.self, path...)                 // already exists
delete(Response.self, path...)               // JSON-returning delete
```

Every request retains the Phase 1 Access, bearer and User-Agent headers. Patch JSON uses the same
snake-case encoding as Post. The existing bodyless Delete remains unchanged for material deletion;
the new generic Delete accepts `200` and decodes the returned draft.

`DraftStore` is `@MainActor @Observable`. Its public state includes catalog, draft, loading/mutation/
validation flags, last successful refresh, one displayed error and one informational notice. It
serializes mutations at the store boundary: controls are disabled while a write is in flight, and a
second write request is refused locally. Refresh is still available through pull-to-refresh when no
write is active.

The store depends on `APIClient`; `NewJobView` receives the shared `MaterialsStore` separately for the
picker. `AppModel` constructs one `DraftStore` alongside the Phase 1–2 stores whenever credentials are
available.

## 4. Models and decoding policy

Models mirror the response instead of flattening away server state:

- `PipelineCatalogResponse.pipelines: [Pipeline]`;
- `Pipeline` carries `id`, `stages`, `required`, `optional`, `roles` and `providers`;
- `Draft` carries the generation and tri-state `validated: Bool?`;
- `DraftSlot` carries `materialID`, name, existence, full probe and warning;
- `DraftBatchEntry` is identified by `digest`, not its list position;
- `DraftValidationResponse` carries `valid`, `stale`, nested `draft`, and optional `output`.

Role kinds decode unknown future strings as `.unknown`. An unknown kind is visible but has no eligible
materials, with an update-required message; it is never guessed as image or video. Unknown additional
JSON fields remain forward-compatible through normal `Decodable` behavior.

Patch bodies are narrow value types so omitted and explicit-null semantics do not blur together:

- pipeline patch: `{pipeline}`;
- provider patch: `{provider}`;
- slot assignment: `{slots: {role: material_id}}`;
- slot clear: `{slots: {role: null}}`.

Phase 3 does not send `tryon_seed`.

## 5. SwiftUI flow

The root tab order becomes Runs · Material · `+` · Output. The Pod tab remains Phase 5. The New Job
tab has:

- title, jobs count and stale indicator;
- a pipeline menu showing formatted id and compact stage list;
- a provider segmented control or menu only when the catalog supplies providers;
- required slots first, optional slots second, each with assigned thumbnail/name or a missing/empty
  card and a Choose/Change affordance;
- a readiness line derived from server `required` and `missing` (`Ready · n of n` only when none are
  missing);
- Add to batch and Clear actions;
- a batch section with stable rows and destructive Drop confirmation; and
- a Validate button plus validating, valid, stale-validation and invalid states.

The tab's symbol is `plus.circle.fill`. It uses the existing dark theme and lime primary action;
missing roles use amber, destructive clear/drop actions use red, and identifiers/estimates use the
bundled mono font. All controls have text labels and accessibility values; readiness is not conveyed
by color alone.

The first appearance fetches catalog and draft together. Material refresh is deferred until the
first material-picker opening unless `MaterialsStore` is already loaded. Returning to the tab
refreshes the draft so Telegram or another app session cannot leave stale assignments on screen.

## 6. Error and concurrency behavior

- A read failure keeps the last catalog/draft dimmed and marks it stale. The first load shows an
  error state with Retry.
- `404` while assigning a material refreshes both materials and draft, then reports that the material
  was removed elsewhere.
- `409` and `422` keep the returned/current draft and show the server message verbatim. This covers
  duplicates, missing slots, unknown roles, wrong media kinds and validation failures.
- A transport failure after a mutation is not replayed automatically because these draft writes have
  no idempotency key. The app refreshes the draft before enabling Retry so it first discovers whether
  the server applied the request.
- Validation may take up to the server's 120-second limit. It has an explicit progress state and is
  not canceled merely because the user changes tabs; server generation makes a concurrent external
  edit safe. App termination cancels the client wait, and the next opening refreshes the draft.
- `busy` validation responses are shown with Retry. There is no automatic validation loop.
- Empty catalog or a selected pipeline missing from a refreshed catalog is an incompatible-contract
  error; the app does not choose another pipeline or mutate the draft automatically.

## 7. Tests and verification

MotionKit tests use `StubURLProtocol`; no test calls live write routes. Required cases:

- catalog and draft fixtures decode required/optional roles, role kinds, providers, slots, probes,
  warnings, tri-state validation, basket rows and estimate;
- unknown role kind decodes safely and exposes no eligible material kind;
- Patch sends snake-case JSON and all auth headers for pipeline, provider, assign and explicit-null
  clear operations;
- JSON-returning Delete percent-encodes a digest path segment, accepts `200` and leaves bodyless
  material Delete behavior unchanged;
- every successful mutation replaces the draft with the server response and clears an earlier
  validation result;
- the store prevents overlapping writes;
- a transport failure refreshes before a user retry is enabled;
- material filtering returns only the catalog-required kind and does not depend on thumbnails;
- successful validation accepts the nested draft and estimate;
- stale validation accepts the nested draft but does not show Ready; and
- `409`/`422` messages, `404` material disappearance and read-stale behavior preserve usable state.

Verification commands:

```bash
make ios-test
make ios-build
make ios-contract   # GET-only; pipelines and draft are already in the contract
make batch-test     # protects the draft API behavior consumed by the app
motions-studio/setup/scrub-secrets.sh --check
```

Manual simulator/iPhone verification is free: switch pipelines, assign image/video materials, clear
a required role, add and drop a basket entry, validate, then edit once more and confirm Ready clears.
That Phase 3 smoke remains deferred and unrun, including its physical-phone portion. Phase 2's
physical-phone upload/resume smoke also remains pending and unrun; neither is a prerequisite for
the implemented Phase 3. No pod lifecycle command or live spend route is used.

## 8. Security and repository constraints

- No credential, material response, draft response, validation output or personal file name is
  logged or committed.
- `motion-contract` remains GET-only. Live Phase 3 writes are limited to `/v1/draft...` and occur only
  during deliberate simulator/phone smoke testing.
- Validation is free but may expose paths in backend diagnostics; the app shows the server's already
  sanitized response and does not persist it.
- `motions-studio/setup/scrub-secrets.sh --check` must exit 0 immediately before every commit.
- No `/v1/runs...`, `/v1/pod...` or other spend-capable route may be called during Phase 3. No pod
  lifecycle command may be run.
