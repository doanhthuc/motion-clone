# Motion iPhone app — Phase 3 New Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a server-authoritative, zero-spend New Job composer that selects a catalog pipeline, assigns compatible materials, manages the draft basket and validates it.

**Architecture:** MotionKit gains exact draft/catalog models, two generic authenticated write primitives and one `@MainActor @Observable` `DraftStore`. SwiftUI adds a central New Job tab whose role picker reuses the existing shared `MaterialsStore`; every mutation replaces local state with the server response, and no Phase A, run or pod endpoint is present.

**Tech Stack:** Swift 6.2, Swift Testing, Observation, SwiftUI, XcodeGen, existing stdlib HTTP control-plane API.

**Spec:** `docs/superpowers/specs/2026-09-22-swiftui-app-phase-3-design.md`

## Global Constraints

- Target iOS 26+ and Swift 6 strict concurrency; MotionKit must not import SwiftUI.
- The control-plane API is the only source of truth; do not persist a second local draft.
- Pipeline ids, roles, stages, role kinds, providers and provider labels come from `GET /v1/pipelines`; do not hardcode them.
- Phase 3 may call only `GET /v1/pipelines`, `GET /v1/draft`, `GET /v1/materials`, authenticated thumbnails and the existing `/v1/draft...` free mutations.
- Do not add Phase A, regenerate, rent, confirm, resume, pod control, cross-build automation or try-on-library behavior.
- Do not run a GPU pod lifecycle command or a live spend endpoint.
- Write code, comments, docs and commit messages in English.
- Run `motions-studio/setup/scrub-secrets.sh --check` immediately before every commit.

## File map

| File | Responsibility |
|---|---|
| `ios/MotionKit/Sources/MotionKit/Models/Drafts.swift` | Exact pipeline, draft, slot, basket, validation and patch models; role-kind filtering |
| `ios/MotionKit/Sources/MotionKit/API/APIClient.swift` | Authenticated JSON Patch and JSON-returning Delete |
| `ios/MotionKit/Sources/MotionKit/Stores/DraftStore.swift` | Catalog/draft state and serialized free mutations |
| `ios/MotionApp/NewJob/NewJobView.swift` | Screen composition, status, actions and sheets |
| `ios/MotionApp/NewJob/PipelinePicker.swift` | Catalog-driven pipeline and provider controls |
| `ios/MotionApp/NewJob/SlotRow.swift` | One required/optional role and its assigned state |
| `ios/MotionApp/NewJob/MaterialPicker.swift` | Kind-filtered material sheet using shared material cache |
| `ios/MotionApp/MotionApp.swift` | Construct the draft store for the active credentials |
| `ios/MotionApp/RootView.swift` | Insert the central New Job tab |
| `ios/MotionKit/Sources/motion-contract/main.swift` | Decode live GET-only pipeline and draft responses |
| `ios/README.md` | Phase 3 behavior and free phone smoke steps |

---

### Task 1: Catalog and draft models

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Models/Drafts.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/Fixtures.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift`

**Interfaces:**
- Consumes: Phase 2 `Material`, `MaterialKind` and `MaterialProbe`.
- Produces: `PipelineCatalogResponse`, `Pipeline`, `PipelineProvider`, `PipelineRoleKind`, `Draft`, `DraftSlot`, `DraftBatchEntry`, `DraftValidationResponse`, `PipelinePatch`, `ProviderPatch` and `SlotPatch`.

- [ ] **Step 1: Add realistic catalog and draft fixtures**

Append fixtures with every Phase 3 field, an unknown role kind, a nullable basket slot and a validation response:

```swift
static let pipelines = #"""
{"pipelines":[
  {"id":"motion-enhance","stages":["motion","enhance"],
   "required":["character","driver"],"optional":[],
   "roles":{"character":"image","driver":"video"},"providers":[]},
  {"id":"tryon-motion-enhance","stages":["tryon","motion","enhance"],
   "required":["character","driver","outfit"],"optional":["mask"],
   "roles":{"character":"image","driver":"video","outfit":"image","mask":"future_kind"},
   "providers":[{"id":"gemini","label":"Gemini"},{"id":"qwen-max","label":"Qwen Max"}]}
]}
"""#

static let draft = #"""
{"owner":"app","pipeline":"tryon-motion-enhance","provider":"gemini","generation":4,
 "slots":{"character":{"material_id":"app/model.png","name":"model.png","exists":true,
   "probe":{"kind":"image","width":1024,"height":1536,"duration_s":null,
   "bitrate_kbps":null,"size_bytes":900,"warning":""},"warning":""}},
 "required":["character","driver","outfit"],"optional":["mask"],
 "missing":["driver","outfit"],"validated":null,
 "batch":[{"digest":"abc123def0","run_id":"model__dress","pipeline":"tryon-motion-enhance",
   "provider":"gemini","slots":{"character":"app/model.png","outfit":"app/dress.png","driver":null}}],
 "jobs":1,"estimate_min":null}
