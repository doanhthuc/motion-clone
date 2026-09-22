# Motion iPhone app — Phase 2 Materials and uploads design

Date: 2026-09-22 · Status: approved for implementation

This spec refines Phase 2 of
`docs/superpowers/specs/2026-09-22-swiftui-app-design.md`. Phase 1 is already shipped. Phase 2 adds
the Materials tab, authenticated thumbnails, foreground chunked uploads from Photos and Files,
resume after interruption, and safe deletion. It consumes the existing material/upload contract in
`docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md`; it adds no backend routes and
never rents or talks to a GPU pod.

## 1. Scope and success criteria

Phase 2 is complete when the installed app can:

- list all global materials from `GET /v1/materials`, including material uploaded through Telegram;
- render authenticated thumbnails from `GET /v1/materials/{owner}/{name}/thumb`;
- choose one image or video from Photos or Files and upload it through the existing 32 MB chunked
  protocol without loading the whole file into memory;
- resume a foreground upload after cancellation, network loss, or app relaunch by asking the server
  which chunks already exist and sending only the missing chunks;
- show byte progress, the current upload phase, and the quality warning returned by `complete`;
- delete only `owner == "app"` materials after confirmation, while showing the server's message for
  `409 in_use`; and
- pass MotionKit unit tests and the simulator build without renting a pod.

Phase 2 does not add background `URLSession` uploads, multiple concurrent uploads, editing or
assigning draft slots, a local material database, or any job/pod action.

## 2. Decisions and rejected alternatives

### 2.1 One serial, foreground uploader

`Uploader` is an actor and owns at most one active upload. A single upload is easier to reason about
on a 1 GB VPS, produces deterministic progress, and matches the app design. Selecting another file
while an upload is active is disabled.

The uploader reads one server-sized slice at a time with `FileHandle.seek` and `read(upToCount:)`.
It never uses `Data(contentsOf:)` for the whole selected video. A memory-buffer implementation was
rejected because the API accepts files up to 2 GiB and the phone may terminate the app long before
that allocation succeeds.

### 2.2 Server-authoritative chunk geometry

The app sends `{file_name, size}` to `POST /v1/uploads`, then uses the returned `chunk_size` and
`chunks_total`. It does not silently substitute a client constant. Unit tests still pin the live
contract expectation that a 32 MiB + 17 byte file produces two chunks when `chunk_size` is 32 MiB.

Before uploading, and again after any restart, the client calls `GET /v1/uploads/{id}`. The
`received` array is authoritative; only indices absent from that array are sent. A chunk is a binary
`PUT` with `Content-Type: application/octet-stream`. Completion is a bodyless `POST`. Replaying
completion is safe because the VPS journals `done.json`.

### 2.3 Durable transient file and checkpoint

Photos and Files providers may vend temporary or security-scoped URLs. The app therefore copies the
selected item immediately into `Application Support/Motion/Uploads/<uuid>/source.<ext>` while access
is valid. Next to it, `checkpoint.json` records:

```text
uploadID, fileName, fileSize, localFileName
```

The checkpoint is written atomically immediately after `POST /v1/uploads` succeeds. On app launch
or when the scene becomes active, `MaterialsStore.resumePendingUpload()` loads the checkpoint,
checks the local file still has the recorded size, and resumes using server status. After a
successful `complete`, both the checkpoint directory and its copied source are removed. If the
local file is missing or changed, the app reports that the upload cannot resume and offers to clear
the local checkpoint; it never opens a second server upload automatically.

This directory is transport state, not a material database. `GET /v1/materials` remains the source
of truth after completion.

### 2.4 Warning lifetime follows the existing contract

`POST /v1/uploads/{id}/complete` returns `{material, probe}` and `probe.warning`. The app places the
new material into the list and shows a persistent amber tag for the remainder of the process. The
current `GET /v1/materials` response contains no probe or warning, so a later cold launch cannot
reconstruct that tag. Phase 2 does not invent local probe persistence or change the shipped API.

### 2.5 Ownership and deletion

All owners are visible because materials are global, but only `owner == "app"` rows expose Delete.
The UI asks for confirmation, sends `DELETE /v1/materials/{owner}/{name}`, and removes the row only
after `204`. A `409 in_use` keeps the row and displays the server message verbatim. `404` refreshes
the list because another client already removed the file. No optimistic deletion is used.

## 3. MotionKit structure and interfaces

New focused units:

```text
ios/MotionKit/Sources/MotionKit/
  Models/Materials.swift       Material, probe and upload response models
  Upload/UploadCheckpoint.swift durable checkpoint value + journal protocol
  Upload/Uploader.swift        chunk planning, stream reads, resume and complete
  Stores/MaterialsStore.swift  list/thumbnail/delete/upload UI state
```

`APIClient` gains only the primitives Phase 2 requires:

