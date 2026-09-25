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
}
}
