import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct GpuSubsStoreTests {
    nonisolated static let listing = #"""
    {"subs": [{"id": "abc123", "gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090",
               "datacenter": "EU-RO-1", "created_at": 1.0,
               "auto_resume": {"run_id": "tg-1", "max_usd_per_hr": 0.99}}],
     "fired": [{"sub_id": "old001", "gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090",
                "datacenter": "EU-RO-1", "stock": "Low", "usd_per_hr": 0.69,
                "fired_at": 100.0, "action": "notified", "reason": null}]}
    """#

    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _listing = GpuSubsStoreTests.listing
        private var _post = TestSupport.json(#"{"sub": {"id": "new001", "gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090", "datacenter": "EU-CZ-1", "created_at": 2.0, "auto_resume": null}}"#, status: 201)
        var listing: String { get { lock.withLock { _listing } } set { lock.withLock { _listing = newValue } } }
        var post: (Int, [String: String], Data) { get { lock.withLock { _post } } set { lock.withLock { _post = newValue } } }
        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            switch (request.httpMethod ?? "GET", request.url?.path ?? "") {
            case ("GET", "/v1/gpu/subs"): return TestSupport.json(listing)
            case ("POST", "/v1/gpu/subs"): return post
            case ("DELETE", "/v1/gpu/subs/abc123"): return TestSupport.json(#"{"subs": []}"#)
            default: return (404, [:], Data())
            }
        }
    }

    private func store(_ routes: Routes, defaults: UserDefaults? = nil) -> (GpuSubsStore, UserDefaults) {
        StubURLProtocol.install { routes.answer($0) }
        let d = defaults ?? UserDefaults(suiteName: "gpusubs-\(UUID().uuidString)")!
        return (GpuSubsStore(client: TestSupport.client(), defaults: d), d)
    }

    @Test func loadsSubsAndFirings() async {
        let (store, _) = store(Routes())
        await store.load()
        #expect(store.subs.map(\.id) == ["abc123"])
        #expect(store.armed?.autoResume?.maxUsdPerHr == 0.99)
        #expect(store.watching(gpu: "NVIDIA GeForce RTX 5090").count == 1)
        #expect(store.fired.first?.subId == "old001")
    }

    @Test func firstLoadMarksExistingFiringsSeen() async {
        let (store, _) = store(Routes())
        await store.load()
        #expect(store.unseen.isEmpty)
    }

    @Test func aNewerFiringIsUnseenUntilMarked() async {
        let routes = Routes()
        let (store, defaults) = store(routes)
        await store.load()
        routes.listing = GpuSubsStoreTests.listing.replacingOccurrences(of: "\"fired_at\": 100.0", with: "\"fired_at\": 200.0")
        await store.load()
        #expect(store.unseen.count == 1)
        store.markSeen()
        #expect(store.unseen.isEmpty)
        let (again, _) = self.store(routes, defaults: defaults)
        await again.load()
        #expect(again.unseen.isEmpty)
    }

    @Test func anEmptyFirstLoadStillShowsTheFirstFiring() async {
        // markSeen with nothing fired must store 0, not the phone's clock: a
        // phone ahead of the VPS would otherwise hide the next firing.
        let routes = Routes()
        routes.listing = #"{"subs": [], "fired": []}"#
        let (store, defaults) = store(routes)
        await store.load()
        #expect(defaults.double(forKey: GpuSubsStore.seenKey) == 0)
        routes.listing = GpuSubsStoreTests.listing
        await store.load()
        #expect(store.unseen.map(\.subId) == ["old001"])
    }

    @Test func watchPostsSnakeCaseThenReloads() async throws {
        let (store, _) = store(Routes())
        let ok = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1", autoResumeRunID: nil)
        #expect(ok)
        let post = try #require(StubURLProtocol.requests.first { $0.httpMethod == "POST" })
        let body = try JSONSerialization.jsonObject(with: post.httpBody ?? Data()) as? [String: Any]
        #expect(body?["auto_resume"] as? Bool == false)
        #expect(body?["run_id"] == nil)
        #expect(StubURLProtocol.requests.last?.url?.path == "/v1/gpu/subs")
        #expect(StubURLProtocol.requests.last?.httpMethod == "GET")
    }

    @Test func armingSendsTheRunID() async throws {
        let (store, _) = store(Routes())
        _ = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-RO-1", autoResumeRunID: "tg-1")
        let post = try #require(StubURLProtocol.requests.first { $0.httpMethod == "POST" })
        let body = try JSONSerialization.jsonObject(with: post.httpBody ?? Data()) as? [String: Any]
        #expect(body?["auto_resume"] as? Bool == true)
        #expect(body?["run_id"] as? String == "tg-1")
    }

    @Test func aRefusedArmShowsTheServerMessage() async {
        let routes = Routes()
        routes.post = TestSupport.json(#"{"error": {"code": "not_home_dc", "message": "auto-resume only rents in the volume's home datacenter"}}"#, status: 409)
        let (store, _) = store(routes)
        let ok = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1", autoResumeRunID: "tg-1")
        #expect(!ok)
        #expect(store.message?.contains("home datacenter") == true)
        #expect(store.inFlight.isEmpty)
    }

    @Test func unwatchRemovesLocally() async {
        let (store, _) = store(Routes())
        await store.load()
        await store.unwatch(store.subs[0])
        #expect(store.subs.isEmpty)
    }
}
}
