import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct StudioStoreTests {
    nonisolated static let catalog = #"""
    {"models":[
      {"key":"nano-banana-pro","label":"Nano Banana Pro","provider":"gemini","max_refs":14,"price_usd":0.134,"available":true,"default":false},
      {"key":"nano-banana-2","label":"Nano Banana 2","provider":"gemini","max_refs":14,"price_usd":0.101,"available":true,"default":true},
      {"key":"qwen-image-3","label":"Qwen Image 3.0 Pro","provider":"qwen","max_refs":3,"price_usd":0.07,"available":false,"default":false}
    ],"aspects":["16:9","4:3","1:1","3:4","9:16"],"max_count":4}
    """#

    nonisolated static func project(status: String) -> String {
        #"{"project":{"id":"p1","title":"","created_at":1,"updated_at":2,"spent_usd":0.1,"generations":[{"id":"g1","created_at":2,"prompt":"red","model":"nano-banana-2","aspect":"9:16","count":1,"refs":[{"kind":"material","id":"app/me.png","file":"g1-0.png"}],"slots":[{"status":"\#(status)"\#(status == "done" ? #","image":"g1-0""# : "")}],"status":"\#(status == "done" ? "done" : "running")","unit_price_usd":0.101,"est_cost_usd":0.101}]}}"#
    }

    final class Box: @unchecked Sendable {
        var keys: [String] = []
        var polls = 0
        var failFirstPost = false
        let lock = NSLock()
        func record(key: String?) { lock.lock(); keys.append(key ?? ""); lock.unlock() }
    }

    private func make(box: Box = Box()) -> StudioStore {
        StubURLProtocol.install { request in
            let path = request.url?.path ?? ""
            switch (request.httpMethod ?? "GET", path) {
            case ("GET", "/v1/studio/models"): return TestSupport.json(Self.catalog)
            case ("GET", "/v1/studio/projects"):
                return TestSupport.json(#"{"projects":[{"id":"p1","title":"","created_at":1,"updated_at":2,"cover":null,"spent_usd":0,"image_count":0}]}"#)
            case ("POST", "/v1/studio/projects"):
                return TestSupport.json(#"{"project":{"id":"p2","title":"","created_at":3,"updated_at":3,"spent_usd":0,"generations":[]}}"#, status: 201)
            case ("GET", "/v1/studio/projects/p1"):
                box.lock.lock(); box.polls += 1; let n = box.polls; box.lock.unlock()
                return TestSupport.json(Self.project(status: n >= 3 ? "done" : "running"))
            case ("POST", "/v1/studio/projects/p1/generations"):
                box.record(key: request.value(forHTTPHeaderField: "Idempotency-Key"))
                if box.failFirstPost && box.keys.count == 1 { return (-1, [:], Data()) }   // dropped connection
                return TestSupport.json(#"{"generation":{"id":"g2","created_at":5,"prompt":"blue","model":"nano-banana-2","aspect":"9:16","count":2,"refs":[],"slots":[{"status":"queued"},{"status":"queued"}],"status":"running","unit_price_usd":0.101,"est_cost_usd":0.202}}"#, status: 202)
            default: return (404, [:], Data())
            }
        }
        return StudioStore(client: TestSupport.client(), sleep: { _ in }, makeKey: { "key-1" }, autoPoll: false)
    }

    @Test func catalogPicksTheServerDefaultAndPrices() async {
        let store = make()
        await store.loadCatalog()
        #expect(store.modelKey == "nano-banana-2")
        store.count = 4
        #expect(abs(store.estimateUSD - 0.404) < 0.0001)
    }

    @Test func unavailableAndOverCapModelsAreDisabled() async {
        let store = make()
        await store.loadCatalog()
        let qwen = store.catalog!.models.first { $0.key == "qwen-image-3" }!
        #expect(store.disabledReason(for: qwen) != nil)
        let pro = store.catalog!.models.first { $0.key == "nano-banana-pro" }!
        #expect(store.disabledReason(for: pro) == nil)
        for i in 0..<15 { store.attach(StudioRef(kind: .material, id: "app/\(i).png")) }
        #expect(store.disabledReason(for: pro) != nil)
        #expect(!store.canSend)
    }

    @Test func attachIgnoresDuplicates() {
        let store = make()
        let ref = StudioRef(kind: .tryon, id: "abc")
        store.attach(ref); store.attach(ref)
        #expect(store.attachments == [ref])
        store.detach(ref)
        #expect(store.attachments.isEmpty)
    }

    @Test func sendRequiresAPrompt() async {
        let store = make()
        await store.loadCatalog()
        store.prompt = "   "
        #expect(!store.canSend)
    }

    @Test func generateRetriesWithSameKey() async {
        let box = Box()
        box.failFirstPost = true
        let store = make(box: box)
        await store.loadCatalog()
        await store.open("p1")
        store.prompt = "blue"
        store.count = 2
        let ok = await store.send()
        #expect(ok)
        #expect(box.keys == ["key-1", "key-1"])
        #expect(store.project?.generations.contains { $0.id == "g2" } == true)
        #expect(store.prompt.isEmpty)
    }

    @Test func pollingStopsWhenNothingRuns() async {
        let box = Box()
        let store = make(box: box)
        await store.open("p1")
        await store.pollUntilIdle()
        #expect(store.project?.generations.first?.status == .done)
        #expect(!store.hasRunning)
        #expect(box.polls == 3)
    }

    @Test func createProjectOpensIt() async {
        let store = make()
        let id = await store.createProject()
        #expect(id == "p2")
        #expect(store.project?.id == "p2")
    }

    final class RequestGate: @unchecked Sendable {
        let release = DispatchSemaphore(value: 0)
    }

    /// A send in flight (up to 3 x 60 s) must not land in whatever project
    /// happens to be open when the 202 arrives, and must not wipe a prompt
    /// the user has since typed for whatever they switched to.
    ///
    /// The switch itself is `close()`, not a second `open(_:)` — `StubURLProtocol`
    /// serialises requests through one session, so a real concurrent `open`
    /// while the POST above is held open never gets its `startLoading()` called
    /// until the POST's does, and the test hangs until `APIClient.get`'s own
    /// 30 s timeout. `close()` produces the exact same `project?.id != pid`
    /// the fix guards against, with no network involved.
    @Test func sendDuringAProjectSwitchOnlyAffectsTheProjectItWasSentFor() async {
        let gate = RequestGate()
        StubURLProtocol.install { request in
            let path = request.url?.path ?? ""
            switch (request.httpMethod ?? "GET", path) {
            case ("GET", "/v1/studio/models"): return TestSupport.json(Self.catalog)
            case ("GET", "/v1/studio/projects"):
                return TestSupport.json(#"{"projects":[]}"#)
            case ("GET", "/v1/studio/projects/p1"):
                return TestSupport.json(Self.project(status: "running"))
            case ("POST", "/v1/studio/projects/p1/generations"):
                gate.release.wait()   // held open until the test has switched away from p1
                return TestSupport.json(#"{"generation":{"id":"g2","created_at":5,"prompt":"blue","model":"nano-banana-2","aspect":"9:16","count":1,"refs":[],"slots":[{"status":"queued"}],"status":"running","unit_price_usd":0.101,"est_cost_usd":0.101}}"#, status: 202)
            default: return (404, [:], Data())
            }
        }
        let store = StudioStore(client: TestSupport.client(), sleep: { _ in }, makeKey: { "key-1" }, autoPoll: false)
        await store.loadCatalog()
        await store.open("p1")
        store.prompt = "blue"
        store.count = 1

        let sending = Task { await store.send() }
        while StubURLProtocol.requests.filter({ $0.httpMethod == "POST" }).isEmpty {
            await Task.yield()
        }
        store.close()
        store.prompt = "green"   // typed after leaving p1, while its send is still in flight
        gate.release.signal()
        let ok = await sending.value

        #expect(ok)
        #expect(store.project == nil)
        #expect(store.prompt == "green")
    }

    /// Retry must resend the failed generation's own snapshot copies, not the
    /// original `{kind, id}` — those can be gone (a pruned material, a deleted
    /// try-on library entry) by the time the user retries.
    @Test func retrySendsSnapshotRefsForTheFailedGenerationsCopies() async {
        struct SentRef: Decodable, Equatable { let kind: String; let id: String }
        struct SentBody: Decodable { let refs: [SentRef] }
        final class Captured: @unchecked Sendable {
            private let lock = NSLock()
            private var body: Data?
            func set(_ d: Data) { lock.lock(); body = d; lock.unlock() }
            func get() -> Data? { lock.lock(); defer { lock.unlock() }; return body }
        }
        let captured = Captured()
        StubURLProtocol.install { request in
            let path = request.url?.path ?? ""
            switch (request.httpMethod ?? "GET", path) {
            case ("GET", "/v1/studio/models"): return TestSupport.json(Self.catalog)
            case ("GET", "/v1/studio/projects"):
                return TestSupport.json(#"{"projects":[]}"#)
            case ("GET", "/v1/studio/projects/p1"):
                return TestSupport.json(Self.project(status: "running"))
            case ("POST", "/v1/studio/projects/p1/generations"):
                if let body = request.httpBody { captured.set(body) }
                return TestSupport.json(#"{"generation":{"id":"g3","created_at":9,"prompt":"red","model":"nano-banana-2","aspect":"9:16","count":1,"refs":[],"slots":[{"status":"queued"}],"status":"running","unit_price_usd":0.101,"est_cost_usd":0.101}}"#, status: 202)
            default: return (404, [:], Data())
            }
        }
        let store = StudioStore(client: TestSupport.client(), sleep: { _ in }, makeKey: { "key-1" }, autoPoll: false)
        await store.loadCatalog()
        await store.open("p1")
        let gen = try! #require(store.project?.generations.first)

        let ok = await store.retry(gen)

        #expect(ok)
        let body = try! #require(captured.get())
        let sent = try! JSONDecoder().decode(SentBody.self, from: body)
        #expect(sent.refs == [SentRef(kind: "snapshot", id: "p1/g1-0.png")])
    }

    struct SentCount: Decodable { let count: Int }

    final class Captured: @unchecked Sendable {
        private let lock = NSLock()
        private var bodies: [Data] = []
        func add(_ d: Data) { lock.lock(); bodies.append(d); lock.unlock() }
        var all: [Data] { lock.lock(); defer { lock.unlock() }; return bodies }
    }

    /// A project whose one generation asked for four images and got three.
    nonisolated static func partlyFailed(model: String) -> String {
        #"{"project":{"id":"p1","title":"","created_at":1,"updated_at":2,"spent_usd":0.3,"generations":[{"id":"g1","created_at":2,"prompt":"red","model":"\#(model)","aspect":"9:16","count":4,"refs":[],"slots":[{"status":"done","image":"g1-0"},{"status":"error","error":"blocked"},{"status":"done","image":"g1-2"},{"status":"done","image":"g1-3"}],"status":"done","unit_price_usd":0.101,"est_cost_usd":0.404}]}}"#
    }

    private func retryStore(model: String, captured: Captured) -> StudioStore {
        let catalog = #"""
        {"models":[
          {"key":"nano-banana-2","label":"Nano Banana 2","provider":"gemini","max_refs":14,"price_usd":0.101,"available":true,"default":true},
          {"key":"qwen-image-3","label":"Qwen Image 3.0 Pro","provider":"qwen","max_refs":3,"price_usd":0.07,"available":true,"default":false}
        ],"aspects":["9:16"],"max_count":4}
        """#
        StubURLProtocol.install { request in
            switch (request.httpMethod ?? "GET", request.url?.path ?? "") {
            case ("GET", "/v1/studio/models"): return TestSupport.json(catalog)
            case ("GET", "/v1/studio/projects"): return TestSupport.json(#"{"projects":[]}"#)
            case ("GET", "/v1/studio/projects/p1"): return TestSupport.json(Self.partlyFailed(model: model))
            case ("POST", "/v1/studio/projects/p1/generations"):
                if let body = request.httpBody { captured.add(body) }
                return TestSupport.json(#"{"generation":{"id":"g3","created_at":9,"prompt":"red","model":"nano-banana-2","aspect":"9:16","count":1,"refs":[],"slots":[{"status":"queued"}],"status":"running","unit_price_usd":0.101,"est_cost_usd":0.101}}"#, status: 202)
            default: return (404, [:], Data())
            }
        }
        return StudioStore(client: TestSupport.client(), sleep: { _ in }, makeKey: { "key-1" }, autoPoll: false)
    }

    /// Retry on one failed Gemini tile out of four re-buys one image, not four.
    @Test func retryOfAGeminiGenerationAsksOnlyForTheFailedSlots() async {
        let captured = Captured()
        let store = retryStore(model: "nano-banana-2", captured: captured)
        await store.loadCatalog()
        await store.open("p1")
        let gen = try! #require(store.project?.generations.first)
        #expect(store.retryCount(for: gen) == 1)

        let ok = await store.retry(gen)

        #expect(ok)
        let body = try! #require(captured.all.first)
        #expect(try! JSONDecoder().decode(SentCount.self, from: body).count == 1)
    }

    /// Qwen slots fail together (one call returns them all), so its retry resends the full count.
    @Test func retryOfAQwenGenerationResendsTheFullCount() async {
        let captured = Captured()
        let store = retryStore(model: "qwen-image-3", captured: captured)
        await store.loadCatalog()
        await store.open("p1")
        let gen = try! #require(store.project?.generations.first)

        let ok = await store.retry(gen)

        #expect(ok)
        let body = try! #require(captured.all.first)
        #expect(try! JSONDecoder().decode(SentCount.self, from: body).count == 4)
    }

    /// 409 `outcome_unknown`: an earlier same-key request is still being
    /// submitted. The send counts as done — Studio wording, prompt cleared,
    /// project re-read — so the user isn't invited to pay a second time.
    @Test func outcomeUnknownIsTreatedAsSubmitted() async {
        let box = Box()
        StubURLProtocol.install { request in
            switch (request.httpMethod ?? "GET", request.url?.path ?? "") {
            case ("GET", "/v1/studio/models"): return TestSupport.json(Self.catalog)
            case ("GET", "/v1/studio/projects"): return TestSupport.json(#"{"projects":[]}"#)
            case ("GET", "/v1/studio/projects/p1"):
                box.lock.lock(); box.polls += 1; box.lock.unlock()
                return TestSupport.json(Self.project(status: "running"))
            case ("POST", "/v1/studio/projects/p1/generations"):
                box.record(key: request.value(forHTTPHeaderField: "Idempotency-Key"))
                return TestSupport.json(#"{"error":{"code":"outcome_unknown","message":"An earlier request with this key is still in flight."}}"#, status: 409)
            default: return (404, [:], Data())
            }
        }
        let store = StudioStore(client: TestSupport.client(), sleep: { _ in }, makeKey: { "key-1" }, autoPoll: false)
        await store.loadCatalog()
        await store.open("p1")
        store.prompt = "blue"

        let ok = await store.send()

        #expect(ok)
        #expect(box.keys == ["key-1"])          // never resent
        #expect(box.polls == 2)                 // the project was re-read after the 409
        #expect(store.prompt.isEmpty)
        #expect(store.message?.contains("still being submitted") == true)
    }
}
}