```swift
post(Response.self, body: Encodable?, path...)
put(data: Data, path...)
delete(path...)
```

Every primitive applies the Phase 1 Access, bearer and User-Agent headers. JSON requests set
`Content-Type: application/json`; chunk requests set `application/octet-stream`; Delete accepts
only `204`. Existing GET and Range behavior is unchanged.

`MaterialsStore` is `@MainActor @Observable`. Its public state is the material list, thumbnail byte
cache, current `UploadProgress`, most recent completed warning, loaded/stale state, and one displayed
error. Views call store methods only; they do not call `APIClient` or `Uploader` directly.

`UploadProgress` separates these phases so the UI never claims byte completion while the server is
still probing or converting a file:

```text
preparing → transferring(bytesSent/totalBytes) → processing → complete
```

Progress counts already received chunks before resume. Since only whole chunks are acknowledged,
the resumed byte count is the sum of their expected lengths.

## 4. SwiftUI flow

The root tab order becomes Runs · Material · Output. The Phase 3 central New Job tab remains absent.

`MaterialsView` has:

- a title and item count, pull-to-refresh, stale/error treatment matching Runs and Outputs;
- one adaptive two-column grid with a thumbnail, file name, kind, owner, byte size and update time;
- an amber warning tag when the current process knows the completed upload's warning;
- a destructive context/menu action only for app-owned rows, followed by a confirmation dialog;
- an Add button whose menu offers Photo Library and Files; and
- an upload card showing file name, phase, byte progress and a progress bar. Selection controls are
  disabled while it is active.

Photos selection uses `PhotosPicker`. Its transferable representation copies the provider file into
the app's upload staging directory rather than decoding image pixels or loading a movie into memory.
Files selection uses `fileImporter`; it balances security-scoped access around the same copy step.
Both paths hand the resulting ordinary local URL to `MaterialsStore.startUpload`.

Thumbnail loading is owned by `MaterialsStore` so the request carries authentication. Rows show a
neutral placeholder while missing or unavailable; thumbnail failure does not fail the material
list.

## 5. Error and lifecycle behavior

- Transport/offline errors retain the material list, mark it stale, and leave the checkpoint for
  Retry/resume.
- `404` from upload status means the VPS pruned the upload after 24 hours. The app clears its local
  checkpoint and tells the user to select the file again.
- `409 incomplete` on completion triggers one status re-read and another missing-chunk pass before
  surfacing the error. Other `409` responses are displayed verbatim.
- `413` and probe/format failures are displayed verbatim. The source/checkpoint remain for an
  explicit retry unless the server reports the upload no longer exists.
- Leaving the Materials tab does not cancel the foreground task. Process termination does; the
  checkpoint enables the next launch to resume.
- Scene activation may start one resume attempt. Repeated activation cannot create parallel upload
  tasks because `Uploader` serializes the operation and `MaterialsStore` tracks its task.

## 6. Tests and verification

MotionKit tests use `StubURLProtocol` and temporary directories; no test calls the live write API.
Required cases:

- material list, upload-open/status/complete, probe warning, and unknown material kind decode;
- POST JSON, PUT binary and DELETE carry all auth headers, correct methods/content types, encoded
  path segments, accepted statuses, and error mapping;
- 32 MiB + 17 bytes plans exactly two chunks with lengths 32 MiB and 17;
- resume status with chunk 0 already received sends only chunks 1 and 2, then completion;
- checkpoint is persisted before the first PUT, survives a recreated uploader, and is removed only
  after completion;
- a mismatched local file is refused before a network request;
- store deletion keeps an item on `409`, removes it on `204`, and refreshes on `404`;
- thumbnail bytes are cached per material id; and
- store upload state transitions through transfer and processing, refreshes the list, and retains the
  returned warning.

Verification commands:

```bash
make ios-test
make ios-build
make ios-contract   # remains GET-only; now also decodes GET /v1/materials
motions-studio/setup/scrub-secrets.sh --check
```

Manual verification on the iPhone uses a real image and a video larger than 32 MiB: interrupt the
video after one chunk, relaunch, verify the first chunk is not resent, complete it, inspect the
thumbnail/warning, then delete the app-owned test material. This uses only material/upload routes,
costs no GPU time, and must not create or rent a pod.

## 7. Security and repository constraints

- No credential, selected-media path, material response, or upload checkpoint is logged or committed.
- `ios/Secrets.xcconfig`, `ios/MotionApp.xcodeproj`, Application Support files and selected media stay
  untracked.
- `motion-contract` remains GET-only. Live write verification is manual and narrowly limited to the
  Phase 2 upload/material routes.
- `motions-studio/setup/scrub-secrets.sh --check` must exit 0 immediately before every commit.
- No POST, PUT, PATCH or DELETE route outside `/v1/uploads...` and `/v1/materials...` may be called
  during Phase 2. No pod lifecycle command may be run.
