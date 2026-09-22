# Motion iPhone app — Phase 2 Materials and uploads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an installable Materials tab that lists and previews global materials, uploads images
and videos with resumable 32 MB chunks, and safely deletes app-owned materials.

**Architecture:** MotionKit owns Codable models, authenticated write requests, an actor-isolated
streaming uploader with a durable file checkpoint, and a main-actor MaterialsStore. SwiftUI owns
provider/file selection and presentation only; every server interaction goes through the store.

**Tech Stack:** Swift 6.2, Swift Testing, Foundation/URLSession, Observation, SwiftUI,
PhotosUI/Transferable, UniformTypeIdentifiers, XcodeGen.

**Spec:** `docs/superpowers/specs/2026-09-22-swiftui-app-phase-2-design.md`

## Global Constraints

- Target iOS 26+, Swift 6 strict concurrency, dark mode, iPhone portrait only.
- The server is the source of truth; upload checkpoints are transient transport state, not a local
  material database.
- Upload one file at a time, in the foreground, without loading the whole source into memory.
- Use the server-returned `chunk_size`; the deployed contract currently returns exactly 32 MiB.
- Only call write routes under `/v1/uploads...` and `/v1/materials...`; never call a job, draft, pod,
  balance, stock or migration write route.
- Never rent a pod or run a pod lifecycle command.
- `motion-contract` remains GET-only.
- Never add or commit `ios/Secrets.xcconfig`, `ios/MotionApp.xcodeproj`, selected media, checkpoint
  files or API response bodies.
- Run `motions-studio/setup/scrub-secrets.sh --check` immediately before every commit.
- Use English for code, comments, docs and commit messages.

---

## File map

| File | Responsibility |
|---|---|
| `ios/MotionKit/Sources/MotionKit/Models/Materials.swift` | Material, probe and upload Codable values |
| `ios/MotionKit/Sources/MotionKit/API/APIClient.swift` | Authenticated JSON POST, binary PUT and DELETE primitives |
| `ios/MotionKit/Sources/MotionKit/Upload/UploadCheckpoint.swift` | Checkpoint value and atomic disk journal |
| `ios/MotionKit/Sources/MotionKit/Upload/Uploader.swift` | Chunk planning, streaming, resume and completion |
| `ios/MotionKit/Sources/MotionKit/Stores/MaterialsStore.swift` | Observable materials/upload/delete/thumbnail state |
| `ios/MotionApp/Materials/ImportStaging.swift` | Copy Photos/Files provider URLs into durable app storage |
| `ios/MotionApp/Materials/MaterialsView.swift` | Materials grid, pickers, upload progress and deletion UI |
| `ios/MotionApp/MotionApp.swift` | Construct MaterialsStore with other credential-bound stores |
| `ios/MotionApp/RootView.swift` | Add the Material tab |
| `ios/MotionKit/Sources/motion-contract/main.swift` | Decode live `GET /v1/materials` only |
| `ios/README.md` | Phase 2 behavior and manual verification |

### Task 1: Material and upload API models + GET contract

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Models/Materials.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/Fixtures.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift`
- Modify: `ios/MotionKit/Sources/motion-contract/main.swift`

**Interfaces:**
- Consumes: `MotionJSON.decoder` with snake-case conversion.
- Produces: `MaterialKind`, `Material`, `MaterialsResponse`, `UploadOpenResponse`, `UploadStatus`,
  `MaterialProbe`, and `UploadCompleteResponse` as public `Decodable & Sendable & Equatable` values.

- [ ] **Step 1: Add failing fixtures and decoding tests**

Add fixtures shaped exactly like the Python serializers:

```swift
static let materials = #"""
{"materials":[
  {"id":"app/áo dài.png","owner":"app","name":"áo dài.png","bytes":901,
   "updated_at":1790000100.5,"kind":"image"},
  {"id":"42/driver.mp4","owner":"42","name":"driver.mp4","bytes":33554449,
   "updated_at":1790000000,"kind":"future_kind"}
]}
"""#

static let uploadOpen =
  #"{"upload_id":"abc123","chunk_size":33554432,"chunks_total":2}"#
static let uploadStatus =
  #"{"upload_id":"abc123","file_name":"driver.mp4","size":33554449,"chunk_size":33554432,"chunks_total":2,"received":[0]}"#
static let uploadComplete = #"""
{"material":{"id":"app/driver.mp4","owner":"app","name":"driver.mp4","bytes":33554449,
 "updated_at":1790000200,"kind":"video"},
 "probe":{"kind":"video","width":1080,"height":1920,"duration_s":12.5,
 "bitrate_kbps":4200,"size_bytes":33554449,"warning":"Video is larger than recommended."}}
