# Motion iPhone app — Phase 4 run flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the iPhone app start Phase A, review/regenerate/keep try-ons, read the rent panel, and
confirm or resume a run — each spend exactly one tap, never duplicated by a retry.

**Architecture:** A `SpendGate` actor (MotionKit `Money/`) is the only sender of the four spend calls;
it persists a one-entry idempotency ledger before sending and owns every retry rule. A `RunFlow`
`@MainActor @Observable` store owns the reads and the phase machine and talks to the gate through the
`SpendSending` protocol. SwiftUI views under `MotionApp/RunFlow/` read only `RunFlow`.

**Tech Stack:** Swift 6 (strict concurrency), SwiftUI iOS 26, swift-testing (`import Testing`),
XCTest UI tests, XcodeGen.

**Spec:** `docs/superpowers/specs/2026-09-23-swiftui-app-phase-4-design.md` (parent:
`docs/superpowers/specs/2026-09-22-swiftui-app-design.md`; server contract:
`docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` §5.3–§5.10).

## Global Constraints

- iOS 26+, Swift 6, `@Observable`; MotionKit imports no SwiftUI.
- Views never call `APIClient`. Only `SpendGate` sends `phase-a`, `regen`, `confirm`, `resume`.
- One UUID `Idempotency-Key` per tap; only `SpendGate.perform` mints one. Retries reuse it.
- `503 bot_busy`: same key, 5 s apart, at most 3 retries. `409 outcome_unknown`: no retry.
  `409 stale_panel`: re-read, wait for a new tap. Ledger entries ≥ 20 h are never resent.