"""#

static let validatedDraft = #"""
{"valid":true,"stale":false,"draft":{"owner":"app","pipeline":"motion-enhance",
 "provider":"gemini","generation":8,"slots":{},"required":[],"optional":[],"missing":[],
 "validated":true,"batch":[],"jobs":1,"estimate_min":48}}
"""#
```

- [ ] **Step 2: Write failing decode and filtering tests**

Add tests that pin optionality and forward compatibility:

```swift
@Test func pipelineAndDraftModelsDecode() throws {
    let catalog = try MotionJSON.decoder.decode(
        PipelineCatalogResponse.self, from: Fixtures.data(Fixtures.pipelines))
    #expect(catalog.pipelines[1].providers.map(\.id) == ["gemini", "qwen-max"])
    #expect(catalog.pipelines[1].roles["mask"] == .unknown)

    let draft = try MotionJSON.decoder.decode(Draft.self, from: Fixtures.data(Fixtures.draft))
    #expect(draft.generation == 4)
    #expect(draft.validated == nil)
    #expect(draft.slots["character"]?.probe.kind == "image")
    #expect(draft.batch.first?.digest == "abc123def0")
    #expect(draft.jobs == 1 && draft.estimateMin == nil)
}

@Test func roleKindsFilterOnlyCompatibleMaterials() throws {
    let image = Material(id: "app/a.png", owner: "app", name: "a.png",
                         bytes: 1, updatedAt: 1, kind: .image)
    let video = Material(id: "app/a.mp4", owner: "app", name: "a.mp4",
                         bytes: 1, updatedAt: 1, kind: .video)
    #expect(PipelineRoleKind.image.accepts(image))
    #expect(!PipelineRoleKind.image.accepts(video))
    #expect(PipelineRoleKind.video.accepts(video))
    #expect(!PipelineRoleKind.unknown.accepts(image))
}

@Test func validationResponseKeepsNestedAuthoritativeDraft() throws {
    let result = try MotionJSON.decoder.decode(
        DraftValidationResponse.self, from: Fixtures.data(Fixtures.validatedDraft))
    #expect(result.valid && !result.stale)
    #expect(result.draft.validated == true)
    #expect(result.draft.estimateMin == 48)
}
```

- [ ] **Step 3: Run the focused tests and verify failure**

Run: `cd ios/MotionKit && swift test --filter ModelsTests`

Expected: compilation fails because the Phase 3 model types do not exist.

- [ ] **Step 4: Implement the model boundary**

Create `Drafts.swift`. Keep raw ids untouched and put only compatibility logic on the role kind:

```swift
import Foundation

public enum PipelineRoleKind: String, Decodable, Sendable, Equatable {
    case image, video, unknown

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: raw) ?? .unknown
    }

    public func accepts(_ material: Material) -> Bool {
        switch (self, material.kind) {
        case (.image, .image), (.video, .video): true
        default: false
        }
    }
}

public struct PipelineProvider: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let label: String
}

public struct Pipeline: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let stages: [String]
    public let required: [String]
    public let optional: [String]
    public let roles: [String: PipelineRoleKind]
    public let providers: [PipelineProvider]
}

public struct PipelineCatalogResponse: Decodable, Sendable, Equatable {
    public let pipelines: [Pipeline]
}

public struct DraftSlot: Decodable, Sendable, Equatable {
    public let materialID: String?
    public let name: String
    public let exists: Bool
    public let probe: MaterialProbe
    public let warning: String
}

public struct DraftBatchEntry: Decodable, Sendable, Equatable, Identifiable {
    public let digest: String
    public let runID: String
    public let pipeline: String
    public let provider: String
    public let slots: [String: String?]
    public var id: String { digest }
}

public struct Draft: Decodable, Sendable, Equatable {
    public let owner: String
    public let pipeline: String
    public let provider: String
    public let generation: Int
    public let slots: [String: DraftSlot]
    public let required: [String]
    public let optional: [String]
    public let missing: [String]
    public let validated: Bool?
    public let batch: [DraftBatchEntry]
    public let jobs: Int
    public let estimateMin: Int?
    public let dropped: [String]?
}

public struct DraftValidationResponse: Decodable, Sendable, Equatable {
    public let valid: Bool
    public let stale: Bool
    public let draft: Draft
    public let output: String?
}

public struct PipelinePatch: Encodable, Sendable { public let pipeline: String }
public struct ProviderPatch: Encodable, Sendable { public let provider: String }
public struct SlotPatch: Encodable, Sendable {
    public let slots: [String: String?]
    public init(role: String, materialID: String?) { slots = [role: materialID] }
}
```

Give patch structs public initializers where the memberwise initializer is not public. Confirm
`MotionJSON.decoder`'s snake-case conversion maps `material_id`, `run_id` and `estimate_min`.

- [ ] **Step 5: Run all MotionKit tests**

Run: `make ios-test`

Expected: all existing and new tests pass.

- [ ] **Step 6: Scrub and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Models/Drafts.swift \
  ios/MotionKit/Tests/MotionKitTests/Fixtures.swift \
  ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift
git commit -m "feat(ios): model pipelines and drafts"
```

---

### Task 2: Authenticated Patch and JSON Delete

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/API/APIClient.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift`

**Interfaces:**
- Consumes: Task 1 patch values and the existing private `send`/`decode` helpers.
- Produces: `APIClient.patch(_:body:_:)` and overloaded `APIClient.delete(_:_:) -> Response`.

- [ ] **Step 1: Write failing transport tests**

Add one test for snake-case Patch JSON and one for a decoded `200` Delete:

```swift
@Test func patchEncodesJSONAndAuthHeaders() async throws {
    StubURLProtocol.install { _ in TestSupport.json(Fixtures.draft) }
    _ = try await TestSupport.client().patch(
        Draft.self, body: SlotPatch(role: "driver", materialID: "app/dance.mp4"),
        "v1", "draft")
    let request = try #require(StubURLProtocol.requests.first)
    #expect(request.httpMethod == "PATCH")
    #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
    #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
    let data = try #require(request.httpBody)
    let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    let slots = try #require(json["slots"] as? [String: Any])
    #expect(slots["driver"] as? String == "app/dance.mp4")
}

@Test func patchPreservesExplicitNullSlot() async throws {
    StubURLProtocol.install { _ in TestSupport.json(Fixtures.draft) }
    _ = try await TestSupport.client().patch(
        Draft.self, body: SlotPatch(role: "outfit", materialID: nil), "v1", "draft")
    let data = try #require(StubURLProtocol.requests.first?.httpBody)
    let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
    let slots = try #require(json["slots"] as? [String: Any])
    #expect(slots["outfit"] is NSNull)
}

@Test func decodedDeleteAccepts200AndEncodesDigest() async throws {
    StubURLProtocol.install { _ in TestSupport.json(Fixtures.draft) }
    let draft = try await TestSupport.client().delete(
        Draft.self, "v1", "draft", "batch", "a/b c")
    #expect(draft.generation == 4)
    let request = try #require(StubURLProtocol.requests.first)
    #expect(request.httpMethod == "DELETE")
    #expect(request.url?.absoluteString.hasSuffix("/v1/draft/batch/a%2Fb%20c") == true)
}
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `cd ios/MotionKit && swift test --filter APIClientTests`

Expected: compilation fails because `patch` and the decoded Delete overload do not exist.

- [ ] **Step 3: Add the two narrow API primitives**

Use the same encoder policy and headers as existing JSON Post:

```swift
public func patch<Response: Decodable & Sendable, Body: Encodable & Sendable>(
    _ response: Response.Type, body: Body, _ components: String...
) async throws(APIError) -> Response {
    let encoded: Data
    do {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoded = try encoder.encode(body)
    } catch {
        throw .transport("couldn't encode the request: \(error.localizedDescription)")
    }
    let (data, _) = try await send(
        url(components), method: "PATCH", body: encoded, contentType: "application/json",
        extraHeaders: [:], okStatuses: [200])
    return try decode(response, data)
}

public func delete<Response: Decodable & Sendable>(
    _ response: Response.Type, _ components: String...
) async throws(APIError) -> Response {
    let (data, _) = try await send(
        url(components), method: "DELETE", extraHeaders: [:], okStatuses: [200])
    return try decode(response, data)
}
```

Do not alter the existing bodyless `delete(_ components:)`, which accepts only `204` for material
deletion.

- [ ] **Step 4: Run the API client and full package tests**

Run: `cd ios/MotionKit && swift test --filter APIClientTests && swift test`

Expected: both commands pass, including explicit JSON null and existing bodyless Delete coverage.

- [ ] **Step 5: Scrub and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/API/APIClient.swift \
  ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift
git commit -m "feat(ios): add authenticated draft writes"
```

---

### Task 3: DraftStore state machine

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Stores/DraftStore.swift`
- Create: `ios/MotionKit/Tests/MotionKitTests/DraftStoreTests.swift`

**Interfaces:**
- Consumes: Tasks 1–2 models and API methods.
- Produces: `DraftStore.load()`, `refresh()`, `selectPipeline(_:)`, `selectProvider(_:)`,
  `assign(role:materialID:)`, `addToBatch()`, `dropFromBatch(_:)`, `clear()`, `validate()` and
  `dismissMessage()`.

- [ ] **Step 1: Write failing load and successful-mutation tests**

Create a serialized MainActor suite. Route stub responses by method/path and assert every server
response replaces state:

```swift
extension URLProtocolTests {
@Suite @MainActor struct DraftStoreTests {
    @Test func loadFetchesCatalogAndDraft() async {
        StubURLProtocol.install { request in
            request.url?.path == "/v1/pipelines"
                ? TestSupport.json(Fixtures.pipelines)
                : TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()
        #expect(store.catalog.count == 2)
        #expect(store.draft?.generation == 4)
        #expect(store.loaded && store.lastSuccess != nil && !store.isStale)
    }

    @Test func everyMutationUsesReturnedDraft() async {
        StubURLProtocol.install { request in
            if request.url?.path == "/v1/pipelines" { return TestSupport.json(Fixtures.pipelines) }
            return TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()
        await store.selectPipeline("motion-enhance")
        await store.selectProvider("gemini")
        await store.assign(role: "driver", materialID: "app/dance.mp4")
        await store.addToBatch()
        await store.dropFromBatch("abc123def0")
        await store.clear()
        #expect(store.draft?.generation == 4)
        #expect(StubURLProtocol.requests.map(\.httpMethod) ==
                ["GET", "GET", "PATCH", "PATCH", "PATCH", "POST", "DELETE", "POST"])
    }
}
}
```

- [ ] **Step 2: Add failing concurrency, validation and recovery tests**

Use an actor-controlled delayed handler or continuation so one write remains active. Assert:

```swift
#expect(store.isMutating)
await store.selectProvider("qwen-max")
#expect(StubURLProtocol.requests.filter { $0.httpMethod == "PATCH" }.count == 1)
#expect(store.message == "Another draft change is still in progress.")
```

Add cases for:

- successful validation installs `response.draft` and its estimate;
- stale validation installs the nested draft, sets `validationWasStale`, and never reports Ready;
- `422` exposes the server message without discarding the last draft;
- failed refresh preserves data and makes `isStale` true;
- a transport failure during Patch attempts `GET /v1/draft` before `isMutating` becomes false;
- an assigning `404` sets `needsMaterialsRefresh`, refreshes the draft, and preserves the server
  message for the view.

- [ ] **Step 3: Run the focused suite and verify failure**

Run: `cd ios/MotionKit && swift test --filter DraftStoreTests`

Expected: compilation fails because `DraftStore` does not exist.

- [ ] **Step 4: Implement the observable store**

Create the state surface and funnel all writes through one helper:

```swift
import Foundation
import Observation

@MainActor @Observable
public final class DraftStore {
    public private(set) var catalog: [Pipeline] = []
    public private(set) var draft: Draft?
    public private(set) var loaded = false
    public private(set) var lastSuccess: Date?
    public private(set) var error: APIError?
    public private(set) var message: String?
    public private(set) var isRefreshing = false
    public private(set) var isMutating = false
    public private(set) var isValidating = false
    public private(set) var validationWasStale = false
    public private(set) var needsMaterialsRefresh = false

    private let client: APIClient

    public init(client: APIClient) { self.client = client }
    public var isStale: Bool { loaded && error != nil }
    public var isBusy: Bool { isMutating || isValidating }
    public var selectedPipeline: Pipeline? {
        guard let id = draft?.pipeline else { return nil }
        return catalog.first { $0.id == id }
    }
    public var isReady: Bool {
        draft?.validated == true && !validationWasStale
    }

    public func load() async {
        guard !isRefreshing && !isBusy else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            async let catalog = client.get(PipelineCatalogResponse.self, "v1", "pipelines")
            async let draft = client.get(Draft.self, "v1", "draft")
            let values = try await (catalog, draft)
            self.catalog = values.0.pipelines
            self.draft = values.1
            loaded = true
            lastSuccess = .now
            error = nil
            checkContract()
        } catch let api as APIError { error = api }
        catch { error = .transport(error.localizedDescription) }
    }
}
```

Implement `refresh()` as a draft GET after the initial catalog is known, and reload both when the
catalog is empty. `checkContract()` sets a decoding-style error/message when the catalog is empty or
does not contain `draft.pipeline`; it never changes the draft.

Use a private mutation helper whose operation returns a `Draft`:

```swift
private func mutate(_ operation: () async throws(APIError) -> Draft) async {
    guard !isBusy else {
        message = "Another draft change is still in progress."
        return
    }
    isMutating = true
    error = nil
    message = nil
    validationWasStale = false
    defer { isMutating = false }
    do {
        accept(try await operation())
    } catch let api {
        if api.isOffline { await refreshAfterAmbiguousWrite() }
        error = api
        message = api.userMessage
    }
}
```

Each public method supplies the exact route/body from the interface block. `assign` handles a `404`
by setting `needsMaterialsRefresh = true` and refreshing the draft before returning. Add
`acknowledgeMaterialsRefresh()` to clear that flag after the view refreshes materials.

`validate()` uses its own `isValidating` flag and bodyless Post:

```swift
let response = try await client.post(DraftValidationResponse.self, "v1", "draft", "validate")
draft = response.draft
validationWasStale = response.stale
message = response.stale ? "The draft changed during validation. Validate it again." : nil
```

`accept(_:)` always assigns the returned draft, sets `loaded/lastSuccess`, clears stale errors and
uses `draft.dropped` to create an informational message such as `Removed incompatible slots: outfit.`

- [ ] **Step 5: Run focused and full tests**

Run: `cd ios/MotionKit && swift test --filter DraftStoreTests && swift test`

Expected: all tests pass with no overlapping write and no stale Ready state.

- [ ] **Step 6: Scrub and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/DraftStore.swift \
  ios/MotionKit/Tests/MotionKitTests/DraftStoreTests.swift
git commit -m "feat(ios): coordinate new job drafts"
```

---

### Task 4: New Job SwiftUI flow

**Files:**
- Create: `ios/MotionApp/NewJob/PipelinePicker.swift`
- Create: `ios/MotionApp/NewJob/SlotRow.swift`
- Create: `ios/MotionApp/NewJob/MaterialPicker.swift`
- Create: `ios/MotionApp/NewJob/NewJobView.swift`

**Interfaces:**
- Consumes: Task 3 `DraftStore`, Phase 2 `MaterialsStore`, existing `MaterialCard`, `Theme`,
  `SectionLabel`, `StaleTag` and `ErrorBanner`.
- Produces: `NewJobView(store:materials:)` for root integration.

- [ ] **Step 1: Add catalog-driven picker components**

`PipelinePicker` receives `Pipeline`, `[Pipeline]`, `disabled`, and async callbacks. Render pipeline
ids with a local `displayName(_:)` that replaces `-`/`_` with spaces and capitalizes words; send raw
ids through callbacks. Render provider buttons only when `pipeline.providers` is non-empty and use
the server labels.

`SlotRow` receives role, `required`, kind, optional `DraftSlot`, optional thumbnail and a tap callback.
Its accessibility value must say `Missing required`, `Empty optional`, or the assigned file name.
Show `slot.warning` in amber and `Required`/`Optional` as text so color is not the only signal.

- [ ] **Step 2: Add the kind-filtered material sheet**

Implement selection from shared state only:

```swift
struct MaterialPicker: View {
    let role: String
    let kind: PipelineRoleKind
    let selectedID: String?
    let materials: MaterialsStore
    let onSelect: (String?) -> Void
    @Environment(\.dismiss) private var dismiss

    private var eligible: [MotionKit.Material] {
        materials.materials.filter(kind.accepts)
    }
}
```

The sheet calls `materials.refresh()` only when `materials.loaded` is false, shows unknown-kind text
instead of guessing, allows assignment without a thumbnail, and offers Clear when `selectedID != nil`.
Use `materials.thumbnail(for:)` for authenticated images; do not call `APIClient` from the view.

- [ ] **Step 3: Assemble the composer**

`NewJobView` owns only presentation selection (`selectedRole`, `dropCandidate`) and renders from the
stores:

```swift
struct NewJobView: View {
    let store: DraftStore
    let materials: MaterialsStore
    @State private var selectedRole: String?
    @State private var dropCandidate: DraftBatchEntry?

    var body: some View {
        Group {
            if let draft = store.draft, let pipeline = store.selectedPipeline {
                composer(draft: draft, pipeline: pipeline)
            } else if store.isRefreshing {
                ProgressView("Loading draft…")
            } else {
                ContentUnavailableView("New Job unavailable", systemImage: "exclamationmark.triangle")
            }
        }
        .background(Theme.bg)
        .task { await store.load() }
        .refreshable { await store.refresh() }
    }
}
```

The scroll content order is:

1. `New Job` title, server `jobs` count and stale tag.
2. Error/information banners with Retry or dismiss.
3. `PipelinePicker`.
4. Required slots followed by optional slots, using the catalog's role kind.
5. Readiness text computed from `draft.required.count - draft.missing.count`; only an empty missing
   array says `Ready`.
6. `Add to batch` disabled when missing is non-empty or the store is busy; Clear is destructive.
7. Batch rows keyed by digest with a confirmation dialog before Drop.
8. Validate disabled when `jobs == 0` or busy; show progress, Ready + estimate, stale result, or the
   server error message.

When `store.needsMaterialsRefresh` becomes true, refresh `MaterialsStore`, call
`store.acknowledgeMaterialsRefresh()`, and close the picker if the selected item disappeared. Launch
validation from the button's unstructured `Task`, not the view's `.task`, so changing tabs does not
cancel the request; `DraftStore` remains the owner of its observable in-flight state.

- [ ] **Step 4: Build the simulator target**

Run: `make ios-build`

Expected: `BUILD SUCCEEDED`; Swift 6 emits no actor-isolation error from view callbacks.

- [ ] **Step 5: Run the package tests again**

Run: `make ios-test`

Expected: all MotionKit tests pass; views introduced no MotionKit dependency on SwiftUI.

- [ ] **Step 6: Scrub and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/NewJob
git commit -m "feat(ios): build the new job composer"
```

---

### Task 5: App integration and GET-only contract

**Files:**
- Modify: `ios/MotionApp/MotionApp.swift`
- Modify: `ios/MotionApp/RootView.swift`
- Modify: `ios/MotionKit/Sources/motion-contract/main.swift`

**Interfaces:**
- Consumes: Task 3 `DraftStore` and Task 4 `NewJobView`.
- Produces: one credential-scoped shared draft store and the Runs · Material · `+` · Output tab order.

- [ ] **Step 1: Construct and clear DraftStore with the other credential-scoped stores**

Add `private(set) var draft: DraftStore?` to `AppModel`. In `reconnect()`, clear it in the missing
credentials branch and create `DraftStore(client: client)` beside MaterialsStore in the valid branch.
Do not create another APIClient or MaterialsStore.

- [ ] **Step 2: Insert the central New Job tab**

Require `draft` in RootView's authenticated unwrap and insert:

```swift
Tab("New Job", systemImage: "plus.circle.fill") {
    NavigationStack { NewJobView(store: draft, materials: materials) }
}
```

The exact order is Runs, Material, New Job, Output. Keep material upload resume ownership at RootView;
the New Job tab must not change it.

- [ ] **Step 3: Extend the contract CLI with reads only**

Add:

```swift
await check("GET /v1/pipelines") {
    _ = try await client.get(PipelineCatalogResponse.self, "v1", "pipelines")
}
await check("GET /v1/draft") {
    _ = try await client.get(Draft.self, "v1", "draft")
}
```

Audit the entire executable with `rg -n 'post|patch|put|delete' ios/MotionKit/Sources/motion-contract`;
expected: no matches that invoke an HTTP write.

- [ ] **Step 4: Run integration gates**

Run:

```bash
make ios-test
make ios-build
make ios-contract
```

Expected: tests and build pass; contract prints `ok` for both new GET routes and performs no writes.

- [ ] **Step 5: Scrub and commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/MotionApp.swift ios/MotionApp/RootView.swift \
  ios/MotionKit/Sources/motion-contract/main.swift
git commit -m "feat(ios): add the new job tab"
```

---

### Task 6: Documentation and Phase 3 verification

**Files:**
- Modify: `ios/README.md`
- Modify: `docs/superpowers/specs/2026-09-22-swiftui-app-design.md`
- Modify: `docs/superpowers/specs/2026-09-22-swiftui-app-phase-3-design.md`

**Interfaces:**
- Consumes: all Phase 3 deliverables.
- Produces: accurate shipped status, free manual smoke instructions and final verification evidence.

- [ ] **Step 1: Document shipped behavior and deferred smoke**

Add a New Job section to `ios/README.md` covering catalog-driven selection, role-kind filtering,
server-authoritative draft mutations, basket add/drop, validation and the explicit no-spend boundary.
Add free simulator/iPhone smoke steps:

1. Open New Job and switch between two pipelines; confirm incompatible slots disappear with notice.
2. Assign image and video materials and verify incompatible kinds are absent from each picker.
3. Clear a required slot; confirm Add is disabled and the role is named missing.
4. Complete a job, add it to the basket, remove it by digest-backed row, and add it again.
5. Validate; confirm Ready and estimate appear, then edit one slot and confirm Ready disappears.
6. Confirm no Phase A, Run, rent or pod action exists in the Phase 3 UI.

Keep the Phase 2 phone smoke and state that it remains pending rather than claiming it ran.

- [ ] **Step 2: Mark Phase 3 implemented in both specs**

Change the base design status to `Phases 1–3 implemented`, mark row 3 implemented with a link to the
Phase 3 design, and change the Phase 3 design status to `implemented`. Do not mark any manual phone
smoke complete.

- [ ] **Step 3: Run every free gate from a clean implementation state**

Run:

```bash
make ios-test
make ios-build
make ios-contract
make batch-test
motions-studio/setup/scrub-secrets.sh --check
git diff --check
```

Expected:

- all MotionKit tests pass;
- simulator build ends with `BUILD SUCCEEDED`;
- contract decodes pipelines and draft with GET only;
- batch tests pass, protecting the server contract;
- scrub and whitespace checks exit 0.

Do not run `make gpu-smoke`, provision a pod or call a spend-capable route.

- [ ] **Step 4: Audit route and repository boundaries**

Run:

```bash
rg -n '"v1", "(runs|pod)|phase-a|confirm|resume|regen' \
  ios/MotionKit/Sources/MotionKit/Stores/DraftStore.swift ios/MotionApp/NewJob
rg -n 'post|patch|put|delete' ios/MotionKit/Sources/motion-contract
git status --short
```

Expected: the Phase 3 store/UI contains no spend route, the contract executable contains no write
invocation, and only intended Phase 3 files are modified.

- [ ] **Step 5: Scrub and commit documentation**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/README.md \
  docs/superpowers/specs/2026-09-22-swiftui-app-design.md \
  docs/superpowers/specs/2026-09-22-swiftui-app-phase-3-design.md
git commit -m "docs(ios): document phase 3 new job workflow"
```

- [ ] **Step 6: Review the complete Phase 3 diff**

Run:

```bash
git log --oneline 96a40d8..HEAD
git diff --stat 96a40d8^..HEAD
git diff --check 96a40d8^..HEAD
git status --short --branch
```

Expected: focused Phase 3 commits, no whitespace errors and a clean worktree. Report physical-phone
smoke as deferred, not passed.