"""#
```

Tests must assert unknown kinds decode to `.unknown`, `canDelete` is true only for owner `app`, and
all upload/probe fields decode without making optional server values required.

- [ ] **Step 2: Run the focused model tests and verify RED**

Run: `cd ios/MotionKit && swift test --filter ModelsTests`

Expected: compile failure because the material/upload types do not exist.

- [ ] **Step 3: Add the minimal models**

Implement the named public types. `MaterialKind` uses a custom decoder like `RunStatus` and exposes
`.image`, `.video`, `.other`, `.unknown`. `Material` conforms to `Identifiable` and has:

```swift
public var canDelete: Bool { owner == "app" }
```

Probe measurements that Python can return as `null` (`width`, `height`, `durationS`, `bitrateKbps`)
must be optional. `UploadStatus` also decodes optional `material` and `probe`, because status includes
them after completion.

- [ ] **Step 4: Run focused and full MotionKit tests and verify GREEN**

Run: `cd ios/MotionKit && swift test --filter ModelsTests && swift test`

Expected: all tests pass.

- [ ] **Step 5: Extend the live GET-only contract**

Add only:

```swift
await check("GET /v1/materials") {
    _ = try await client.get(MaterialsResponse.self, "v1", "materials")
}
```

Do not add any POST, PUT, PATCH or DELETE invocation to `motion-contract`.

- [ ] **Step 6: Check secrets and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Models/Materials.swift \
  ios/MotionKit/Tests/MotionKitTests/Fixtures.swift \
  ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift \
  ios/MotionKit/Sources/motion-contract/main.swift
git commit -m "feat(ios): model materials and upload responses"
```

### Task 2: Authenticated Phase 2 write primitives

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/API/APIClient.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift`

**Interfaces:**
- Consumes: Phase 1 `APIClient.authHeaders`, URL component encoding, `APIError` mapping.
- Produces:
  - `post<Response, Body>(_ response: Response.Type, body: Body?, _ components: String...)`
  - `put(data: Data, _ components: String...)`
  - `delete(_ components: String...)`

- [ ] **Step 1: Write failing request-shape tests**

Add tests that call the three new methods and inspect `StubURLProtocol.requests`:

```swift
@Test func postEncodesJSONAndAccepts201() async throws {
    StubURLProtocol.install { _ in TestSupport.json(Fixtures.uploadOpen, status: 201) }
    let result = try await TestSupport.client().post(
        UploadOpenResponse.self,
        body: UploadOpenRequest(fileName: "áo dài.png", size: 901),
        "v1", "uploads")
    let request = try #require(StubURLProtocol.requests.first)
    #expect(result.chunkSize == 33_554_432)
    #expect(request.httpMethod == "POST")
    #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
    #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
}
```

Also test bodyless POST to `complete`, binary PUT with exact body and
`application/octet-stream`, DELETE accepting 204, all auth headers on every verb, percent-encoded
owner/name components, and JSON error mapping on write responses.

- [ ] **Step 2: Run API client tests and verify RED**

Run: `cd ios/MotionKit && swift test --filter APIClientTests`

Expected: compile failure because the write methods and `UploadOpenRequest` are absent.

- [ ] **Step 3: Generalize the private request sender and add minimal public methods**

Keep GET callers unchanged. The internal request function must accept method, body, content type,
timeout and accepted statuses. JSON uses `JSONEncoder` with `.convertToSnakeCase`. Use accepted
statuses `{200, 201}` for POST, `{200}` for PUT, and `{204}` for DELETE. A bodyless POST sends no
invented `{}` body.

Add `UploadOpenRequest: Encodable & Sendable` to `Materials.swift`.

- [ ] **Step 4: Run focused and full tests and verify GREEN**

Run: `cd ios/MotionKit && swift test --filter APIClientTests && swift test`

Expected: all tests pass and the existing GET/ETag/Range tests remain green.

- [ ] **Step 5: Check secrets and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/API/APIClient.swift \
  ios/MotionKit/Sources/MotionKit/Models/Materials.swift \
  ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift
git commit -m "feat(ios): add authenticated material write requests"
```

### Task 3: Chunk geometry and durable upload checkpoint

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Upload/UploadCheckpoint.swift`
- Create: `ios/MotionKit/Sources/MotionKit/Upload/Uploader.swift`
- Create: `ios/MotionKit/Tests/MotionKitTests/UploadCheckpointTests.swift`
- Create: `ios/MotionKit/Tests/MotionKitTests/UploaderTests.swift`

**Interfaces:**
- Produces:
  - `UploadCheckpoint(uploadID:fileName:fileSize:localFileName:)`
  - `UploadCheckpointJournal(root:)` with `load()`, `save(_:)`, `sourceURL(for:)`, `clear()`
  - `ChunkPlan(fileSize:chunkSize:)` with `count`, `length(of:)`, `offset(of:)`, `bytes(in:)`
  - `UploadProgress` and the public shell of `Uploader` used in Task 4.

- [ ] **Step 1: Write failing chunk-plan and journal tests**

Required assertions:

```swift
@Test func nonMultipleOf32MiBHasShortFinalChunk() throws {
    let plan = try ChunkPlan(fileSize: 33_554_432 + 17, chunkSize: 33_554_432)
    #expect(plan.count == 2)
    #expect(try plan.offset(of: 1) == 33_554_432)
    #expect(try plan.length(of: 0) == 33_554_432)
    #expect(try plan.length(of: 1) == 17)
    #expect(try plan.bytes(in: [0]) == 33_554_432)
}
```

Journal tests use a temporary root, save a checkpoint, construct a new journal instance, load the
same value, confirm the source URL stays inside the root, and verify `clear()` removes the upload
directory. Add invalid-index and zero/negative-size cases.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `cd ios/MotionKit && swift test --filter UploadCheckpointTests && swift test --filter UploaderTests`

Expected: compile failure because checkpoint/journal/chunk plan do not exist.

- [ ] **Step 3: Implement the value types and atomic journal**

Use checked `Int64` arithmetic. Reject non-positive file/chunk sizes and out-of-range chunk indices
with `UploadFailure.invalidLocalFile`. `UploadCheckpointJournal.save` writes JSON to a sibling temp
file and replaces/moves it atomically. Its default production root is Application Support under
`Motion/Uploads/current`; tests always inject a temporary root.

Define progress as:

```swift
public enum UploadPhase: Sendable, Equatable { case preparing, transferring, processing, complete }
public struct UploadProgress: Sendable, Equatable {
    public let fileName: String
    public let phase: UploadPhase
    public let bytesSent: Int64
    public let totalBytes: Int64
}
```

- [ ] **Step 4: Run focused and full tests and verify GREEN**

Run: `cd ios/MotionKit && swift test --filter UploadCheckpointTests && swift test --filter UploaderTests && swift test`

Expected: all tests pass.

- [ ] **Step 5: Check secrets and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Upload \
  ios/MotionKit/Tests/MotionKitTests/UploadCheckpointTests.swift \
  ios/MotionKit/Tests/MotionKitTests/UploaderTests.swift
git commit -m "feat(ios): persist resumable upload checkpoints"
```

### Task 4: Streaming uploader and missing-chunk resume

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Upload/Uploader.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/UploaderTests.swift`

**Interfaces:**
- Consumes: API write primitives, upload models, `ChunkPlan`, `UploadCheckpointJournal`.
- Produces:
  - `Uploader.start(fileURL:fileName:progress:) async throws -> UploadCompleteResponse`
  - `Uploader.resume(progress:) async throws -> UploadCompleteResponse?`
  - `Uploader.hasPendingUpload() async -> Bool`

- [ ] **Step 1: Write the failing resume behavior tests**

Use a small temporary file (`abcdefghijkl`, 12 bytes) and stub the server with `chunk_size = 4`,
`chunks_total = 3`, `received = [0]`. Assert requests are exactly:

```text
POST /v1/uploads
GET  /v1/uploads/<id>
PUT  /v1/uploads/<id>/chunks/1   body "efgh"
PUT  /v1/uploads/<id>/chunks/2   body "ijkl"
POST /v1/uploads/<id>/complete
```

Assert no PUT for chunk 0, progress begins at four acknowledged bytes, `processing` is emitted before
completion, the checkpoint exists before the first PUT, and it is removed after the 201 response.

Add tests for recreating `Uploader` and calling `resume`, refusing a size-changed local file before
network, preserving the checkpoint on transport failure, clearing it on upload-status 404, and one
status/retry pass after `409 incomplete`.

- [ ] **Step 2: Run uploader tests and verify RED**

Run: `cd ios/MotionKit && swift test --filter UploaderTests`

Expected: tests fail because `start`/`resume` do not transfer or complete.

- [ ] **Step 3: Implement the minimal serial uploader**

`start` validates the local regular-file size, opens the upload, saves the checkpoint, then calls one
private `transfer(checkpoint:progress:allowIncompleteRetry:)` path. `resume` loads the checkpoint or
returns nil and calls that same path.

For each missing chunk, open `FileHandle(forReadingFrom:)`, seek to the checked offset, read exactly
the planned length, reject short reads, then await binary PUT. Do not retain earlier chunk data.
Deduplicate and bounds-check `received` before counting progress. After all PUTs, emit `.processing`,
POST complete, emit `.complete`, then clear the journal.

- [ ] **Step 4: Run focused and full tests and verify GREEN**

Run: `cd ios/MotionKit && swift test --filter UploaderTests && swift test`

Expected: all tests pass.

- [ ] **Step 5: Check secrets and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Upload/Uploader.swift \
  ios/MotionKit/Tests/MotionKitTests/UploaderTests.swift
git commit -m "feat(ios): stream and resume chunked uploads"
```

### Task 5: MaterialsStore orchestration

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Stores/MaterialsStore.swift`
- Create: `ios/MotionKit/Tests/MotionKitTests/MaterialsStoreTests.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/StubURLProtocol.swift` if ordered async responses need
  a request-safe helper.

**Interfaces:**
- Consumes: `APIClient`, `Uploader`, material models.
- Produces: a `@MainActor @Observable MaterialsStore` with `refresh`, `thumbnail(for:)`,
  `startUpload(fileURL:fileName:)`, `resumePendingUpload`, `delete(_:)`, and `clearError`.

- [ ] **Step 1: Write failing store tests**

Cover these observable outcomes:

- `refresh` loads and orders the server response as supplied, clears error, and records success time;
- a failed refresh after a success retains rows and sets `isStale`;
- two `thumbnail(for:)` calls make one GET and return the cached bytes;
- successful upload transitions through transfer/processing, inserts or refreshes the material, and
  records the non-empty warning by material id;
- only one upload task can run at once;
- successful DELETE removes the row;
- `409 in_use` keeps the row and exposes the server message;
- `404` triggers refresh and drops the server-removed row; and
- deleting a non-app material performs no request and returns a local permission error.

- [ ] **Step 2: Run store tests and verify RED**

Run: `cd ios/MotionKit && swift test --filter MaterialsStoreTests`

Expected: compile failure because `MaterialsStore` does not exist.

- [ ] **Step 3: Implement the store**

Use private task guards to prevent parallel upload/resume. Key thumbnail and warning caches by
`Material.id`. Build thumbnail URLs from `owner` and `name` as separate path components. On upload
completion, call `refresh` and retain the returned warning for the completed material even if the
refresh replaces the array. Error presentation uses `APIError.userMessage` where applicable and a
small local `MaterialStoreError` for ownership/busy/local-file cases.

- [ ] **Step 4: Run focused and full tests and verify GREEN**

Run: `cd ios/MotionKit && swift test --filter MaterialsStoreTests && swift test`

Expected: all tests pass.

- [ ] **Step 5: Check secrets and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/MaterialsStore.swift \
  ios/MotionKit/Tests/MotionKitTests/MaterialsStoreTests.swift \
  ios/MotionKit/Tests/MotionKitTests/StubURLProtocol.swift
git commit -m "feat(ios): coordinate material browsing and uploads"
```

### Task 6: Materials SwiftUI tab, Photos and Files import

**Files:**
- Create: `ios/MotionApp/Materials/ImportStaging.swift`
- Create: `ios/MotionApp/Materials/MaterialsView.swift`
- Create: `ios/MotionApp/Materials/MaterialCard.swift`
- Modify: `ios/MotionApp/MotionApp.swift`
- Modify: `ios/MotionApp/RootView.swift`
- Modify: `ios/MotionApp/Info.plist` through `ios/project.yml` if Photos read usage text is required by
  the generated project.
- Create: `ios/MotionKit/Tests/MotionKitTests/ImportFilenameTests.swift` only for pure filename rules
  moved into MotionKit; SwiftUI rendering itself is verified by compile and phone smoke.

**Interfaces:**
- Consumes: `MaterialsStore` only; views never invoke `APIClient` or `Uploader`.
- Produces: `MaterialsView(store:)`, a Photos transferable file representation, and a
  security-scoped Files importer path that both create an ordinary durable local source URL.

- [ ] **Step 1: Add a failing pure test for imported filename handling**

Put the pure helper in MotionKit so it can be tested without SwiftUI. Assert an empty provider name
becomes `upload.bin`, path separators are reduced to a basename, and a real extension is preserved.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `cd ios/MotionKit && swift test --filter ImportFilenameTests`

Expected: compile failure because the helper does not exist.

- [ ] **Step 3: Implement filename handling and import staging**

`ImportStaging` creates a unique directory below the same Application Support upload root, copies in
bounded streaming fashion, and balances `startAccessingSecurityScopedResource()` with `defer`. The
Photos `Transferable` uses `FileRepresentation` and copies the received file; it must not request a
whole-file `Data` representation.

- [ ] **Step 4: Build the Materials UI**

Follow the spec's two-column grid, authenticated store thumbnail cache, warning tag, upload phase
card, Add menu, confirmation dialog and app-owner-only Delete action. Use Theme tokens and existing
`ErrorBanner`, `StaleTag`, `SectionLabel`, and `.card()` patterns. Add `materials` to `AppModel`, reset
it with the other stores when credentials disappear, and insert the Material tab between Runs and
Output.

At the root or Materials screen, observe scene activation and call `resumePendingUpload()` once per
activation. Do not make upload cancellation depend on tab visibility.

- [ ] **Step 5: Run test and simulator build and verify GREEN**

Run:

```bash
cd ios/MotionKit && swift test --filter ImportFilenameTests
cd ../../ && make ios-build
```

Expected: test passes and `xcodebuild` ends with `BUILD SUCCEEDED`.

- [ ] **Step 6: Check secrets and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit ios/MotionApp ios/project.yml
git commit -m "feat(ios): add Materials tab and media pickers"
```

Confirm with `git status --short` that neither `ios/Secrets.xcconfig` nor
`ios/MotionApp.xcodeproj` is staged.

### Task 7: Documentation, contract and Phase 2 verification

**Files:**
- Modify: `ios/README.md`
- Modify: `docs/superpowers/specs/2026-09-22-swiftui-app-design.md` (mark Phase 2 implemented and link
  the refining spec; do not rewrite the API contract)
- Modify: this plan only to tick completed checkboxes if desired.

**Interfaces:**
- Consumes: all Phase 2 deliverables.
- Produces: reproducible Mac verification and a precise iPhone smoke checklist.

- [ ] **Step 1: Update user-facing docs**

Document the Material tab, Photos/Files sources, foreground-only behavior, retry/resume semantics,
owner-limited deletion, and why a cold launch cannot restore a warning. Add the phone smoke steps for
one image and one >32 MiB video. State that this flow does not rent a pod.

- [ ] **Step 2: Run all free verification gates**

Run:

```bash
make ios-test
make ios-build
make ios-contract
make batch-test
```

Expected: all commands exit 0. `ios-contract` output contains `ok   GET /v1/materials` and contains no
write route.

- [ ] **Step 3: Audit routes, secrets and repository state**

Run:

```bash
rg -n 'client\.(post|put|delete)|"POST"|"PUT"|"PATCH"|"DELETE"' \
  ios/MotionKit/Sources ios/MotionApp
git status --short
git diff --check
motions-studio/setup/scrub-secrets.sh --check
```

Expected: production write calls are limited to uploader/material deletion; no pod/job/draft write
route appears; generated/secrets files are not staged; diff and scrub pass.

- [ ] **Step 4: Commit docs and verification metadata**

Run `motions-studio/setup/scrub-secrets.sh --check` again immediately before:

```bash
git add ios/README.md docs/superpowers/specs/2026-09-22-swiftui-app-design.md \
  docs/superpowers/plans/2026-09-22-swiftui-app-phase-2.md
git commit -m "docs(ios): document Phase 2 material workflow"
```

- [ ] **Step 5: Install and manually verify on the connected iPhone**

Open the already generated `ios/MotionApp.xcodeproj` in Xcode, retain the selected personal Team,
choose the connected iPhone and Run. Verify:

1. Existing global materials and authenticated thumbnails load.
2. Upload a small image from Photos and delete it from its app-owned card.
3. Upload a video larger than 32 MiB; interrupt network/app after at least one completed chunk,
   relaunch, tap Retry if needed, and verify it resumes instead of restarting.
4. Confirm the final material thumbnail and any amber warning.
5. Confirm Runs and Outputs still load and no pod was created.

Do not include personal filenames, screenshots, checkpoint contents or response bodies in git.

### Task 8: Final review and branch handoff

**Files:** none unless review finds a defect; every defect follows a new RED/GREEN cycle and a focused
commit.

- [ ] **Step 1: Review implementation against every spec requirement**

Check scope, interfaces, error cases, lifecycle behavior, tests and security constraints section by
section. Confirm each required test exists and each write route is allowlisted by this plan.

- [ ] **Step 2: Run the final verification suite fresh**

```bash
make ios-test
make ios-build
make ios-contract
make batch-test
motions-studio/setup/scrub-secrets.sh --check
git status --short --branch
```

Expected: every command exits 0 and the worktree is clean. Report the branch and commits; do not merge
or push unless the user asks.