- Spend requests time out at 90 s (under Cloudflare's ~100 s).
- No new API route or field. Nothing under `scripts/**` changes (a push there redeploys the bot).
- No live `phase-a`, `regen`, `confirm` or `resume` during development, except the three bogus-token
  refusals of `make ios-refusal-smoke`. No pod is rented.
- English for code, comments, docs, commit messages. No `#region ALD` markers.
- `motions-studio/setup/scrub-secrets.sh --check` exits 0 before every commit. Never commit
  `ios/Secrets.xcconfig`, personal media or live payloads.
- The working tree carries the user's uncommitted edits to `AGENTS.md`, `CLAUDE.md`, `ios/README.md`
  and the untracked `docs/superpowers/swiftui-app-progress.md`. Never `git add -A`; stage exact paths.
  Task 11 asks the user before committing any of those four files.
- Commit trailer on every commit: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

```text
ios/MotionKit/Sources/MotionKit/
  Money/Guidance.swift            Task 1  closed regenerate vocabulary
  Money/SpendIntent.swift         Task 1  the four spend calls: path + JSON body; Codable
  Money/IdempotencyLedger.swift   Task 2  one-entry fsync'd journal
  Money/SpendResult.swift         Task 4  SpendResult, SpendSending, SpendAccepted
  Money/SpendGate.swift           Task 4  actor: ledger → send → classify → retry
  API/APIClient.swift             Task 3  + spendPost, + get(query:)
  Models/RunFlow.swift            Task 5  TryonPreviews, RentPanel, Keep request/record
  Formatting.swift                Task 5  + CostEstimate.quote
  Stores/RunFlow.swift            Tasks 6–7
  Sources/motion-contract/main.swift  Task 8  + 2 GETs, + --refusal-smoke
ios/MotionKit/Tests/MotionKitTests/
  StubURLProtocol.swift           Task 3  status < 0 → transport failure
  SpendIntentTests.swift          Task 1
  IdempotencyLedgerTests.swift    Task 2
  APIClientTests.swift            Task 3  (append)
  SpendGateTests.swift            Task 4
  Fixtures.swift, ModelsTests.swift  Task 5 (append)
  FakeSpendGate.swift             Task 6
  RunFlowTests.swift              Tasks 6–7
ios/MotionApp/
  MotionApp.swift, RootView.swift Task 9  gate + flow wiring, tab selection, spend banner
  RunFlow/RecordingSpendGate.swift Task 9 UI-test stand-in
  RunFlow/RunFlowView.swift       Task 10 compose / Phase A / previews / choice / outcome
  RunFlow/TryonPreviewCard.swift  Task 10 image, versions, Keep, Regenerate sheet
  RunFlow/RentPanelView.swift     Task 10
  NewJob/NewJobView.swift, Runs/RunsView.swift, Runs/RunDetailView.swift  Task 10 entry points
ios/MotionAppUITests/Phase4SmokeTests.swift  Task 10
Makefile                          Task 8  ios-refusal-smoke
docs/…                            Task 11
```

---

### Task 1: Guidance and SpendIntent

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Money/Guidance.swift`
- Create: `ios/MotionKit/Sources/MotionKit/Money/SpendIntent.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/SpendIntentTests.swift`

**Interfaces:**
- Produces: `Guidance` (`keepFace`/`tighterCrop`/`matchLighting`, `rawValue` = wire value, `label`),
  `SpendProvider` (`runpod`/`vast`), `TryonChoice` (`reuse`/`rerun`),
  `SpendIntent` with `path: [String]`, `runID: String?`, `body() -> Data`, `kind: SpendKind`,
  `SpendKind` (`phaseA`/`regen`/`confirm`/`resume`). `SpendIntent` is `Codable, Sendable, Equatable`.

- [ ] **Step 1: Write the failing tests**

```swift
import Foundation
import Testing
@testable import MotionKit

@Suite struct SpendIntentTests {
    private func object(_ intent: SpendIntent) throws -> [String: Any] {
        try #require(JSONSerialization.jsonObject(with: intent.body()) as? [String: Any])
    }

    @Test func guidanceHasExactlyTheThreeWireValues() {
        #expect(Guidance.allCases.map(\.rawValue) == ["keep_face", "tighter_crop", "match_lighting"])
    }

    @Test func phaseAPostsAnEmptyObject() throws {
        #expect(SpendIntent.phaseA.path == ["v1", "runs", "phase-a"])
        #expect(try object(.phaseA).isEmpty)
        #expect(SpendIntent.phaseA.runID == nil)
    }

    @Test func regenBodyCarriesTokenAndOrderedGuidance() throws {
        let intent = SpendIntent.regen(runID: "tg-1000", index: "2", runToken: "123.4",
                                       guidance: [.keepFace, .matchLighting])
        #expect(intent.path == ["v1", "runs", "tg-1000", "tryon", "2", "regen"])
        let body = try object(intent)
        #expect(body["run_token"] as? String == "123.4")
        #expect(body["guidance"] as? [String] == ["keep_face", "match_lighting"])
    }

    @Test func confirmOmitsNilFields() throws {
        let bare = SpendIntent.confirm(runID: "tg-1000", provider: .vast, panelToken: "9.1",
                                       gpu: nil, tryon: nil)
        #expect(bare.path == ["v1", "runs", "tg-1000", "confirm"])
        #expect(try object(bare).keys.sorted() == ["panel_token", "provider"])
        let full = SpendIntent.confirm(runID: "tg-1000", provider: .runpod, panelToken: "9.1",
                                       gpu: "NVIDIA GeForce RTX 5090", tryon: .reuse)
        let body = try object(full)
        #expect(body["provider"] as? String == "runpod")
        #expect(body["gpu"] as? String == "NVIDIA GeForce RTX 5090")
        #expect(body["tryon"] as? String == "reuse")
    }

    @Test func resumeBody() throws {
        let intent = SpendIntent.resume(runID: "tg-1000", provider: .runpod, runToken: "77",
                                        gpu: "NVIDIA GeForce RTX 5090")
        #expect(intent.path == ["v1", "runs", "tg-1000", "resume"])
        #expect(intent.runID == "tg-1000")
        let body = try object(intent)
        #expect(body["run_token"] as? String == "77")
        #expect(body["provider"] as? String == "runpod")
    }

    @Test func intentRoundTripsThroughCodable() throws {
        let intent = SpendIntent.confirm(runID: "tg-1000", provider: .runpod, panelToken: "t",
                                         gpu: "g", tryon: .rerun)
        let decoded = try JSONDecoder().decode(SpendIntent.self, from: JSONEncoder().encode(intent))
        #expect(decoded == intent)
        #expect(decoded.kind == .confirm)
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ios/MotionKit && swift test --filter SpendIntentTests`
Expected: compile failure — `cannot find 'SpendIntent' in scope`.

- [ ] **Step 3: Implement**

`Guidance.swift`:

```swift
import Foundation

/// The closed regenerate vocabulary (API spec §5.10). The server answers any
/// other string with 400, so the phone can only ever send these three.
public enum Guidance: String, Codable, Sendable, CaseIterable, Hashable {
    case keepFace = "keep_face"
    case tighterCrop = "tighter_crop"
    case matchLighting = "match_lighting"

    public var label: String {
        switch self {
        case .keepFace: "Keep face"
        case .tighterCrop: "Tighter crop"
        case .matchLighting: "Match lighting"
        }
    }
}
```

`SpendIntent.swift`:

```swift
import Foundation

public enum SpendProvider: String, Codable, Sendable, Equatable {
    case runpod, vast
}

public enum TryonChoice: String, Codable, Sendable, Equatable {
    case reuse, rerun
}

public enum SpendKind: Sendable, Equatable {
    case phaseA, regen, confirm, resume
}

/// One spend call, exactly as `AppRuns`/`AppPod` read it (bot.py). Persisted
/// in the idempotency ledger, so a replay after an app kill resends the very
/// same path and body with the very same key.
public enum SpendIntent: Codable, Sendable, Equatable {
    case phaseA
    case regen(runID: String, index: String, runToken: String, guidance: [Guidance])
    case confirm(runID: String, provider: SpendProvider, panelToken: String, gpu: String?,
                 tryon: TryonChoice?)
    case resume(runID: String, provider: SpendProvider, runToken: String, gpu: String?)

    public var kind: SpendKind {
        switch self {
        case .phaseA: .phaseA
        case .regen: .regen
        case .confirm: .confirm
        case .resume: .resume
        }
    }

    public var runID: String? {
        switch self {
        case .phaseA: nil
        case let .regen(runID, _, _, _), let .confirm(runID, _, _, _, _),
             let .resume(runID, _, _, _): runID
        }
    }

    public var path: [String] {
        switch self {
        case .phaseA: ["v1", "runs", "phase-a"]
        case let .regen(runID, index, _, _): ["v1", "runs", runID, "tryon", index, "regen"]
        case let .confirm(runID, _, _, _, _): ["v1", "runs", runID, "confirm"]
        case let .resume(runID, _, _, _): ["v1", "runs", runID, "resume"]
        }
    }

    /// Keys are the server's snake_case names; optional fields are omitted
    /// rather than sent as null (`_gpu_mismatch` treats a missing `gpu` as
    /// "no check", and `confirm` rejects a `tryon` that is not reuse/rerun).
    public func body() -> Data {
        var object: [String: Any] = [:]
        switch self {
        case .phaseA:
            break
        case let .regen(_, _, runToken, guidance):
            object["run_token"] = runToken
            object["guidance"] = guidance.map(\.rawValue)
        case let .confirm(_, provider, panelToken, gpu, tryon):
            object["provider"] = provider.rawValue
            object["panel_token"] = panelToken
            if let gpu { object["gpu"] = gpu }
            if let tryon { object["tryon"] = tryon.rawValue }
        case let .resume(_, provider, runToken, gpu):
            object["provider"] = provider.rawValue
            object["run_token"] = runToken
            if let gpu { object["gpu"] = gpu }
        }
        // Strings, arrays of strings and nothing else: serialization cannot fail.
        return (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
    }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ios/MotionKit && swift test --filter SpendIntentTests`
Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add ios/MotionKit/Sources/MotionKit/Money/Guidance.swift ios/MotionKit/Sources/MotionKit/Money/SpendIntent.swift ios/MotionKit/Tests/MotionKitTests/SpendIntentTests.swift
git commit -m "feat(ios): model the four spend calls

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: IdempotencyLedger

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Money/IdempotencyLedger.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/IdempotencyLedgerTests.swift`

**Interfaces:**
- Consumes: `SpendIntent` (Task 1).
- Produces: `SpendLedgerEntry {key: String, intent: SpendIntent, label: String, createdAt: Date}`
  (public memberwise init); `IdempotencyLedger(root: URL)` / `IdempotencyLedger()` with
  `load() throws -> SpendLedgerEntry?`, `save(_:) throws`, `clear() throws`, `fileURL: URL`.

- [ ] **Step 1: Write the failing tests**

```swift
import Foundation
import Testing
@testable import MotionKit

@Suite struct IdempotencyLedgerTests {
    private func ledger() -> IdempotencyLedger {
        IdempotencyLedger(root: FileManager.default.temporaryDirectory
            .appending(component: "ledger-\(UUID().uuidString)"))
    }

    private let entry = SpendLedgerEntry(
        key: "K1", intent: .phaseA, label: "Try-on preview · 1 job",
        createdAt: Date(timeIntervalSince1970: 1_790_000_000))

    @Test func emptyLedgerLoadsNil() throws {
        #expect(try ledger().load() == nil)
    }

    @Test func saveThenLoadRoundTrips() throws {
        let l = ledger()
        try l.save(entry)
        #expect(try l.load() == entry)
    }

    @Test func saveReplacesTheSingleEntry() throws {
        let l = ledger()
        try l.save(entry)
        let second = SpendLedgerEntry(key: "K2", intent: .phaseA, label: "x", createdAt: .now)
        try l.save(second)
        #expect(try l.load()?.key == "K2")
        let files = try FileManager.default.contentsOfDirectory(atPath: l.root.path)
        #expect(files == ["pending-spend.json"])
    }

    @Test func clearRemovesTheEntry() throws {
        let l = ledger()
        try l.save(entry)
        try l.clear()
        #expect(try l.load() == nil)
        try l.clear()   // idempotent
    }

    @Test func corruptJournalThrows() throws {
        let l = ledger()
        try FileManager.default.createDirectory(at: l.root, withIntermediateDirectories: true)
        try Data("{not json".utf8).write(to: l.fileURL)
        #expect(throws: (any Error).self) { try l.load() }
    }
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ios/MotionKit && swift test --filter IdempotencyLedgerTests`
Expected: compile failure — `cannot find 'IdempotencyLedger' in scope`.

- [ ] **Step 3: Implement**

```swift
import Foundation

public struct SpendLedgerEntry: Codable, Sendable, Equatable {
    public let key: String
    public let intent: SpendIntent
    /// What the user tapped, e.g. "Confirm · RTX 5090 · ~$1.40".
    public let label: String
    public let createdAt: Date

    public init(key: String, intent: SpendIntent, label: String, createdAt: Date) {
        self.key = key
        self.intent = intent
        self.label = label
        self.createdAt = createdAt
    }
}

/// The spend request the phone has sent (or is about to send) and has not
/// yet had a definitive answer for. At most one: there is one run slot.
///
/// Written — synced to disk — BEFORE the request leaves, so an app killed
/// mid-request can resend the same key on the next launch instead of
/// minting a new one (parent design §4).
public struct IdempotencyLedger: Sendable {
    public let root: URL

    public init() { self.root = Self.defaultRoot() }
    public init(root: URL) { self.root = root }

    public var fileURL: URL { root.appending(component: "pending-spend.json") }

    /// Throws when the journal exists but cannot be decoded — the caller must
    /// treat that as "an earlier spend may be in flight, key unknown".
    public func load() throws -> SpendLedgerEntry? {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        return try decoder.decode(SpendLedgerEntry.self, from: Data(contentsOf: fileURL))
    }

    public func save(_ entry: SpendLedgerEntry) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .secondsSince1970
        let data = try encoder.encode(entry)
        let temporary = root.appending(component: ".pending-spend.\(UUID().uuidString).tmp")
        guard fm.createFile(atPath: temporary.path, contents: nil) else {
            throw CocoaError(.fileWriteUnknown)
        }
        do {
            let handle = try FileHandle(forWritingTo: temporary)
            defer { try? handle.close() }
            try handle.write(contentsOf: data)
            try handle.synchronize()
        } catch {
            try? fm.removeItem(at: temporary)
            throw error
        }
        if fm.fileExists(atPath: fileURL.path) {
            _ = try fm.replaceItemAt(fileURL, withItemAt: temporary)
        } else {
            try fm.moveItem(at: temporary, to: fileURL)
        }
    }

    public func clear() throws {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return }
        try FileManager.default.removeItem(at: fileURL)
    }

    private static func defaultRoot() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appending(path: "Motion/Spend", directoryHint: .isDirectory)
    }
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ios/MotionKit && swift test --filter IdempotencyLedgerTests`
Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add ios/MotionKit/Sources/MotionKit/Money/IdempotencyLedger.swift ios/MotionKit/Tests/MotionKitTests/IdempotencyLedgerTests.swift
git commit -m "feat(ios): persist the in-flight spend before sending

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: APIClient.spendPost, query GETs, and transport failures in the stub

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/API/APIClient.swift` (add after `delete<Response>`)
- Modify: `ios/MotionKit/Tests/MotionKitTests/StubURLProtocol.swift` (`startLoading`)
- Test: `ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift` (append a suite)

**Interfaces:**
- Produces: `RawSpendResponse` (`.http(status: Int, body: Data)` / `.transport(String)`);
  `APIClient.spendPost(_ components: [String], body: Data, idempotencyKey: String,
  timeout: TimeInterval = 90) async -> RawSpendResponse` (never throws);
  `APIClient.get<T>(_ type: T.Type, query: [URLQueryItem], _ components: String...) async throws(APIError) -> T`.
- Test support: a stub handler returning status `-1` makes the request fail with `URLError(.timedOut)`.

- [ ] **Step 1: Teach the stub to fail**

In `StubURLProtocol.startLoading()`, replace the lines from `let (status, headers, body) = h(captured)`
to the end of the method with:

```swift
        let (status, headers, body) = h(captured)
        // A negative status simulates a dropped connection / timeout: no
        // HTTP response at all, which is what a spend retry must survive.
        if status < 0 {
            client?.urlProtocol(self, didFailWithError: URLError(.timedOut))
            return
        }
        let response = HTTPURLResponse(url: request.url!, statusCode: status,
                                       httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
```

- [ ] **Step 2: Write the failing tests** (append to `APIClientTests.swift`)

```swift
extension URLProtocolTests {
@Suite struct SpendTransportTests {
    @Test func spendPostSendsKeyAuthAndJSON() async throws {
        StubURLProtocol.install { _ in TestSupport.json(#"{"run_id":"tg-1000","outcome":"started"}"#, status: 202) }
        let raw = await TestSupport.client().spendPost(
            ["v1", "runs", "phase-a"], body: Data("{}".utf8), idempotencyKey: "KEY-1")
        #expect(raw == .http(status: 202, body: Data(#"{"run_id":"tg-1000","outcome":"started"}"#.utf8)))
        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.httpMethod == "POST")
        #expect(request.url?.path == "/v1/runs/phase-a")
        #expect(request.value(forHTTPHeaderField: "Idempotency-Key") == "KEY-1")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
        #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Id") == "id-123")
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
        #expect(request.timeoutInterval == 90)
    }

    @Test func spendPostReportsTransportFailureWithoutThrowing() async {
        StubURLProtocol.install { _ in (-1, [:], Data()) }
        let raw = await TestSupport.client().spendPost(
            ["v1", "runs", "phase-a"], body: Data("{}".utf8), idempotencyKey: "K")
        guard case .transport = raw else {
            Issue.record("expected .transport, got \(raw)")
            return
        }
    }

    @Test func spendPostReturnsErrorStatusesRaw() async {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.errorConflict, status: 409) }
        let raw = await TestSupport.client().spendPost(
            ["v1", "runs", "x", "confirm"], body: Data("{}".utf8), idempotencyKey: "K")
        #expect(raw == .http(status: 409, body: Data(Fixtures.errorConflict.utf8)))
    }

    @Test func getWithQueryAppendsItems() async throws {
        StubURLProtocol.install { _ in TestSupport.json(#"{"ok":true}"#) }
        struct OK: Decodable, Sendable { let ok: Bool }
        _ = try await TestSupport.client().get(
            OK.self, query: [URLQueryItem(name: "force", value: "1")], "v1", "runs", "tg-1", "rent-panel")
        let url = try #require(StubURLProtocol.requests.first?.url)
        #expect(url.path == "/v1/runs/tg-1/rent-panel")
        #expect(url.query == "force=1")
    }
}
}
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ios/MotionKit && swift test --filter SpendTransportTests`
Expected: compile failure — `value of type 'APIClient' has no member 'spendPost'`.

- [ ] **Step 4: Implement** — in `APIClient.swift`, add at file scope above `public actor APIClient`:

```swift
/// A spend call's answer before any interpretation. `SpendGate` decides what
/// is definitive and what is worth resending with the same key.
public enum RawSpendResponse: Sendable, Equatable {
    case http(status: Int, body: Data)
    case transport(String)
}
```

and inside the actor, after `delete<Response>`:

```swift
    public func get<T: Decodable & Sendable>(
        _ type: T.Type, query: [URLQueryItem], _ components: String...
    ) async throws(APIError) -> T {
        let target = url(components).appending(queryItems: query)
        let (data, _) = try await send(target, extraHeaders: [:], okStatuses: [200])
        return try decode(type, data)
    }

    /// The transport for `SpendGate` only. Never throws: a dropped connection
    /// is an answer the gate must classify (resend with the same key), not an
    /// error to surface. 90 s stays under Cloudflare's ~100 s origin ceiling.
    public func spendPost(_ components: [String], body: Data, idempotencyKey: String,
                          timeout: TimeInterval = 90) async -> RawSpendResponse {
        var request = URLRequest(url: url(components))
        request.httpMethod = "POST"
        request.httpBody = body
        request.timeoutInterval = timeout
        for (k, v) in authHeaders { request.setValue(v, forHTTPHeaderField: k) }
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key")
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else { return .transport("not an HTTP response") }
            return .http(status: http.statusCode, body: data)
        } catch {
            return .transport(error.localizedDescription)
        }
    }
```

- [ ] **Step 5: Run the whole suite to verify pass and no regressions**

Run: `cd ios/MotionKit && swift test`
Expected: all tests pass (83 existing + 11 from Tasks 1–2 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add ios/MotionKit/Sources/MotionKit/API/APIClient.swift ios/MotionKit/Tests/MotionKitTests/StubURLProtocol.swift ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift
git commit -m "feat(ios): add a non-throwing spend transport

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: SpendGate

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Money/SpendResult.swift`
- Create: `ios/MotionKit/Sources/MotionKit/Money/SpendGate.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/SpendGateTests.swift`

**Interfaces:**
- Consumes: `SpendIntent`, `IdempotencyLedger`, `SpendLedgerEntry`, `APIClient.spendPost`, `RawSpendResponse`.
- Produces:
  - `SpendResult`: `.accepted(runID: String?, outcome: String)`,
    `.refused(status: Int, code: String, message: String, panelToken: String?)`, `.outcomeUnknown`,
    `.busy(attempts: Int)`, `.unreachable(detail: String)`, `.expired(label: String, createdAt: Date?)`,
    `.notSent(reason: String)`.
  - `SpendRetryReason`: `.botBusy`, `.unreachable`.
  - `SpendRetryHandler = @Sendable (Int, SpendRetryReason) -> Void`.
  - `protocol SpendSending: Sendable` with `perform(_:label:onRetry:) async -> SpendResult`,
    `recheck(onRetry:) async -> SpendResult`, `replayPending() async -> SpendResult?`,
    `pending() async -> SpendLedgerEntry?`.
  - `SpendAccepted {runId: String, outcome: String}` (Decodable).
  - `actor SpendGate: SpendSending`, `init(client:ledger:sleep:now:makeKey:)`,
    `static let replayWindow: TimeInterval = 72_000`.

- [ ] **Step 1: Write the failing tests**

```swift
import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite struct SpendGateTests {
    /// Scripted answers, one per request, in order.
    final class Script: @unchecked Sendable {
        private let lock = NSLock()
        private var answers: [(Int, [String: String], Data)]
        init(_ answers: [(Int, [String: String], Data)]) { self.answers = answers }
        func next() -> (Int, [String: String], Data) {
            lock.withLock { answers.isEmpty ? (500, [:], Data()) : answers.removeFirst() }
        }
    }

    final class Box<T: Sendable>: @unchecked Sendable {
        private let lock = NSLock()
        private var stored: T
        init(_ v: T) { stored = v }
        var value: T { lock.withLock { stored } }
        func mutate(_ f: (inout T) -> Void) { lock.withLock { f(&stored) } }
    }

    static let accepted = TestSupport.json(#"{"run_id":"tg-1000","outcome":"started"}"#, status: 202)
    static let busy = TestSupport.json(#"{"error":{"code":"bot_busy","message":"the bot is busy"}}"#, status: 503)
    static let unknown = TestSupport.json(#"{"error":{"code":"outcome_unknown","message":"did not finish"}}"#, status: 409)
    static let stale = TestSupport.json(#"{"error":{"code":"stale_panel","message":"the job changed since the panel was read — read it again"}}"#, status: 409)
    static let choice = TestSupport.json(#"{"error":{"code":"choice_required","message":"try-on already ran"},"panel_token":"55.6"}"#, status: 409)
    static let cloudflare524: (Int, [String: String], Data) = (524, ["Content-Type": "text/html"], Data("<html>timeout</html>".utf8))
    static let dropped: (Int, [String: String], Data) = (-1, [:], Data())

    struct Harness {
        let gate: SpendGate
        let ledger: IdempotencyLedger
        let sleeps: Box<[Duration]>
        let minted: Box<Int>
    }

    static func harness(now: Date = Date(timeIntervalSince1970: 1_790_000_000)) -> Harness {
        let ledger = IdempotencyLedger(root: FileManager.default.temporaryDirectory
            .appending(component: "gate-\(UUID().uuidString)"))
        let sleeps = Box<[Duration]>([])
        let minted = Box(0)
        let gate = SpendGate(
            client: TestSupport.client(), ledger: ledger,
            sleep: { d in sleeps.mutate { $0.append(d) } },
            now: { now },
            makeKey: { minted.mutate { $0 += 1 }; return "KEY-\(minted.value)" })
        return Harness(gate: gate, ledger: ledger, sleeps: sleeps, minted: minted)
    }

    static var keys: [String?] {
        StubURLProtocol.requests.map { $0.value(forHTTPHeaderField: "Idempotency-Key") }
    }

    @Test func ledgerIsOnDiskBeforeTheRequestArrives() async throws {
        let h = Self.harness()
        let sawEntry = Box(false)
        let file = h.ledger.fileURL
        StubURLProtocol.install { _ in
            sawEntry.mutate { $0 = FileManager.default.fileExists(atPath: file.path) }
            return Self.accepted
        }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(sawEntry.value)
        #expect(result == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(try h.ledger.load() == nil)
    }

    @Test func droppedConnectionIsResentWithTheSameKey() async throws {
        let h = Self.harness()
        let script = Script([Self.dropped, Self.accepted])
        StubURLProtocol.install { _ in script.next() }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(Self.keys == ["KEY-1", "KEY-1"])
        #expect(h.sleeps.value == [.seconds(2)])
        #expect(h.minted.value == 1)
    }

    @Test func unreachableKeepsTheLedgerAndRecheckReusesTheKey() async throws {
        let h = Self.harness()
        let script = Script([Self.dropped, Self.cloudflare524, Self.dropped, Self.accepted])
        StubURLProtocol.install { _ in script.next() }
        let first = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        guard case .unreachable = first else { Issue.record("got \(first)"); return }
        #expect(try h.ledger.load()?.key == "KEY-1")
        #expect(h.sleeps.value == [.seconds(2), .seconds(5)])
        let second = await h.gate.recheck { _, _ in }
        #expect(second == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(Self.keys == ["KEY-1", "KEY-1", "KEY-1", "KEY-1"])
        #expect(h.minted.value == 1)
        #expect(try h.ledger.load() == nil)
    }

    @Test func botBusyRetriesThreeTimesThenGivesUpAndClears() async throws {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.busy }
        let retries = Box<[Int]>([])
        let result = await h.gate.perform(.phaseA, label: "Preview") { n, reason in
            #expect(reason == .botBusy)
            retries.mutate { $0.append(n) }
        }
        #expect(result == .busy(attempts: 4))
        #expect(Self.keys == ["KEY-1", "KEY-1", "KEY-1", "KEY-1"])
        #expect(h.sleeps.value == [.seconds(5), .seconds(5), .seconds(5)])
        #expect(retries.value == [1, 2, 3])
        #expect(try h.ledger.load() == nil)
    }

    @Test func botBusyThenAcceptedKeepsTheKey() async {
        let h = Self.harness()
        let script = Script([Self.busy, Self.accepted])
        StubURLProtocol.install { _ in script.next() }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(Self.keys == ["KEY-1", "KEY-1"])
    }

    @Test func outcomeUnknownIsNeverRetried() async throws {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.unknown }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        #expect(result == .outcomeUnknown)
        #expect(StubURLProtocol.requests.count == 1)
        #expect(try h.ledger.load() == nil)
    }

    @Test func stalePanelIsDefinitiveAndSentOnce() async {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.stale }
        let intent = SpendIntent.confirm(runID: "tg-1000", provider: .runpod, panelToken: "old",
                                         gpu: nil, tryon: nil)
        let result = await h.gate.perform(intent, label: "Confirm") { _, _ in }
        #expect(result == .refused(status: 409, code: "stale_panel",
                                   message: "the job changed since the panel was read — read it again",
                                   panelToken: nil))
        #expect(StubURLProtocol.requests.count == 1)
    }

    @Test func choiceRequiredCarriesTheFreshPanelToken() async {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.choice }
        let intent = SpendIntent.confirm(runID: "tg-1000", provider: .runpod, panelToken: "old",
                                         gpu: nil, tryon: nil)
        let result = await h.gate.perform(intent, label: "Confirm") { _, _ in }
        #expect(result == .refused(status: 409, code: "choice_required",
                                   message: "try-on already ran", panelToken: "55.6"))
    }

    @Test func cloudflareHTML403IsDefinitiveAccessDenied() async throws {
        let h = Self.harness()
        StubURLProtocol.install { _ in (403, ["Content-Type": "text/html"], Data(Fixtures.cloudflareHTML.utf8)) }
        let result = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        guard case .refused(403, "access_denied", _, nil) = result else { Issue.record("got \(result)"); return }
        #expect(try h.ledger.load() == nil)
    }

    @Test func performRefusesWhileAnEntryIsPending() async throws {
        let h = Self.harness()
        try h.ledger.save(SpendLedgerEntry(key: "OLD", intent: .phaseA, label: "Preview",
                                           createdAt: Date(timeIntervalSince1970: 1_790_000_000 - 60)))
        StubURLProtocol.install { _ in Self.accepted }
        let result = await h.gate.perform(.phaseA, label: "Preview again") { _, _ in }
        guard case .notSent = result else { Issue.record("got \(result)"); return }
        #expect(StubURLProtocol.requests.isEmpty)
        #expect(h.minted.value == 0)
    }

    @Test func replayResendsAYoungEntryOnceWithItsKey() async throws {
        let h = Self.harness()
        try h.ledger.save(SpendLedgerEntry(key: "OLD", intent: .phaseA, label: "Preview",
                                           createdAt: Date(timeIntervalSince1970: 1_790_000_000 - 3600)))
        StubURLProtocol.install { _ in Self.dropped }
        let result = await h.gate.replayPending()
        guard case .unreachable = result else { Issue.record("got \(String(describing: result))"); return }
        #expect(Self.keys == ["OLD"])          // a single attempt, no retry loop
        #expect(try h.ledger.load()?.key == "OLD")
        #expect(h.minted.value == 0)
    }

    @Test func replayNeverResendsAnEntryTwentyHoursOld() async throws {
        let h = Self.harness()
        let created = Date(timeIntervalSince1970: 1_790_000_000 - 20 * 3600)
        try h.ledger.save(SpendLedgerEntry(key: "OLD", intent: .phaseA, label: "Confirm", createdAt: created))
        StubURLProtocol.install { _ in Self.accepted }
        let result = await h.gate.replayPending()
        #expect(result == .expired(label: "Confirm", createdAt: created))
        #expect(StubURLProtocol.requests.isEmpty)
        #expect(try h.ledger.load() == nil)
    }

    @Test func replayWithNothingPendingIsNil() async {
        let h = Self.harness()
        StubURLProtocol.install { _ in Self.accepted }
        #expect(await h.gate.replayPending() == nil)
        #expect(StubURLProtocol.requests.isEmpty)
    }

    @Test func corruptLedgerIsReportedNotResent() async throws {
        let h = Self.harness()
        try FileManager.default.createDirectory(at: h.ledger.root, withIntermediateDirectories: true)
        try Data("{".utf8).write(to: h.ledger.fileURL)
        StubURLProtocol.install { _ in Self.accepted }
        let result = await h.gate.replayPending()
        #expect(result == .expired(label: "An earlier spend", createdAt: nil))
        #expect(StubURLProtocol.requests.isEmpty)
    }

    @Test func concurrentPerformIsRefusedWhileOneIsSending() async throws {
        let h = Self.harness()
        let release = DispatchSemaphore(value: 0)
        StubURLProtocol.install { _ in release.wait(); return Self.accepted }
        let first = Task { await h.gate.perform(.phaseA, label: "Preview") { _, _ in } }
        while StubURLProtocol.requests.isEmpty { try await Task.sleep(for: .milliseconds(10)) }
        let second = await h.gate.perform(.phaseA, label: "Preview") { _, _ in }
        guard case .notSent = second else { Issue.record("got \(second)"); return }
        release.signal()
        #expect(await first.value == .accepted(runID: "tg-1000", outcome: "started"))
        #expect(StubURLProtocol.requests.count == 1)
    }
}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ios/MotionKit && swift test --filter SpendGateTests`
Expected: compile failure — `cannot find 'SpendGate' in scope`.

- [ ] **Step 3: Implement `SpendResult.swift`**

```swift
import Foundation

public enum SpendResult: Sendable, Equatable {
    /// 2xx. `outcome` is the server's (`started`, `queued`, …).
    case accepted(runID: String?, outcome: String)
    /// A definitive refusal; nothing was spent. `panelToken` is set only by
    /// `409 choice_required`.
    case refused(status: Int, code: String, message: String, panelToken: String?)
    /// `409 outcome_unknown`: an earlier attempt with this key died midway.
    case outcomeUnknown
    /// `503 bot_busy` on every attempt. The server recorded nothing.
    case busy(attempts: Int)
    /// No definitive answer; the ledger still holds the key for `recheck()`.
    case unreachable(detail: String)
    /// A pending entry was too old (or unreadable) to resend safely.
    case expired(label: String, createdAt: Date?)
    /// Refused on the phone before anything was sent.
    case notSent(reason: String)
}

public enum SpendRetryReason: Sendable, Equatable {
    case botBusy, unreachable
}

public typealias SpendRetryHandler = @Sendable (Int, SpendRetryReason) -> Void

/// What `RunFlow` needs from the gate — a protocol so store tests and the UI
/// test build can substitute a fake that never reaches the VPS.
public protocol SpendSending: Sendable {
    func perform(_ intent: SpendIntent, label: String,
                 onRetry: @escaping SpendRetryHandler) async -> SpendResult
    func recheck(onRetry: @escaping SpendRetryHandler) async -> SpendResult
    func replayPending() async -> SpendResult?
    func pending() async -> SpendLedgerEntry?
}

/// `202 {run_id, outcome}` from phase-a, regen, confirm and resume.
public struct SpendAccepted: Decodable, Sendable, Equatable {
    public let runId: String
    public let outcome: String
}
```

- [ ] **Step 4: Implement `SpendGate.swift`**

```swift
import Foundation

/// The only sender of `phase-a`, `regen`, `confirm` and `resume`.
///
/// Every rule that keeps one tap from becoming two spends lives here
/// (parent design §4, API spec §5.5/§5.8):
/// - the key is minted in `perform` and nowhere else;
/// - the ledger entry is on disk before the request leaves;
/// - a transport failure or a non-JSON 5xx (the origin may still be working)
///   is resent with the same key; the entry stays until a definitive answer;
/// - `503 bot_busy` means the server recorded nothing: same key, 5 s apart,
///   at most 3 retries;
/// - `409 outcome_unknown` is final — resending would only repeat it.
public actor SpendGate: SpendSending {
    /// The server prunes idempotency records after 24 h, after which a replay
    /// would be a FRESH spend. 20 h leaves margin for clock skew.
    public static let replayWindow: TimeInterval = 20 * 3600
    static let busyDelay: Duration = .seconds(5)
    static let busyRetries = 3
    static let unreachableDelays: [Duration] = [.seconds(2), .seconds(5)]

    private let client: APIClient
    private let ledger: IdempotencyLedger
    private let sleep: @Sendable (Duration) async throws -> Void
    private let now: @Sendable () -> Date
    private let makeKey: @Sendable () -> String
    private var sending = false

    public init(client: APIClient, ledger: IdempotencyLedger = IdempotencyLedger(),
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
                now: @escaping @Sendable () -> Date = { Date() },
                makeKey: @escaping @Sendable () -> String = { UUID().uuidString }) {
        self.client = client
        self.ledger = ledger
        self.sleep = sleep
        self.now = now
        self.makeKey = makeKey
    }

    public func pending() -> SpendLedgerEntry? {
        try? ledger.load()
    }

    public func perform(_ intent: SpendIntent, label: String,
                        onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        guard !sending else { return .notSent(reason: "Another spend request is still in flight.") }
        sending = true
        defer { sending = false }
        switch loadPending() {
        case .failure(let problem):
            return problem.result
        case .success(let existing?):
            return .notSent(reason: "“\(existing.label)” hasn't been answered yet. Check it before spending again.")
        case .success(nil):
            break
        }
        let entry = SpendLedgerEntry(key: makeKey(), intent: intent, label: label, createdAt: now())
        do {
            try ledger.save(entry)
        } catch {
            return .notSent(reason: "Couldn't record the request on this phone, so it wasn't sent (\(error.localizedDescription)).")
        }
        return await transmit(entry, retrying: true, onRetry: onRetry)
    }

    public func recheck(onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        guard !sending else { return .notSent(reason: "Another spend request is still in flight.") }
        sending = true
        defer { sending = false }
        switch loadPending() {
        case .failure(let problem):
            return problem.result
        case .success(nil):
            return .notSent(reason: "Nothing is waiting to be checked.")
        case .success(let entry?):
            return await transmit(entry, retrying: true, onRetry: onRetry)
        }
    }

    /// Once per launch. A single attempt: the user is not looking at the
    /// button yet, so no retry loop runs behind their back.
    public func replayPending() async -> SpendResult? {
        guard !sending else { return nil }
        sending = true
        defer { sending = false }
        switch loadPending() {
        case .failure(let problem):
            return problem.result
        case .success(nil):
            return nil
        case .success(let entry?):
            return await transmit(entry, retrying: false, onRetry: { _, _ in })
        }
    }

    /// `.failure` carries the result to return instead of sending: an
    /// unreadable journal or an entry too old to resend. Both clear the ledger.
    private func loadPending() -> Result<SpendLedgerEntry?, PendingProblem> {
        let entry: SpendLedgerEntry?
        do {
            entry = try ledger.load()
        } catch {
            try? ledger.clear()
            return .failure(PendingProblem(.expired(label: "An earlier spend", createdAt: nil)))
        }
        if let entry, now().timeIntervalSince(entry.createdAt) >= Self.replayWindow {
            try? ledger.clear()
            return .failure(PendingProblem(.expired(label: entry.label, createdAt: entry.createdAt)))
        }
        return .success(entry)
    }

    private func transmit(_ entry: SpendLedgerEntry, retrying: Bool,
                          onRetry: SpendRetryHandler) async -> SpendResult {
        var busyRetries = 0
        var unreachableRetries = 0
        while true {
            let raw = await client.spendPost(entry.intent.path, body: entry.intent.body(),
                                             idempotencyKey: entry.key)
            switch Self.classify(raw, intent: entry.intent) {
            case .definitive(let result):
                try? ledger.clear()
                return result
            case .busy:
                guard retrying, busyRetries < Self.busyRetries else {
                    try? ledger.clear()   // nothing was recorded server-side
                    return .busy(attempts: busyRetries + 1)
                }
                busyRetries += 1
                onRetry(busyRetries, .botBusy)
                do { try await sleep(Self.busyDelay) } catch {
                    try? ledger.clear()
                    return .busy(attempts: busyRetries)
                }
            case .ambiguous(let detail):
                guard retrying, unreachableRetries < Self.unreachableDelays.count else {
                    return .unreachable(detail: detail)
                }
                let delay = Self.unreachableDelays[unreachableRetries]
                unreachableRetries += 1
                onRetry(unreachableRetries, .unreachable)
                do { try await sleep(delay) } catch { return .unreachable(detail: detail) }
            }
        }
    }

    enum Classified: Equatable {
        case definitive(SpendResult)
        case busy
        case ambiguous(String)
    }

    struct Envelope: Decodable {
        struct Inner: Decodable { let code: String; let message: String }
        let error: Inner
        let panelToken: String?
    }

    static func classify(_ raw: RawSpendResponse, intent: SpendIntent) -> Classified {
        switch raw {
        case .transport(let detail):
            return .ambiguous(detail)
        case let .http(status, body):
            if (200..<300).contains(status) {
                let accepted = try? MotionJSON.decoder.decode(SpendAccepted.self, from: body)
                return .definitive(.accepted(runID: accepted?.runId ?? intent.runID,
                                             outcome: accepted?.outcome ?? "accepted"))
            }
            if let envelope = try? MotionJSON.decoder.decode(Envelope.self, from: body) {
                if status == 503 && envelope.error.code == "bot_busy" { return .busy }
                if envelope.error.code == "outcome_unknown" { return .definitive(.outcomeUnknown) }
                return .definitive(.refused(status: status, code: envelope.error.code,
                                            message: envelope.error.message,
                                            panelToken: envelope.panelToken))
            }
            // No API envelope: Cloudflare (or another proxy) answered. A 5xx
            // there (502/504/524) may mean the origin is still working, so it
            // is not definitive. A 403 is Access refusing before the origin.
            if status >= 500 { return .ambiguous("HTTP \(status) without an API answer") }
            if status == 403 {
                return .definitive(.refused(status: 403, code: "access_denied",
                                            message: APIError.accessDenied(status: 403).userMessage,
                                            panelToken: nil))
            }
            return .definitive(.refused(status: status, code: "http_\(status)",
                                        message: String(decoding: body.prefix(200), as: UTF8.self),
                                        panelToken: nil))
        }
    }
}

struct PendingProblem: Error {
    let result: SpendResult
    init(_ result: SpendResult) { self.result = result }
}
```


- [ ] **Step 5: Run to verify pass**

Run: `cd ios/MotionKit && swift test --filter SpendGateTests`
Expected: 15 tests pass. Then `swift test` — everything passes.

- [ ] **Step 6: Commit**

```bash
git add ios/MotionKit/Sources/MotionKit/Money/SpendResult.swift ios/MotionKit/Sources/MotionKit/Money/SpendGate.swift ios/MotionKit/Tests/MotionKitTests/SpendGateTests.swift
git commit -m "feat(ios): send spends through one idempotent gate

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Run-flow models, fixtures and the quote

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Models/RunFlow.swift`
- Modify: `ios/MotionKit/Sources/MotionKit/Formatting.swift` (`CostEstimate`)
- Modify: `ios/MotionKit/Tests/MotionKitTests/Fixtures.swift` (append before `static func data`)
- Test: `ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift` (append a suite)

**Interfaces:**
- Produces: `TryonPreview {index, run, status: StageStatus, hasImage}` (Identifiable by `index`),
  `TryonPreviews {runId, runToken, phaseARunning, previews}`,
  `RentPanelRunpod {gpu, datacenter?, stock?, usdPerHr?, soldOut}`,
  `RentPanelVast {enabled, usdPerHr?, sessionUsd?, blockers, canSpend}`,
  `RentPanel {runId, panelToken, afterPhaseA, jobs, estimateMin: Double, runpod, vast}`,
  `TryonKeepRequest(runId:index:)` (Encodable), `TryonLibraryRecord {id, provider, savedAt}`,
  `CostEstimate.quote(estimateMin: Double, usdPerHr: Double?) -> Double?`.
- Fixtures: `tryonIdle`, `tryonRunning`, `tryonDone`, `rentPanel`, `rentPanelFresh`, `rentPanelSoldOut`,
  `keepRecord`.

- [ ] **Step 1: Add fixtures** (shaped on `AppRuns.tryon` / `rent_panel` / `TryonLibrary.save`; made-up ids)

```swift
    static let tryonIdle = #"{"run_id":"tg-1000","run_token":"1790000000123.4","phase_a_running":false,"previews":[]}"#

    static let tryonRunning = #"""
    {"run_id":"tg-1000","run_token":"1790000000123.4","phase_a_running":true,
     "previews":[{"index":"0","run":"model__dress","status":"running","has_image":false},
                 {"index":"1","run":"model__blazer","status":"pending","has_image":false}]}
    """#

    static let tryonDone = #"""
    {"run_id":"tg-1000","run_token":"1790000000123.4","phase_a_running":false,
     "previews":[{"index":"0","run":"model__dress","status":"done","has_image":true},
                 {"index":"1","run":"model__blazer","status":"error","has_image":false}]}
    """#

    static let rentPanel = #"""
    {"runpod":{"gpu":"NVIDIA GeForce RTX 5090","datacenter":"EU-RO-1","stock":"High",
               "usd_per_hr":0.99,"sold_out":false},
     "vast":{"enabled":true,"usd_per_hr":0.62,"session_usd":1.05,"blockers":[],"can_spend":true},
     "run_id":"tg-1000","panel_token":"1790000000123.4.9","after_phase_a":true,
     "jobs":2,"estimate_min":84}
    """#

    static let rentPanelFresh = #"""
    {"runpod":{"gpu":"NVIDIA GeForce RTX 5090","datacenter":"EU-RO-1","stock":"Medium",
               "usd_per_hr":0.99,"sold_out":false},
     "vast":{"enabled":true,"usd_per_hr":0.62,"session_usd":1.05,"blockers":[],"can_spend":true},
     "run_id":"tg-1000","panel_token":"1790000000999.1.10","after_phase_a":false,
     "jobs":2,"estimate_min":84}
    """#

    static let rentPanelSoldOut = #"""
    {"runpod":{"gpu":"NVIDIA GeForce RTX 5090","datacenter":"EU-RO-1","stock":null,
               "usd_per_hr":null,"sold_out":true},
     "vast":{"enabled":false,"usd_per_hr":null,"session_usd":null,
             "blockers":["Vast is disabled — set VAST_ENABLED=1"],"can_spend":false},
     "run_id":"tg-1000","panel_token":"1790000000123.4.9","after_phase_a":false,
     "jobs":1,"estimate_min":42}
    """#

    static let keepRecord = #"""
    {"id":"a1b2c3","owner":"app","material_ids":{"character":"app/model.png","outfit":"app/dress.png"},
     "provider":"gemini","saved_at":1790000300.5}
    """#
```

- [ ] **Step 2: Write the failing tests** (append to `ModelsTests.swift`)

```swift
@Suite struct RunFlowModelTests {
    @Test func tryonPreviewsDecode() throws {
        let t = try MotionJSON.decoder.decode(TryonPreviews.self, from: Fixtures.data(Fixtures.tryonRunning))
        #expect(t.runId == "tg-1000" && t.runToken == "1790000000123.4" && t.phaseARunning)
        #expect(t.previews.map(\.id) == ["0", "1"])
        #expect(t.previews[0].status == .running && !t.previews[0].hasImage)
    }

    @Test func rentPanelDecodes() throws {
        let p = try MotionJSON.decoder.decode(RentPanel.self, from: Fixtures.data(Fixtures.rentPanel))
        #expect(p.panelToken == "1790000000123.4.9" && p.afterPhaseA && p.jobs == 2 && p.estimateMin == 84)
        #expect(p.runpod.usdPerHr == 0.99 && !p.runpod.soldOut && p.runpod.datacenter == "EU-RO-1")
        #expect(p.vast.canSpend && p.vast.sessionUsd == 1.05)
    }

    @Test func soldOutPanelDecodesNulls() throws {
        let p = try MotionJSON.decoder.decode(RentPanel.self, from: Fixtures.data(Fixtures.rentPanelSoldOut))
        #expect(p.runpod.soldOut && p.runpod.usdPerHr == nil && p.runpod.stock == nil)
        #expect(!p.vast.canSpend && p.vast.blockers.count == 1)
    }

    @Test func keepRecordDecodes() throws {
        let r = try MotionJSON.decoder.decode(TryonLibraryRecord.self, from: Fixtures.data(Fixtures.keepRecord))
        #expect(r.id == "a1b2c3" && r.provider == "gemini")
    }

    @Test func quoteIsMinutesTimesRate() {
        #expect(CostEstimate.quote(estimateMin: 84, usdPerHr: 0.99) == 84.0 / 60 * 0.99)
        #expect(CostEstimate.quote(estimateMin: 84, usdPerHr: nil) == nil)
        #expect(CostEstimate.quote(estimateMin: 0, usdPerHr: 0.99) == 0)
    }
}
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ios/MotionKit && swift test --filter RunFlowModelTests`
Expected: compile failure — `cannot find type 'TryonPreviews'`.

- [ ] **Step 4: Implement `Models/RunFlow.swift`**

```swift
import Foundation

/// `GET /v1/runs/{id}/tryon` (`AppRuns.tryon`). `index` is the run's position
/// in the manifest, as a string — the index `regen` and the image routes take.
public struct TryonPreview: Decodable, Sendable, Equatable, Identifiable {
    public let index: String
    public let run: String
    public let status: StageStatus
    public let hasImage: Bool
    public var id: String { index }
}

public struct TryonPreviews: Decodable, Sendable, Equatable {
    public let runId: String
    /// Required by `regen` and `resume`. Changes when the manifest is rewritten.
    public let runToken: String
    public let phaseARunning: Bool
    public let previews: [TryonPreview]
}

/// `GET /v1/runs/{id}/rent-panel` (`AppRuns.rent_panel` + `_rent_panel_data`).
public struct RentPanelRunpod: Decodable, Sendable, Equatable {
    public let gpu: String
    public let datacenter: String?
    public let stock: String?
    public let usdPerHr: Double?
    public let soldOut: Bool
}

public struct RentPanelVast: Decodable, Sendable, Equatable {
    public let enabled: Bool
    public let usdPerHr: Double?
    public let sessionUsd: Double?
    public let blockers: [String]
    public let canSpend: Bool
}

public struct RentPanel: Decodable, Sendable, Equatable {
    public let runId: String
    /// Must accompany `confirm`; every read replaces it.
    public let panelToken: String
    public let afterPhaseA: Bool
    public let jobs: Int
    public let estimateMin: Double
    public let runpod: RentPanelRunpod
    public let vast: RentPanelVast
}

/// `POST /v1/tryon-library` — a free file copy, not a spend.
public struct TryonKeepRequest: Encodable, Sendable {
    public let runId: String
    public let index: String
    public init(runId: String, index: String) {
        self.runId = runId
        self.index = index
    }
}

public struct TryonLibraryRecord: Decodable, Sendable, Equatable {
    public let id: String
    public let provider: String
    public let savedAt: Double
}
```

In `Formatting.swift`, inside `enum CostEstimate`, add:

```swift
    /// The rent panel's price: estimated minutes × the row's rate. A quote
    /// shown before the tap; nil when the row has no rate, and then no spend
    /// button is drawn for it.
    public static func quote(estimateMin: Double, usdPerHr: Double?) -> Double? {
        guard let rate = usdPerHr else { return nil }
        return max(0, estimateMin) / 60 * rate
    }
```

- [ ] **Step 5: Run to verify pass**

Run: `cd ios/MotionKit && swift test --filter RunFlowModelTests` → 5 pass; then `swift test` → all pass.

- [ ] **Step 6: Commit**

```bash
git add ios/MotionKit/Sources/MotionKit/Models/RunFlow.swift ios/MotionKit/Sources/MotionKit/Formatting.swift ios/MotionKit/Tests/MotionKitTests/Fixtures.swift ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift
git commit -m "feat(ios): decode try-on previews and the rent panel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: RunFlow — loading, phases, Phase A, previews, regenerate, keep

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Stores/RunFlow.swift`
- Create: `ios/MotionKit/Tests/MotionKitTests/FakeSpendGate.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift`

**Interfaces:**
- Consumes: `SpendSending`, `SpendIntent`, `SpendResult`, `Guidance`, models from Task 5,
  `PodStatus`, `Draft`, `PipelineCatalogResponse`, `APIClient.get/data/post`.
- Produces (`@MainActor @Observable public final class RunFlow`):
  - `init(client: APIClient, gate: any SpendSending, sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) })`
  - `enum Phase: Equatable { loading, compose, phaseARunning, previews, rentPanel, choiceRequired(panelToken: String, provider: SpendProvider), started(runID: String), outcomeUnknown }`
  - `enum Entry { newJob, existing }`
  - State: `phase`, `pod`, `tryon`, `draft`, `catalog`, `panel`, `error: APIError?`, `message: String?`,
    `inFlightLabel: String?`, `retryNote: String?`, `needsRecheck: Bool`, `imageGeneration: Int`,
    `versions: [String: [Data]]`, `isLoadingPanel: Bool`, `selectedProvider: SpendProvider`,
    `podRequested: Bool`.
  - Derived: `runID: String?`, `hasLocalTryon: Bool`, `isSpending: Bool`, `canRetryRental: Bool`,
    `needsTryonPolling: Bool`, `isKept(_ index: String) -> Bool`.
  - Methods (this task): `start(_:) async`, `refreshPod() async`, `refreshTryon() async`, `pollTryon(interval:) async`,
    `startPhaseA() async`, `regenerate(index:guidance:) async`, `keep(index:) async`,
    `image(index:) async -> Data?`, `loadVersions(index:) async`, `dismissMessage()`,
    `acknowledgePodRequest()`.
  - Task 7 adds: `continueToRent() async`, `loadPanel(force:) async`, `quote(for:) -> Double?`,
    `canConfirm(_:) -> Bool`, `confirm() async`, `choose(_:) async`, `retryRental() async`,
    `recheck() async`, `replayPendingOnce() async`.

- [ ] **Step 1: Write the fake gate**

```swift
import Foundation
@testable import MotionKit

/// Records every intent and answers from a script. Never touches the network.
actor FakeSpendGate: SpendSending {
    private(set) var intents: [SpendIntent] = []
    private(set) var labels: [String] = []
    private(set) var rechecks = 0
    private(set) var replays = 0
    private var results: [SpendResult]
    private var pendingEntry: SpendLedgerEntry?
    private var replayResult: SpendResult?

    init(_ results: [SpendResult] = [], pending: SpendLedgerEntry? = nil, replay: SpendResult? = nil) {
        self.results = results
        self.pendingEntry = pending
        self.replayResult = replay
    }

    func perform(_ intent: SpendIntent, label: String, onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        intents.append(intent)
        labels.append(label)
        return results.isEmpty ? .notSent(reason: "script empty") : results.removeFirst()
    }

    func recheck(onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        rechecks += 1
        return results.isEmpty ? .notSent(reason: "script empty") : results.removeFirst()
    }

    func replayPending() async -> SpendResult? {
        replays += 1
        pendingEntry = nil
        return replayResult
    }

    func pending() async -> SpendLedgerEntry? { pendingEntry }
}
```

- [ ] **Step 2: Write the failing tests**

```swift
import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct RunFlowTests {
    /// Routes reads by path. `tryon` and `panel` can be swapped mid-test.
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _pod = Fixtures.podIdle
        private var _tryon = Fixtures.tryonIdle
        private var _panels = [Fixtures.rentPanel]
        private var _draft = Fixtures.draft
        var pod: String { get { lock.withLock { _pod } } set { lock.withLock { _pod = newValue } } }
        var tryon: String { get { lock.withLock { _tryon } } set { lock.withLock { _tryon = newValue } } }
        var draft: String { get { lock.withLock { _draft } } set { lock.withLock { _draft = newValue } } }
        func setPanels(_ p: [String]) { lock.withLock { _panels = p } }
        func nextPanel() -> String { lock.withLock { _panels.count > 1 ? _panels.removeFirst() : _panels[0] } }

        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            let path = request.url?.path ?? ""
            switch true {
            case path == "/v1/pod": return TestSupport.json(pod)
            case path == "/v1/pipelines": return TestSupport.json(Fixtures.pipelines)
            case path == "/v1/draft": return TestSupport.json(draft)
            case path.hasSuffix("/rent-panel"): return TestSupport.json(nextPanel())
            case path.hasSuffix("/tryon"): return TestSupport.json(tryon)
            case path == "/v1/tryon-library": return TestSupport.json(Fixtures.keepRecord)
            case path.contains("/versions/"):
                let n = Int(path.split(separator: "/").last ?? "") ?? 0
                return n <= 2 ? (200, ["Content-Type": "image/png"], Data("v\(n)".utf8))
                              : TestSupport.json(#"{"error":{"code":"not_found","message":"no"}}"#, status: 404)
            case path.contains("/tryon/"): return (200, ["Content-Type": "image/png"], Data("img".utf8))
            default: return (404, [:], Data())
            }
        }
    }

    private func make(_ routes: Routes, gate: FakeSpendGate = FakeSpendGate()) -> RunFlow {
        StubURLProtocol.install { routes.answer($0) }
        return RunFlow(client: TestSupport.client(), gate: gate, sleep: { _ in })
    }

    @Test func newJobEntryLoadsAndComposes() async {
        let flow = make(Routes())
        await flow.start(.newJob)
        #expect(flow.phase == .compose)
        #expect(flow.runID == "tg-1000")
        #expect(flow.hasLocalTryon)            // draft batch entry: tryon-motion-enhance + gemini
    }

    @Test func motionOnlyDraftHasNoLocalTryon() async {
        let routes = Routes()
        routes.draft = #"""
        {"owner":"app","pipeline":"motion-enhance","provider":"gemini","generation":1,"slots":{},
         "required":["character","driver"],"optional":[],"missing":[],"validated":true,
         "batch":[],"jobs":1,"estimate_min":40}
        """#
        let flow = make(routes)
        await flow.start(.newJob)
        #expect(!flow.hasLocalTryon)
    }

    @Test func existingEntryWithPreviewsShowsPreviews() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        #expect(flow.phase == .previews)
    }

    @Test func runningPhaseAWinsOverEntry() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonRunning
        let flow = make(routes)
        await flow.start(.newJob)
        #expect(flow.phase == .phaseARunning)
        #expect(flow.needsTryonPolling)
    }

    @Test func startPhaseASendsOneIntentAndPolls() async {
        let routes = Routes()
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.newJob)
        routes.tryon = Fixtures.tryonRunning
        await flow.startPhaseA()
        #expect(await gate.intents == [.phaseA])
        #expect(flow.phase == .phaseARunning)
        #expect(flow.inFlightLabel == nil)
    }

    @Test func finishingPhaseAMovesToPreviewsAndBumpsGeneration() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonRunning
        let flow = make(routes)
        await flow.start(.newJob)
        let before = flow.imageGeneration
        routes.tryon = Fixtures.tryonDone
        await flow.refreshTryon()
        #expect(flow.phase == .previews)
        #expect(flow.imageGeneration == before + 1)
    }

    @Test func regenerateSendsOrderedGuidanceAndCurrentToken() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        await flow.regenerate(index: "0", guidance: [.matchLighting, .keepFace])
        #expect(await gate.intents == [.regen(runID: "tg-1000", index: "0", runToken: "1790000000123.4",
                                              guidance: [.keepFace, .matchLighting])])
        #expect(flow.phase == .phaseARunning)
    }

    @Test func regenerateStaleRefreshesTryonAndShowsServerText() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let gate = FakeSpendGate([.refused(status: 409, code: "stale_panel",
                                           message: "the run changed — read it again", panelToken: nil)])
        let flow = make(routes, gate: gate)
        await flow.start(.existing)
        await flow.regenerate(index: "0", guidance: [])
        #expect(flow.message == "the run changed — read it again")
        #expect(flow.phase == .previews)
        #expect(await gate.intents.count == 1)
    }

    @Test func keepPostsAndMarksTheCurrentImage() async throws {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        await flow.keep(index: "0")
        #expect(flow.isKept("0"))
        let post = try #require(StubURLProtocol.requests.last { $0.url?.path == "/v1/tryon-library" })
        let body = try #require(JSONSerialization.jsonObject(with: post.httpBody ?? Data()) as? [String: String])
        #expect(body == ["run_id": "tg-1000", "index": "0"])
    }

    @Test func versionsStopAtTheFirst404() async {
        let routes = Routes()
        routes.tryon = Fixtures.tryonDone
        let flow = make(routes)
        await flow.start(.existing)
        await flow.loadVersions(index: "0")
        #expect(flow.versions["0"] == [Data("v1".utf8), Data("v2".utf8)])
    }

    @Test func spendInFlightDisablesAndClears() async {
        let gate = FakeSpendGate([.busy(attempts: 4)])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        #expect(!flow.isSpending)
        await flow.startPhaseA()
        #expect(!flow.isSpending)
        #expect(flow.message?.contains("busy") == true)
    }
}
}
```

- [ ] **Step 3: Run to verify failure**

Run: `cd ios/MotionKit && swift test --filter RunFlowTests`
Expected: compile failure — `cannot find 'RunFlow' in scope`.

- [ ] **Step 4: Implement `Stores/RunFlow.swift` (this task's part)**

```swift
import Foundation
import Observation

/// Phase A → try-on previews → rent panel → confirm/resume for the one run
/// slot (API spec §5.8). Reads go straight to `APIClient`; every spend goes
/// through `SpendSending`, which owns the key and the retries. Nothing here
/// ever calls `perform` except in response to one user tap.
@MainActor @Observable
public final class RunFlow {
    public enum Phase: Equatable, Sendable {
        case loading, compose, phaseARunning, previews, rentPanel
        case choiceRequired(panelToken: String, provider: SpendProvider)
        case started(runID: String)
        case outcomeUnknown
    }

    public enum Entry: Sendable { case newJob, existing }

    static let localTryonProviders: Set<String> = ["gemini", "qwen-max"]

    public private(set) var phase: Phase = .loading
    public private(set) var pod: PodStatus?
    public private(set) var tryon: TryonPreviews?
    public private(set) var draft: Draft?
    public private(set) var catalog: [Pipeline] = []
    public private(set) var panel: RentPanel?
    public private(set) var error: APIError?
    /// Server refusals verbatim (409/422), or a notice about the last spend.
    public private(set) var message: String?
    /// Set while a spend request is outstanding; every spend button disables.
    public private(set) var inFlightLabel: String?
    public private(set) var retryNote: String?
    /// The last spend got no definitive answer; "Check again" resends its key.
    public private(set) var needsRecheck = false
    /// Increments when a Phase A or regenerate finishes — the image cache key.
    public private(set) var imageGeneration = 0
    public private(set) var versions: [String: [Data]] = [:]
    public private(set) var isLoadingPanel = false
    public var selectedProvider: SpendProvider = .runpod
    /// RootView switches to the Runs tab (pod strip) and clears it.
    public private(set) var podRequested = false

    private let client: APIClient
    private let gate: any SpendSending
    private let sleep: @Sendable (Duration) async throws -> Void
    private var images: [String: Data] = [:]
    private var keptKeys: Set<String> = []
    private var pendingKind: SpendKind?
    private var didReplay = false

    public init(client: APIClient, gate: any SpendSending,
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }) {
        self.client = client
        self.gate = gate
        self.sleep = sleep
    }

    // MARK: derived

    public var runID: String? { pod?.runId }
    public var isSpending: Bool { inFlightLabel != nil }

    /// Preview try-on is the primary action only when some job would call a
    /// hosted try-on provider. A pipeline without a try-on stage never does.
    public var hasLocalTryon: Bool {
        guard let draft else { return false }
        func local(_ pipeline: String, _ provider: String) -> Bool {
            let stages = catalog.first { $0.id == pipeline }?.stages ?? []
            return stages.contains("tryon") && Self.localTryonProviders.contains(provider)
        }
        let current = draft.missing.isEmpty && local(draft.pipeline, draft.provider)
        return current || draft.batch.contains { local($0.pipeline, $0.provider) }
    }

    /// Only offered when the server says a rental failed and nothing is leased.
    public var canRetryRental: Bool {
        pod?.failedRental != nil && pod?.lease == nil && runID != nil
    }

    public var needsTryonPolling: Bool {
        if phase == .phaseARunning || tryon?.phaseARunning == true { return true }
        return tryon?.previews.contains { $0.status == .pending || $0.status == .running } ?? false
    }

    public func isKept(_ index: String) -> Bool {
        keptKeys.contains("\(index)#\(imageGeneration)")
    }

    // MARK: loading

    public func start(_ entry: Entry) async {
        phase = .loading
        error = nil
        do {
            pod = try await client.get(PodStatus.self, "v1", "pod")
        } catch {
            self.error = error
            return
        }
        guard let runID else {
            error = .decoding("GET /v1/pod returned no run_id")
            return
        }
        do {
            async let t = client.get(TryonPreviews.self, "v1", "runs", runID, "tryon")
            async let d = client.get(Draft.self, "v1", "draft")
            async let c = client.get(PipelineCatalogResponse.self, "v1", "pipelines")
            let (tryon, draft, catalog) = try await (t, d, c)
            self.tryon = tryon
            self.draft = draft
            self.catalog = catalog.pipelines
        } catch {
            self.error = error
            return
        }
        if tryon?.phaseARunning == true {
            phase = .phaseARunning
        } else if entry == .existing, !(tryon?.previews.isEmpty ?? true) {
            phase = .previews
        } else {
            phase = .compose
        }
    }

    /// Cheap (files only on the server). Run detail uses it to decide on
    /// Retry rental without disturbing whatever phase the flow is in.
    public func refreshPod() async {
        do {
            pod = try await client.get(PodStatus.self, "v1", "pod")
        } catch {
            self.error = error
        }
    }

    public func refreshTryon() async {
        guard let runID else { return }
        let wasRunning = tryon?.phaseARunning == true || phase == .phaseARunning
        do {
            let fresh = try await client.get(TryonPreviews.self, "v1", "runs", runID, "tryon")
            tryon = fresh
            error = nil
            if wasRunning && !fresh.phaseARunning {
                imageGeneration += 1
                images = [:]
                versions = [:]
                if phase == .phaseARunning { phase = .previews }
            } else if fresh.phaseARunning, phase == .compose || phase == .previews {
                phase = .phaseARunning
            }
        } catch {
            self.error = error
        }
    }

    /// Every 5 s while the view is visible and something is still running.
    public func pollTryon(interval: Duration = .seconds(5)) async {
        while !Task.isCancelled {
            if needsTryonPolling { await refreshTryon() }
            do { try await sleep(interval) } catch { return }
        }
    }

    // MARK: previews

    public func image(index: String) async -> Data? {
        let key = "\(index)#\(imageGeneration)"
        if let cached = images[key] { return cached }
        guard let runID else { return nil }
        guard let data = try? await client.data("v1", "runs", runID, "tryon", index) else { return nil }
        images[key] = data
        return data
    }

    /// The API returns no version count, so probe 1, 2, … until 404 (max 10).
    public func loadVersions(index: String) async {
        guard let runID, versions[index] == nil else { return }
        var found: [Data] = []
        for n in 1...10 {
            guard let data = try? await client.data("v1", "runs", runID, "tryon", index, "versions", "\(n)") else { break }
            found.append(data)
        }
        versions[index] = found
    }

    public func keep(index: String) async {
        guard let runID else { return }
        do {
            _ = try await client.post(TryonLibraryRecord.self,
                                      body: TryonKeepRequest(runId: runID, index: index),
                                      "v1", "tryon-library")
            keptKeys.insert("\(index)#\(imageGeneration)")
        } catch {
            message = error.userMessage
        }
    }

    // MARK: spends (Task 6)

    public func startPhaseA() async {
        let jobs = draft?.jobs ?? 0
        await spend(.phaseA, label: "Try-on preview · \(jobs) job\(jobs == 1 ? "" : "s")")
    }

    public func regenerate(index: String, guidance: Set<Guidance>) async {
        guard let runID, let token = tryon?.runToken else { return }
        let ordered = Guidance.allCases.filter(guidance.contains)
        await spend(.regen(runID: runID, index: index, runToken: token, guidance: ordered),
                    label: "Regenerate try-on #\(index)")
    }

    public func dismissMessage() { message = nil }
    public func acknowledgePodRequest() { podRequested = false }

    // MARK: spend plumbing

    func spend(_ intent: SpendIntent, label: String) async {
        guard inFlightLabel == nil else {
            message = "Another spend request is still in flight."
            return
        }
        inFlightLabel = label
        retryNote = nil
        message = nil
        needsRecheck = false
        pendingKind = intent.kind
        let result = await gate.perform(intent, label: label) { [weak self] attempt, reason in
            Task { @MainActor in self?.noteRetry(attempt, reason) }
        }
        inFlightLabel = nil
        retryNote = nil
        await apply(result, kind: intent.kind)
    }

    private func noteRetry(_ attempt: Int, _ reason: SpendRetryReason) {
        guard inFlightLabel != nil else { return }
        retryNote = reason == .botBusy
            ? "The bot is busy — retry \(attempt) of 3"
            : "No answer — retry \(attempt) of 2 with the same request"
    }

    func apply(_ result: SpendResult, kind: SpendKind) async {
        switch result {
        case let .accepted(runID, _):
            needsRecheck = false
            switch kind {
            case .phaseA, .regen:
                phase = .phaseARunning
                await refreshTryon()
            case .confirm, .resume:
                phase = .started(runID: runID ?? self.runID ?? "")
                pod = try? await client.get(PodStatus.self, "v1", "pod")
            }
        case let .refused(status, code, text, panelToken):
            needsRecheck = false
            await applyRefusal(status: status, code: code, text: text, panelToken: panelToken, kind: kind)
        case .outcomeUnknown:
            needsRecheck = false
            phase = .outcomeUnknown
            message = "Couldn't tell whether this went through — check the pod before trying again."
            podRequested = true
        case let .busy(attempts):
            message = "The bot stayed busy after \(attempts) attempts. Nothing was recorded — tap again when ready."
        case let .unreachable(detail):
            needsRecheck = true
            pendingKind = kind
            message = "No answer from the VPS (\(detail)). The request is saved — Check again resends it without spending twice."
        case let .expired(label, createdAt):
            needsRecheck = false
            let when = createdAt.map { $0.formatted(date: .omitted, time: .shortened) } ?? "earlier"
            message = "\(label) from \(when) couldn't be verified. Check Runs and the pod before trying again."
        case let .notSent(reason):
            message = reason
        }
    }

    /// Filled in by Task 7 (stale panel, choice_required, stale_run). For
    /// now: the server's text verbatim for 409/422, the mapped text otherwise.
    func applyRefusal(status: Int, code: String, text: String, panelToken: String?,
                      kind: SpendKind) async {
        message = APIError.server(status: status, code: code, message: text).userMessage
        if code == "stale_panel", kind == .regen { await refreshTryon() }
    }
}
```

- [ ] **Step 5: Run to verify pass**

Run: `cd ios/MotionKit && swift test --filter RunFlowTests` → 11 pass; `swift test` → all pass.

- [ ] **Step 6: Commit**

```bash
git add ios/MotionKit/Sources/MotionKit/Stores/RunFlow.swift ios/MotionKit/Tests/MotionKitTests/FakeSpendGate.swift ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift
git commit -m "feat(ios): drive Phase A and try-on previews from RunFlow

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: RunFlow — rent panel, confirm, chooser, resume, recheck, launch replay

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/RunFlow.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift` (append tests inside `RunFlowTests`)

**Interfaces:**
- Consumes: Task 6's `RunFlow`, `spend`, `apply`, `applyRefusal`, `Routes`, `FakeSpendGate`.
- Produces: `continueToRent()`, `loadPanel(force: Bool)`, `quote(for: SpendProvider) -> Double?`,
  `canConfirm(_ provider: SpendProvider) -> Bool`, `confirm()`, `choose(_ choice: TryonChoice)`,
  `retryRental()`, `recheck()`, `replayPendingOnce()`, `pendingNotice: String?`.

- [ ] **Step 1: Write the failing tests** (append inside `struct RunFlowTests`)

```swift
    @Test func rentPanelSelectsRunpodAndQuotes() async {
        let flow = make(Routes())
        await flow.start(.newJob)
        await flow.continueToRent()
        #expect(flow.phase == .rentPanel)
        #expect(flow.selectedProvider == .runpod)
        #expect(flow.quote(for: .runpod) == 84.0 / 60 * 0.99)
        #expect(flow.quote(for: .vast) == 1.05)       // Vast: its own session estimate
        #expect(flow.canConfirm(.runpod))
    }

    @Test func soldOutAndBlockedVastOfferNoSpendButton() async {
        let routes = Routes()
        routes.setPanels([Fixtures.rentPanelSoldOut])
        let flow = make(routes)
        await flow.start(.newJob)
        await flow.continueToRent()
        #expect(flow.quote(for: .runpod) == nil)
        #expect(flow.quote(for: .vast) == nil)
        #expect(!flow.canConfirm(.runpod) && !flow.canConfirm(.vast))
    }

    @Test func confirmSendsPanelTokenAndGpu() async {
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(await gate.intents == [.confirm(runID: "tg-1000", provider: .runpod,
                                                panelToken: "1790000000123.4.9",
                                                gpu: "NVIDIA GeForce RTX 5090", tryon: nil)])
        #expect(await gate.labels.first?.contains("$1.39") == true)
        #expect(flow.phase == .started(runID: "tg-1000"))
    }

    @Test func stalePanelRereadsAndNeverConfirmsAgain() async {
        let routes = Routes()
        routes.setPanels([Fixtures.rentPanel, Fixtures.rentPanelFresh])
        let gate = FakeSpendGate([.refused(status: 409, code: "stale_panel",
                                           message: "the job changed since the panel was read — read it again",
                                           panelToken: nil)])
        let flow = make(routes, gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(await gate.intents.count == 1)
        #expect(flow.phase == .rentPanel)
        #expect(flow.panel?.panelToken == "1790000000999.1.10")
        #expect(StubURLProtocol.requests.filter { $0.url?.path.hasSuffix("/rent-panel") == true }.count == 2)
        #expect(flow.message == "the job changed since the panel was read — read it again")
    }

    @Test func choiceRequiredOffersTwoFreshTaps() async {
        let gate = FakeSpendGate([
            .refused(status: 409, code: "choice_required", message: "try-on already ran", panelToken: "55.6"),
            .accepted(runID: "tg-1000", outcome: "started"),
        ])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(flow.phase == .choiceRequired(panelToken: "55.6", provider: .runpod))
        await flow.choose(.reuse)
        let intents = await gate.intents
        #expect(intents.count == 2)
        #expect(intents[1] == .confirm(runID: "tg-1000", provider: .runpod, panelToken: "55.6",
                                       gpu: "NVIDIA GeForce RTX 5090", tryon: .reuse))
        #expect(flow.phase == .started(runID: "tg-1000"))
    }

    @Test func outcomeUnknownRequestsThePod() async {
        let gate = FakeSpendGate([.outcomeUnknown])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(flow.phase == .outcomeUnknown)
        #expect(flow.podRequested)
        flow.acknowledgePodRequest()
        #expect(!flow.podRequested)
    }

    @Test func retryRentalOnlyWithAFailedRentalAndUsesRunToken() async {
        let live = Routes()
        live.pod = Fixtures.podLive
        let liveFlow = make(live)
        await liveFlow.start(.existing)
        #expect(!liveFlow.canRetryRental)

        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(Routes(), gate: gate)       // podIdle carries failed_rental
        await flow.start(.existing)
        #expect(flow.canRetryRental)
        await flow.retryRental()
        #expect(await gate.intents == [.resume(runID: "tg-1000", provider: .runpod,
                                               runToken: "1790000000123.4",
                                               gpu: "NVIDIA GeForce RTX 5090")])
    }

    @Test func noFailureRefusalIsShownVerbatim() async {
        let gate = FakeSpendGate([.refused(status: 409, code: "no_failure",
                                           message: "no failed rental to retry for this run", panelToken: nil)])
        let flow = make(Routes(), gate: gate)
        await flow.start(.existing)
        await flow.retryRental()
        #expect(flow.message == "no failed rental to retry for this run")
    }

    @Test func unreachableOffersRecheckWhichNeverPerforms() async {
        let gate = FakeSpendGate([.unreachable(detail: "timed out"),
                                  .accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(Routes(), gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        await flow.confirm()
        #expect(flow.needsRecheck)
        await flow.recheck()
        #expect(await gate.intents.count == 1)
        #expect(await gate.rechecks == 1)
        #expect(!flow.needsRecheck)
        #expect(flow.phase == .started(runID: "tg-1000"))
    }

    @Test func replayRunsOncePerLaunch() async {
        let entry = SpendLedgerEntry(key: "OLD", intent: .confirm(runID: "tg-1000", provider: .runpod,
                                                                  panelToken: "t", gpu: nil, tryon: nil),
                                     label: "Confirm · RTX 5090 · ~$1.39", createdAt: .now)
        let gate = FakeSpendGate(pending: entry, replay: .accepted(runID: "tg-1000", outcome: "started"))
        let flow = make(Routes(), gate: gate)
        await flow.replayPendingOnce()
        await flow.replayPendingOnce()
        #expect(await gate.replays == 1)
        #expect(flow.phase == .started(runID: "tg-1000"))
        #expect(flow.pendingNotice == nil)
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `cd ios/MotionKit && swift test --filter RunFlowTests`
Expected: compile failure — `value of type 'RunFlow' has no member 'continueToRent'`.

- [ ] **Step 3: Implement** — add to `RunFlow`:

```swift
    /// "Checking the earlier Confirm…" while the launch replay is outstanding.
    public private(set) var pendingNotice: String?

    // MARK: rent panel

    public func continueToRent() async {
        phase = .rentPanel
        await loadPanel(force: false)
    }

    /// `force` = pull-to-refresh: fresh stock instead of the bot's cache.
    public func loadPanel(force: Bool) async {
        guard let runID else { return }
        isLoadingPanel = true
        defer { isLoadingPanel = false }
        do {
            let fresh = force
                ? try await client.get(RentPanel.self, query: [URLQueryItem(name: "force", value: "1")],
                                       "v1", "runs", runID, "rent-panel")
                : try await client.get(RentPanel.self, "v1", "runs", runID, "rent-panel")
            panel = fresh
            error = nil
            if quote(for: selectedProvider) == nil {
                selectedProvider = quote(for: .runpod) != nil ? .runpod
                    : quote(for: .vast) != nil ? .vast : .runpod
            }
        } catch {
            self.error = error
        }
    }

    /// No quote → no spend button for that row.
    public func quote(for provider: SpendProvider) -> Double? {
        guard let panel else { return nil }
        switch provider {
        case .runpod:
            guard !panel.runpod.soldOut else { return nil }
            return CostEstimate.quote(estimateMin: panel.estimateMin, usdPerHr: panel.runpod.usdPerHr)
        case .vast:
            guard panel.vast.canSpend else { return nil }
            return panel.vast.sessionUsd
                ?? CostEstimate.quote(estimateMin: panel.estimateMin, usdPerHr: panel.vast.usdPerHr)
        }
    }

    public func canConfirm(_ provider: SpendProvider) -> Bool {
        panel != nil && quote(for: provider) != nil && !isSpending && !isLoadingPanel
    }

    private func confirmLabel(_ provider: SpendProvider, suffix: String = "") -> String {
        let where_ = provider == .runpod ? (panel?.runpod.gpu ?? "RunPod") : "Vast"
        let price = quote(for: provider).map { " · ~\(Format.usd($0)) quote" } ?? ""
        return "Confirm\(suffix) · \(where_)\(price)"
    }

    public func confirm() async {
        guard let runID, let panel, canConfirm(selectedProvider) else { return }
        let provider = selectedProvider
        await spend(.confirm(runID: runID, provider: provider, panelToken: panel.panelToken,
                             gpu: provider == .runpod ? panel.runpod.gpu : nil, tryon: nil),
                    label: confirmLabel(provider))
    }

    /// The reuse/rerun answer to `choice_required` — its own tap, its own key.
    public func choose(_ choice: TryonChoice) async {
        guard let runID, case let .choiceRequired(token, provider) = phase else { return }
        await spend(.confirm(runID: runID, provider: provider, panelToken: token,
                             gpu: provider == .runpod ? panel?.runpod.gpu : nil, tryon: choice),
                    label: confirmLabel(provider, suffix: choice == .reuse ? " (reuse try-on)" : " (re-run try-on)"))
    }

    // MARK: resume

    public func retryRental() async {
        guard canRetryRental, let runID, let gpu = pod?.gpu else { return }
        await refreshTryon()      // resume needs the CURRENT run_token
        guard let token = tryon?.runToken else { return }
        await spend(.resume(runID: runID, provider: .runpod, runToken: token, gpu: gpu),
                    label: "Retry rental · \(gpu)")
    }

    // MARK: recheck and replay

    /// Resends the saved request with its saved key. Never mints a new one.
    public func recheck() async {
        guard needsRecheck, inFlightLabel == nil else { return }
        let kind = pendingKind ?? .confirm
        inFlightLabel = "Checking the earlier request…"
        message = nil
        let result = await gate.recheck { [weak self] attempt, reason in
            Task { @MainActor in self?.noteRetry(attempt, reason) }
        }
        inFlightLabel = nil
        retryNote = nil
        await apply(result, kind: kind)
    }

    public func replayPendingOnce() async {
        guard !didReplay else { return }
        didReplay = true
        guard let entry = await gate.pending() else { return }
        pendingNotice = "Checking the earlier \(entry.label)…"
        let result = await gate.replayPending()
        pendingNotice = nil
        guard let result else { return }
        if pod == nil { pod = try? await client.get(PodStatus.self, "v1", "pod") }
        await apply(result, kind: entry.intent.kind)
    }
```

Replace Task 6's `applyRefusal` body with:

```swift
    func applyRefusal(status: Int, code: String, text: String, panelToken: String?,
                      kind: SpendKind) async {
        message = APIError.server(status: status, code: code, message: text).userMessage
        switch (code, kind) {
        case ("choice_required", .confirm):
            if let panelToken { phase = .choiceRequired(panelToken: panelToken, provider: selectedProvider) }
        case ("stale_panel", .confirm):
            // Never an automatic re-confirm: re-read, show the new price, wait.
            phase = .rentPanel
            await loadPanel(force: false)
        case ("stale_panel", .regen), ("stale_run", .resume):
            await refreshTryon()
        default:
            break
        }
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `cd ios/MotionKit && swift test --filter RunFlowTests` → 21 pass; `swift test` → all pass.
Note: `confirmSendsPanelTokenAndGpu` expects `$1.39` = 84/60 × 0.99 = 1.386 → `Format.usd` rounds to `$1.39`.

- [ ] **Step 5: Commit**

```bash
git add ios/MotionKit/Sources/MotionKit/Stores/RunFlow.swift ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift
git commit -m "feat(ios): confirm, choose and resume through RunFlow

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Contract GETs and the live zero-spend refusal smoke

**Files:**
- Modify: `ios/MotionKit/Sources/motion-contract/main.swift`
- Modify: `Makefile` (`.PHONY` line and after `ios-contract`)

**Interfaces:**
- Consumes: `APIClient`, `SpendGate`, `IdempotencyLedger`, `SpendIntent`, `SpendResult`, `PodStatus`,
  `TryonPreviews`, `RentPanel`.
- Produces: `make ios-refusal-smoke` (runs `motion-contract <env> --refusal-smoke`).

- [ ] **Step 1: Add the two GETs** — in `main.swift`, directly after the `GET /v1/pod` check, replace
  `await check("GET /v1/pod") { … }` with:

```swift
var slot: PodStatus?
await check("GET /v1/pod") { slot = try await client.get(PodStatus.self, "v1", "pod") }
if let runID = slot?.runId {
    await check("GET /v1/runs/{slot}/tryon") {
        _ = try await client.get(TryonPreviews.self, "v1", "runs", runID, "tryon")
    }
    // Cached stock (no ?force=1); the Vast quote inside can be slow.
    await check("GET /v1/runs/{slot}/rent-panel") {
        _ = try await client.get(RentPanel.self, "v1", "runs", runID, "rent-panel")
    }
} else {
    print("skip GET /v1/runs/{slot}/tryon and rent-panel (no run_id on /v1/pod)")
}
```

Also change the header comment to: `// Decodes the live API's read routes with the app's own models. GET only —`
`// spends nothing — unless --refusal-smoke is passed (see below).`

- [ ] **Step 2: Add the refusal smoke** — insert immediately after `var failed = 0`… no: insert right
  after the `check` function definition and before `await check("GET /v1/health")`:

```swift
// --refusal-smoke: three spend calls with deliberately bogus tokens, through
// the real SpendGate. Each is refused by a token check that runs before any
// provider or pod call (bot.py AppRuns.confirm / _regen_tryon / AppPod.resume,
// verified 2026-09-23). phase-a is never sent: it has no token to refuse on.
if CommandLine.arguments.contains("--refusal-smoke") {
    let ledgerRoot = FileManager.default.temporaryDirectory.appending(component: "motion-refusal-\(UUID().uuidString)")
    let gate = SpendGate(client: client, ledger: IdempotencyLedger(root: ledgerRoot))
    func noLease(_ when: String) async -> PodStatus? {
        guard let pod = try? await client.get(PodStatus.self, "v1", "pod") else {
            print("FAIL GET /v1/pod \(when)"); return nil
        }
        guard pod.lease == nil else {
            print("FAIL a pod is leased \(when) — refusing to continue"); return nil
        }
        print("ok   no lease \(when)")
        return pod
    }
    guard let pod = await noLease("before"), let runID = pod.runId else { exit(1) }
    let bogus = "refusal-smoke-\(UUID().uuidString)"
    let cases: [(String, SpendIntent, String)] = [
        ("confirm stale panel_token", .confirm(runID: runID, provider: .runpod, panelToken: bogus,
                                               gpu: pod.gpu, tryon: nil), "stale_panel"),
        ("regen stale run_token", .regen(runID: runID, index: "0", runToken: bogus, guidance: []), "stale_panel"),
        ("resume stale run_token", .resume(runID: runID, provider: .runpod, runToken: bogus, gpu: pod.gpu), "stale_run"),
    ]
    var smokeFailed = 0
    for (name, intent, expected) in cases {
        let result = await gate.perform(intent, label: "refusal smoke: \(name)") { _, _ in }
        if case .refused(409, expected, _, _) = result {
            print("ok   \(name) → 409 \(expected)")
        } else {
            print("FAIL \(name): \(result)")
            smokeFailed += 1
        }
    }
    if await noLease("after") == nil { smokeFailed += 1 }
    try? FileManager.default.removeItem(at: ledgerRoot)
    exit(smokeFailed == 0 ? 0 : 1)
}
```

- [ ] **Step 3: Add the Make target** — append `ios-refusal-smoke` to the `.PHONY` line, and after the
  `ios-contract` recipe add:

```make
ios-refusal-smoke: ## Live zero-spend check: bogus-token confirm/regen/resume must be refused (no pod)
	cd ios/MotionKit && swift run -q motion-contract ../../.env --refusal-smoke
```

- [ ] **Step 4: Build and run the free contract**

Run: `cd ios/MotionKit && swift build` → builds. Then `make ios-contract`.
Expected: every line `ok`, including `GET /v1/runs/{slot}/tryon` and `GET /v1/runs/{slot}/rent-panel`.
If a new model fails to decode, fix the model (not the contract) and add the missing shape to the
fixture from Task 5.

- [ ] **Step 5: Run the refusal smoke — only after asking the user**

This sends three real (refused) spend requests to the live VPS. Ask the user: "OK to run
`make ios-refusal-smoke` now? It sends bogus-token confirm/regen/resume, which the bot refuses before
any provider or pod call, and it aborts if a pod is leased." On yes:

Run: `make ios-refusal-smoke`
Expected: `ok   no lease before`, three `ok … → 409 …` lines, `ok   no lease after`, exit 0.

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/motion-contract/main.swift Makefile
git commit -m "feat(ios): contract-check the run flow reads and refusals

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: App wiring — gate, flow, tab selection, spend banner, UI-test stand-in

**Files:**
- Create: `ios/MotionApp/RunFlow/RecordingSpendGate.swift`
- Modify: `ios/MotionApp/MotionApp.swift` (`AppModel`)
- Modify: `ios/MotionApp/RootView.swift`

**Interfaces:**
- Consumes: `SpendGate`, `SpendSending`, `RunFlow`.
- Produces: `AppModel.runFlow: RunFlow?`, `AppModel.selectedTab: AppTab` (`.runs/.materials/.newJob/.outputs`),
  `AppModel.replayPendingSpend()`; launch argument `-UITestRecordingSpendGate` swaps in
  `RecordingSpendGate`.

- [ ] **Step 1: Create the stand-in**

```swift
import Foundation
import MotionKit

/// Used only when the app is launched with `-UITestRecordingSpendGate`: a
/// stray tap in a UI test is recorded and refused on the phone, so the test
/// build can never reach a spend route on the VPS.
actor RecordingSpendGate: SpendSending {
    private(set) var intents: [SpendIntent] = []

    func perform(_ intent: SpendIntent, label: String, onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        intents.append(intent)
        return .notSent(reason: "UI test build — spend recorded, not sent.")
    }
    func recheck(onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        .notSent(reason: "UI test build — nothing to check.")
    }
    func replayPending() async -> SpendResult? { nil }
    func pending() async -> SpendLedgerEntry? { nil }
}
```

- [ ] **Step 2: Wire `AppModel`** — in `MotionApp.swift`:

Add above `final class AppModel`:

```swift
enum AppTab: Hashable { case runs, materials, newJob, outputs }
```

Inside `AppModel`, add properties:

```swift
    private(set) var runFlow: RunFlow?
    var selectedTab: AppTab = .runs
    private var replayTask: Task<Void, Never>?
```

In `reconnect()`, add `runFlow = nil` to the nil branch, and after `outputs = OutputsStore(client: client)`:

```swift
        let gate: any SpendSending = ProcessInfo.processInfo.arguments.contains("-UITestRecordingSpendGate")
            ? RecordingSpendGate()
            : SpendGate(client: client)
        runFlow = RunFlow(client: client, gate: gate)
```

Add the method:

```swift
    /// Once per launch (RunFlow guards it): resend an interrupted spend with
    /// its original key, or report it as too old to verify.
    func replayPendingSpend() {
        guard replayTask == nil, let runFlow else { return }
        replayTask = Task { await runFlow.replayPendingOnce() }
    }
```

- [ ] **Step 3: Wire `RootView`** — replace the `TabView { … }` construction with a selection-bound
  version and add the banner/observers. The full new `body`:

```swift
    var body: some View {
        @Bindable var model = model
        Group {
            if let runs = model.runs, let pod = model.pod,
               let materials = model.materials, let draft = model.draft,
               let outputs = model.outputs, let flow = model.runFlow {
                TabView(selection: $model.selectedTab) {
                    Tab("Runs", systemImage: "waveform.path.ecg", value: AppTab.runs) {
                        NavigationStack { RunsView(runs: runs, pod: pod, flow: flow) }
                    }
                    Tab("Material", systemImage: "square.grid.2x2", value: AppTab.materials) {
                        NavigationStack { MaterialsView(store: materials) }
                    }
                    Tab("New Job", systemImage: "plus.circle.fill", value: AppTab.newJob) {
                        NavigationStack { NewJobView(store: draft, materials: materials, flow: flow) }
                    }
                    Tab("Output", systemImage: "play.rectangle", value: AppTab.outputs) {
                        NavigationStack { OutputsView(store: outputs) }
                    }
                }
                .tint(Theme.lime)
                .safeAreaInset(edge: .top) { SpendBanner(flow: flow) }
                .onChange(of: flow.podRequested) { _, requested in
                    guard requested else { return }
                    model.selectedTab = .runs
                    Task { await pod.refresh() }
                    flow.acknowledgePodRequest()
                }
            } else {
                NavigationStack { SettingsView(firstRun: true) }
            }
        }
        .background(Theme.bg)
        .task { model.resumeMaterialsUpload() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                model.resumeMaterialsUpload()
                model.replayPendingSpend()
            }
        }
    }
```

And add, in the same file:

```swift
/// Visible on every tab while a spend is outstanding or being re-checked.
struct SpendBanner: View {
    let flow: RunFlow
    var body: some View {
        if let text = flow.pendingNotice ?? flow.inFlightLabel.map({ "Sending: \($0)" }) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small).tint(Theme.lime)
                VStack(alignment: .leading, spacing: 2) {
                    Text(text).font(Theme.sans(13, .semibold)).foregroundStyle(Theme.ink1)
                    if let note = flow.retryNote {
                        Text(note).font(Theme.mono(11)).foregroundStyle(Theme.amber)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .card(border: Theme.limeLine)
            .padding(.horizontal, 16)
        }
    }
}
```

`RunsView` and `NewJobView` gain a `flow: RunFlow` parameter in Task 10. To keep this task
compiling on its own, add `let flow: RunFlow` to both structs now (unused until Task 10).

- [ ] **Step 4: Build**

Run: `make ios-build`
Expected: build succeeds.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/RunFlow/RecordingSpendGate.swift ios/MotionApp/MotionApp.swift ios/MotionApp/RootView.swift ios/MotionApp/Runs/RunsView.swift ios/MotionApp/NewJob/NewJobView.swift
git commit -m "feat(ios): wire the spend gate and run flow into the app

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Run flow screens, entry points and the Phase 4 UI smoke

**Files:**
- Create: `ios/MotionApp/RunFlow/RunFlowView.swift`
- Create: `ios/MotionApp/RunFlow/TryonPreviewCard.swift`
- Create: `ios/MotionApp/RunFlow/RentPanelView.swift`
- Modify: `ios/MotionApp/NewJob/NewJobView.swift` (validation section)
- Modify: `ios/MotionApp/Runs/RunsView.swift` (live card destination)
- Modify: `ios/MotionApp/Runs/RunDetailView.swift` (Retry rental)
- Create: `ios/MotionAppUITests/Phase4SmokeTests.swift`
- Modify: `ios/project.yml` (pass the launch argument to UI tests — see Step 7)

**Interfaces:**
- Consumes: everything `RunFlow` exposes (Tasks 6–7), `Theme`, `SectionLabel`, `ErrorBanner`, `PulseDot`.
- Produces: `RunFlowView(flow:entry:)`; accessibility identifiers used by the UI test:
  `runflow.previewTryon`, `runflow.rentWithoutPreview`, `runflow.confirm`, `runflow.soldOut`,
  `runflow.quote`, `newjob.continueToRun`.

- [ ] **Step 1: `RunFlowView.swift`**

```swift
import MotionKit
import SwiftUI

struct RunFlowView: View {
    let flow: RunFlow
    let entry: RunFlow.Entry
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let error = flow.error { ErrorBanner(error: error) { await flow.start(entry) } }
                if let message = flow.message { MessageCard(text: message) { flow.dismissMessage() } }
                if flow.needsRecheck {
                    Button("Check again") { Task { await flow.recheck() } }
                        .buttonStyle(SecondaryButtonStyle())
                        .disabled(flow.isSpending)
                }
                content
            }
            .padding(.horizontal, 20).padding(.top, 4)
        }
        .background(Theme.bg)
        .navigationTitle("Run")
        .navigationBarTitleDisplayMode(.inline)
        .task { await flow.start(entry) }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await flow.pollTryon()
        }
    }

    @ViewBuilder private var content: some View {
        switch flow.phase {
        case .loading:
            ProgressView().frame(maxWidth: .infinity).padding(.top, 60)
        case .compose:
            compose
        case .phaseARunning:
            SectionLabel(text: "Try-on on the VPS")
            ForEach(flow.tryon?.previews ?? []) { preview in
                HStack {
                    StageDot(status: preview.status)
                    Text(preview.run).font(Theme.mono(12)).foregroundStyle(Theme.ink1)
                }
            }
            if flow.tryon?.previews.isEmpty ?? true {
                HStack(spacing: 8) { PulseDot(); Text("Starting…").font(Theme.sans(13)).foregroundStyle(Theme.ink2) }
            }
        case .previews:
            SectionLabel(text: "Try-on previews")
            ForEach(flow.tryon?.previews ?? []) { preview in
                TryonPreviewCard(flow: flow, preview: preview)
            }
            Button("Continue to rent") { Task { await flow.continueToRent() } }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(flow.isSpending)
        case .rentPanel:
            RentPanelView(flow: flow)
        case .choiceRequired:
            SectionLabel(text: "Try-on already ran")
            Text("These inputs already have a try-on. Reusing it spends no Gemini/Qwen quota; re-running pays for it again.")
                .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
            Button("Reuse try-on (no quota)") { Task { await flow.choose(.reuse) } }
                .buttonStyle(PrimaryButtonStyle()).disabled(flow.isSpending)
            Button("Re-run try-on") { Task { await flow.choose(.rerun) } }
                .buttonStyle(SecondaryButtonStyle()).disabled(flow.isSpending)
        case let .started(runID):
            Label("Started — progress is on the run screen and in Telegram.", systemImage: "checkmark.circle.fill")
                .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.lime)
            if let client = model.client {
                NavigationLink("Open \(runID)") {
                    RunDetailView(store: RunDetailStore(client: client, runID: runID), flow: flow)
                }
                .buttonStyle(SecondaryButtonStyle())
            }
        case .outcomeUnknown:
            Label("Couldn't tell whether this went through. The Runs tab shows the pod.", systemImage: "questionmark.circle")
                .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.amber)
        }
    }

    @Environment(AppModel.self) private var model

    @ViewBuilder private var compose: some View {
        SectionLabel(text: "Next step")
        let jobs = flow.draft?.jobs ?? 0
        Text("\(jobs) job\(jobs == 1 ? "" : "s") in the draft.")
            .font(Theme.sans(14)).foregroundStyle(Theme.ink1)
        if flow.hasLocalTryon {
            Button("Preview try-on") { Task { await flow.startPhaseA() } }
                .buttonStyle(PrimaryButtonStyle())
                .accessibilityIdentifier("runflow.previewTryon")
                .disabled(flow.isSpending)
            Text("Spends Gemini/Qwen quota — no pod is rented.")
                .font(Theme.mono(11)).foregroundStyle(Theme.ink3)
        }
        Button("Rent without preview") { Task { await flow.continueToRent() } }
            .buttonStyle(flow.hasLocalTryon ? AnyButtonStyle(SecondaryButtonStyle()) : AnyButtonStyle(PrimaryButtonStyle()))
            .accessibilityIdentifier("runflow.rentWithoutPreview")
            .disabled(flow.isSpending)
        if !(flow.tryon?.previews.isEmpty ?? true) {
            Button("View last previews") { Task { await flow.start(.existing) } }
                .buttonStyle(SecondaryButtonStyle())
        }
    }
}

struct MessageCard: View {
    let text: String
    let dismiss: () -> Void
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "info.circle").foregroundStyle(Theme.amber)
            Text(text).font(Theme.sans(13)).foregroundStyle(Theme.ink1)
            Spacer(minLength: 0)
            Button("Dismiss", action: dismiss).font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
        }
        .padding(12).card(border: Theme.amber.opacity(0.4))
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.limeInk)
            .frame(maxWidth: .infinity).padding(.vertical, 13)
            .background(Theme.lime.opacity(configuration.isPressed ? 0.7 : 1), in: .rect(cornerRadius: 12))
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.ink1)
            .frame(maxWidth: .infinity).padding(.vertical, 13)
            .background(Theme.surface2.opacity(configuration.isPressed ? 0.7 : 1), in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.line2))
    }
}

struct AnyButtonStyle: ButtonStyle {
    private let make: (Configuration) -> AnyView
    init<S: ButtonStyle>(_ style: S) { make = { AnyView(style.makeBody(configuration: $0)) } }
    func makeBody(configuration: Configuration) -> some View { make(configuration) }
}
```

- [ ] **Step 2: `TryonPreviewCard.swift`**

```swift
import MotionKit
import SwiftUI

struct TryonPreviewCard: View {
    let flow: RunFlow
    let preview: TryonPreview
    @State private var image: UIImage?
    @State private var showVersions = false
    @State private var showRegenerate = false
    @State private var guidance: Set<Guidance> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(preview.run).font(Theme.mono(12, .semibold)).foregroundStyle(Theme.ink1)
                Spacer()
                StageDot(status: preview.status)
            }
            if let image {
                Image(uiImage: image).resizable().scaledToFit()
                    .clipShape(.rect(cornerRadius: 12))
            } else if preview.hasImage {
                ProgressView().frame(maxWidth: .infinity, minHeight: 200)
            } else {
                Text(preview.status == .error ? "Try-on failed for this job." : "No image yet.")
                    .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
            }
            if preview.hasImage {
                HStack(spacing: 10) {
                    Button(flow.isKept(preview.index) ? "Saved" : "Keep") {
                        Task { await flow.keep(index: preview.index) }
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(flow.isKept(preview.index))
                    Button("Versions") {
                        showVersions.toggle()
                        if showVersions { Task { await flow.loadVersions(index: preview.index) } }
                    }
                    .buttonStyle(SecondaryButtonStyle())
                }
            }
            Button("Regenerate…") { showRegenerate = true }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(flow.isSpending)
            if showVersions { versionStrip }
        }
        .padding(14).card()
        .task(id: "\(preview.index)#\(flow.imageGeneration)#\(preview.hasImage)") {
            guard preview.hasImage else { image = nil; return }
            image = await flow.image(index: preview.index).flatMap(UIImage.init(data:))
        }
        .sheet(isPresented: $showRegenerate) { regenerateSheet }
    }

    @ViewBuilder private var versionStrip: some View {
        let items = flow.versions[preview.index] ?? []
        if items.isEmpty {
            Text("No earlier versions.").font(Theme.mono(11)).foregroundStyle(Theme.ink3)
        } else {
            ScrollView(.horizontal) {
                HStack(spacing: 8) {
                    ForEach(Array(items.enumerated()), id: \.offset) { n, data in
                        VStack(spacing: 4) {
                            if let ui = UIImage(data: data) {
                                Image(uiImage: ui).resizable().scaledToFill()
                                    .frame(width: 72, height: 96).clipShape(.rect(cornerRadius: 8))
                            }
                            Text("v\(n + 1)").font(Theme.mono(10)).foregroundStyle(Theme.ink2)
                        }
                    }
                }
            }
        }
    }

    private var regenerateSheet: some View {
        NavigationStack {
            Form {
                Section("Guidance") {
                    ForEach(Guidance.allCases, id: \.self) { g in
                        Toggle(g.label, isOn: Binding(
                            get: { guidance.contains(g) },
                            set: { on in if on { guidance.insert(g) } else { guidance.remove(g) } }))
                    }
                }
                Section {
                    Text("Spends Gemini/Qwen quota again. The current image becomes an earlier version.")
                        .font(Theme.sans(12)).foregroundStyle(Theme.ink2)
                }
            }
            .navigationTitle("Regenerate #\(preview.index)")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { showRegenerate = false } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Regenerate") {
                        showRegenerate = false
                        let chosen = guidance
                        Task { await flow.regenerate(index: preview.index, guidance: chosen) }
                    }
                    .disabled(flow.isSpending)
                }
            }
        }
        .presentationDetents([.medium])
    }
}
```

- [ ] **Step 3: `RentPanelView.swift`**

```swift
import MotionKit
import SwiftUI

struct RentPanelView: View {
    let flow: RunFlow

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionLabel(text: "Rent GPU")
            if let panel = flow.panel {
                Text("\(panel.jobs) job\(panel.jobs == 1 ? "" : "s") · about \(Int(panel.estimateMin)) min"
                     + (panel.afterPhaseA ? " · try-on already done" : ""))
                    .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                runpodRow(panel.runpod)
                vastRow(panel.vast)
                if let price = flow.quote(for: flow.selectedProvider), flow.canConfirm(flow.selectedProvider) {
                    Button {
                        Task { await flow.confirm() }
                    } label: {
                        Text("Confirm · ~\(Format.usd(price)) quote")
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .accessibilityIdentifier("runflow.confirm")
                    Text("A quote from the estimate, not the invoice.")
                        .font(Theme.mono(10)).foregroundStyle(Theme.ink3)
                        .accessibilityIdentifier("runflow.quote")
                } else if flow.quote(for: .runpod) == nil && flow.quote(for: .vast) == nil {
                    Label("Nothing can be rented right now.", systemImage: "xmark.octagon")
                        .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.red)
                        .accessibilityIdentifier("runflow.soldOut")
                }
                if flow.isSpending {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text(flow.inFlightLabel ?? "").font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                    }
                }
            } else if flow.isLoadingPanel {
                ProgressView("Reading stock and prices…").frame(maxWidth: .infinity).padding(.top, 30)
            }
        }
        .refreshable { await flow.loadPanel(force: true) }
    }

    private func runpodRow(_ row: RentPanelRunpod) -> some View {
        providerRow(provider: .runpod, title: row.gpu,
                    detail: [row.datacenter, row.stock.map { "stock \($0)" },
                             row.usdPerHr.map { "\(Format.usd($0))/h" }].compactMap { $0 }.joined(separator: " · "),
                    blockers: row.soldOut ? ["Sold out at \(row.datacenter ?? "the home datacenter")"] : [])
    }

    private func vastRow(_ row: RentPanelVast) -> some View {
        providerRow(provider: .vast, title: "Vast",
                    detail: [row.usdPerHr.map { "\(Format.usd($0))/h" },
                             row.sessionUsd.map { "session ~\(Format.usd($0))" }].compactMap { $0 }.joined(separator: " · "),
                    blockers: row.canSpend ? [] : row.blockers)
    }

    private func providerRow(provider: SpendProvider, title: String, detail: String, blockers: [String]) -> some View {
        let enabled = flow.quote(for: provider) != nil
        let selected = flow.selectedProvider == provider
        return Button {
            flow.selectedProvider = provider
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Image(systemName: selected && enabled ? "largecircle.fill.circle" : "circle")
                        .foregroundStyle(enabled ? Theme.lime : Theme.ink3)
                    Text(title).font(Theme.sans(15, .semibold)).foregroundStyle(enabled ? Theme.ink : Theme.ink3)
                    Spacer()
                }
                if !detail.isEmpty { Text(detail).font(Theme.mono(11)).foregroundStyle(Theme.ink2) }
                ForEach(blockers, id: \.self) { b in
                    Text(b).font(Theme.sans(12)).foregroundStyle(Theme.amber)
                }
            }
            .padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .card(border: selected && enabled ? Theme.limeLine : Theme.line)
        }
        .buttonStyle(.plain)
        .disabled(!enabled || flow.isSpending)
    }
}
```

- [ ] **Step 4: Entry points**

`NewJobView`: in `validation(_:)`, directly after the `Validate` button (inside the `VStack`), add:

```swift
            if store.isReady {
                NavigationLink {
                    RunFlowView(flow: flow, entry: .newJob)
                } label: {
                    Text("Continue to run →")
                        .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.ink1)
                        .frame(maxWidth: .infinity).padding(.vertical, 13)
                        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.limeLine))
                }
                .accessibilityIdentifier("newjob.continueToRun")
                .disabled(store.isBusy)
            }
```

`RunsView`: change the live card so a `phase_a` run opens the flow and anything else the detail:

```swift
                if let live = runs.live {
                    if live.status == .phaseA {
                        NavigationLink { RunFlowView(flow: flow, entry: .existing) } label: { LiveRunCard(run: live) }
                            .buttonStyle(.plain)
                    } else {
                        NavigationLink(value: live.id) { LiveRunCard(run: live) }.buttonStyle(.plain)
                    }
                }
```

and the destination: `RunDetailView(store: RunDetailStore(client: client, runID: id), flow: flow)`.

`RunDetailView`: add `let flow: RunFlow` after `store`; after the `StatusHero(...)` line add:

```swift
                    if d.id == flow.runID, flow.canRetryRental, let failure = flow.pod?.failedRental {
                        VStack(alignment: .leading, spacing: 8) {
                            SectionLabel(text: "Rental failed")
                            Text(failure.detail).font(Theme.sans(13)).foregroundStyle(Theme.ink1)
                            Button("Retry rental · \(failure.gpu)") { Task { await flow.retryRental() } }
                                .buttonStyle(PrimaryButtonStyle())
                                .disabled(flow.isSpending)
                            if let message = flow.message {
                                Text(message).font(Theme.sans(12)).foregroundStyle(Theme.amber)
                            }
                        }
                        .padding(14).card(border: Theme.redLine)
                    }
```

and add a second modifier after `.task(id: scenePhase)` so `canRetryRental` is current:
`.task { await flow.refreshPod() }`.

- [ ] **Step 5: Build**

Run: `make ios-build` → succeeds. Fix compile errors only in the files this task touches.

- [ ] **Step 6: Write the UI smoke** — `ios/MotionAppUITests/Phase4SmokeTests.swift`

```swift
import XCTest

/// Zero-spend: the app is launched with -UITestRecordingSpendGate, so even a
/// stray tap on a spend button is recorded on the phone and never sent. The
/// test still never taps one. The rent panel read is a free GET.
final class Phase4SmokeTests: XCTestCase {
    @MainActor
    func testRunFlowReachesRentPanelWithoutSpending() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.staticTexts["New Job"].waitForExistence(timeout: 15))

        Phase4Draft.composeValidatedTryonJob(in: app)

        let proceed = app.buttons["newjob.continueToRun"]
        XCTAssertTrue(proceed.waitForExistence(timeout: 10))
        proceed.tap()

        let rent = app.buttons["runflow.rentWithoutPreview"]
        XCTAssertTrue(rent.waitForExistence(timeout: 20))
        XCTAssertTrue(app.buttons["runflow.previewTryon"].exists, "try-on draft makes Preview primary")
        rent.tap()

        // Either a priced Confirm or the no-stock state — both are valid live answers.
        let confirm = app.buttons["runflow.confirm"]
        let soldOut = app.descendants(matching: .any)["runflow.soldOut"]
        let deadline = Date().addingTimeInterval(150)
        while !confirm.exists && !soldOut.exists && Date() < deadline {
            _ = confirm.waitForExistence(timeout: 2)
        }
        XCTAssertTrue(confirm.exists || soldOut.exists, "rent panel must render")
        if confirm.exists {
            XCTAssertTrue(confirm.label.contains("$"), "Confirm carries its quote")
        }

        app.navigationBars.buttons.element(boundBy: 0).tap()
        Phase4Draft.clear(in: app)
    }
}
```

Add `Phase4Draft` to the same file, built from the helpers already in `Phase3SmokeTests.swift`
(`clearDraft`, `selectTryonPipeline`, `chooseMaterial`, `revealButton`, `waitUntil`): copy those
private helpers into an `enum Phase4Draft` as `@MainActor static` functions with the same bodies, and
compose them as:

```swift
    @MainActor static func composeValidatedTryonJob(in app: XCUIApplication) {
        clear(in: app)
        selectTryonPipeline(in: app)
        chooseMaterial(for: "Character", in: app)
        chooseMaterial(for: "Driver", in: app)
        chooseMaterial(for: "Outfit", in: app)
        revealButton("Validate", in: app).tap()
        XCTAssertTrue(app.staticTexts["Ready"].waitForExistence(timeout: 30))
    }

    @MainActor static func clear(in app: XCUIApplication) {
        revealButton("Clear", in: app).tap()
        XCTAssertTrue(app.staticTexts["0 of 3 required slots assigned"].waitForExistence(timeout: 10)
            || app.staticTexts["0 of 2 required slots assigned"].waitForExistence(timeout: 1))
    }
```

(The helper bodies must be copied verbatim from `Phase3SmokeTests.swift` — read that file first.)

In `Phase3SmokeTests.swift`, the forbidden-button loop still holds for `Phase A`, `Run`, `Rent`,
`Pod` (the new button is `Continue to run →`); leave it unchanged.

- [ ] **Step 7: Run the UI smoke**

Run: `make ios-ui-test`
Expected: `Phase3SmokeTests` and `Phase4SmokeTests` pass; the live draft is empty afterwards
(`GET /v1/draft` → zero slots and zero batch entries).

- [ ] **Step 8: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/RunFlow ios/MotionApp/NewJob/NewJobView.swift ios/MotionApp/Runs/RunsView.swift ios/MotionApp/Runs/RunDetailView.swift ios/MotionAppUITests/Phase4SmokeTests.swift
git commit -m "feat(ios): add the run flow screens

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Verification sweep and docs

**Files:**
- Modify: `docs/superpowers/swiftui-app-progress.md` (user's untracked file)
- Modify: `docs/superpowers/specs/2026-09-23-swiftui-app-phase-4-design.md` (Status line)
- Modify: `docs/superpowers/specs/2026-09-22-swiftui-app-design.md` (§5 row 4)
- Modify: `CLAUDE.md` and `AGENTS.md` (Commands: add `make ios-refusal-smoke`; keep in sync)
- Modify: `ios/README.md` (Phase 4 section, the launch argument, refusal smoke)

- [ ] **Step 1: Run every gate, fresh, and record the output**

```bash
make ios-test
make ios-build
make ios-contract
make ios-ui-test
motions-studio/setup/scrub-secrets.sh --check
```

`make ios-refusal-smoke` only if the user approved it in Task 8 (or approves it now).
Expected: all pass. Record the test count from `ios-test`.

- [ ] **Step 2: Update the docs**

- Phase 4 spec status → `Status: implemented (zero-spend); live spend path not yet exercised`.
- Parent design §5 row 4 → `… — implemented per [Phase 4 design](2026-09-23-swiftui-app-phase-4-design.md)`.
- Progress handoff: Phase 4 row → Complete in code, with the evidence and the explicit gap "no live
  Phase A / regen / confirm / resume has run"; the file map gains `Money/` and `RunFlow/`; gates list
  gains the new counts, `ios-refusal-smoke`; "Next work" becomes Phase 5 plus the one approved live
  spend test.
- `CLAUDE.md` / `AGENTS.md`: under the `make ios-contract` line add
  `make ios-refusal-smoke                         # live, zero-spend: bogus-token confirm/regen/resume must 409 (asks first)`.
- `ios/README.md`: Phase 4 notes — the ledger path (`Application Support/Motion/Spend`), the 20 h rule,
  `-UITestRecordingSpendGate`, and that live spend is never part of any gate.

- [ ] **Step 3: Ask before committing the user's own files**

`AGENTS.md`, `CLAUDE.md`, `ios/README.md` and `docs/superpowers/swiftui-app-progress.md` carried
uncommitted user edits at the start. Show `git diff` for them and ask whether to commit their earlier
edits together with these doc updates. Commit only what the user approves:

```bash
git add docs/superpowers/specs/2026-09-23-swiftui-app-phase-4-design.md docs/superpowers/specs/2026-09-22-swiftui-app-design.md
# plus, only if approved: AGENTS.md CLAUDE.md ios/README.md docs/superpowers/swiftui-app-progress.md
git commit -m "docs(ios): record phase 4 delivery and gates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Report**

Report mocked/simulator coverage and the live refusal smoke separately. State plainly that no live
spend path ran. Offer the one real run (Phase A + pod) only with its quoted cost, and do not start it
without an explicit yes.
