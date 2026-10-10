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
        private var _listingStatus = 200
        private var _post = TestSupport.json(#"{"sub": {"id": "new001", "gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090", "datacenter": "EU-CZ-1", "created_at": 2.0, "auto_resume": null}}"#, status: 201)
        var listing: String { get { lock.withLock { _listing } } set { lock.withLock { _listing = newValue } } }
        var listingStatus: Int { get { lock.withLock { _listingStatus } } set { lock.withLock { _listingStatus = newValue } } }
        var post: (Int, [String: String], Data) { get { lock.withLock { _post } } set { lock.withLock { _post = newValue } } }
        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            switch (request.httpMethod ?? "GET", request.url?.path ?? "") {
            case ("GET", "/v1/gpu/subs"):
                // A pre-deploy server answers its generic JSON 404 here.
                return listingStatus == 404
                    ? TestSupport.json(#"{"error": {"code": "not_found", "message": "no such route"}}"#, status: 404)
                    : TestSupport.json(listing)
            case ("POST", "/v1/gpu/subs"): return post
            case ("DELETE", "/v1/gpu/subs/abc123"): return TestSupport.json(#"{"subs": []}"#)
            case ("DELETE", "/v1/gpu/subs/gone01"):
                return TestSupport.json(#"{"error": {"code": "busy", "message": "try again in a moment"}}"#, status: 409)
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

    /// The 2026-10-10 bug: a firing whose banner auto-hid came back on every
    /// launch, because only Dismiss wrote anything down.
    @Test func aBanneredFiringStaysNewInTheDrawerButNeverBannersAgain() async throws {
        let routes = Routes()
        let (store, defaults) = store(routes)
        await store.load()
        routes.listing = GpuSubsStoreTests.listing.replacingOccurrences(of: "\"fired_at\": 100.0", with: "\"fired_at\": 200.0")
        await store.load()
        let firing = try #require(store.bannerFiring)
        store.markBannered(firing)
        #expect(store.bannerFiring == nil)
        #expect(store.unseen.count == 1)
        let (relaunched, _) = self.store(routes, defaults: defaults)
        await relaunched.load()
        #expect(relaunched.bannerFiring == nil)
        #expect(relaunched.unseen.count == 1)
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
        #expect(store.message(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1")?.contains("home datacenter") == true)
        #expect(store.inFlight.isEmpty)
    }

    @Test func messagesStayWithTheirOwnPair() async {
        // The GPU sheet and the run's retry card each show one pair; neither
        // may show the other's refusal (follow-up, 2026-09-27).
        let routes = Routes()
        routes.post = TestSupport.json(#"{"error": {"code": "not_home_dc", "message": "auto-resume only rents in the volume's home datacenter"}}"#, status: 409)
        let (store, _) = store(routes)
        await store.load()
        _ = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1", autoResumeRunID: "tg-1")
        #expect(store.message(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1") != nil)
        #expect(store.message(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-RO-1") == nil)
        #expect(store.message(gpu: "NVIDIA GeForce RTX 4090", datacenter: "EU-CZ-1") == nil)

        let gone = GpuSub(id: "gone01", gpu: "NVIDIA GeForce RTX 4090", name: "RTX 4090",
                          datacenter: "EU-RO-1", createdAt: 1, autoResume: nil)
        await store.unwatch(gone)
        #expect(store.message(gpu: "NVIDIA GeForce RTX 4090", datacenter: "EU-RO-1")?.contains("try again") == true)

        store.dismissMessage(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1")
        #expect(store.message(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1") == nil)
        #expect(store.message(gpu: "NVIDIA GeForce RTX 4090", datacenter: "EU-RO-1") != nil)
    }

    @Test func aSuccessfulWatchClearsOnlyItsOwnMessage() async {
        let routes = Routes()
        routes.post = TestSupport.json(#"{"error": {"code": "not_home_dc", "message": "nope"}}"#, status: 409)
        let (store, _) = store(routes)
        _ = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1", autoResumeRunID: "tg-1")
        _ = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-RO-1", autoResumeRunID: "tg-1")
        routes.post = TestSupport.json(#"{"sub": {"id": "new001", "gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090", "datacenter": "EU-RO-1", "created_at": 2.0, "auto_resume": null}}"#, status: 201)
        _ = await store.watch(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-RO-1", autoResumeRunID: nil)
        #expect(store.message(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-RO-1") == nil)
        #expect(store.message(gpu: "NVIDIA GeForce RTX 5090", datacenter: "EU-CZ-1") != nil)
    }

    @Test func aMissingRouteMarksTheServerUnsupported() async {
        // Pre-deploy the VPS has no /v1/gpu/subs: the app hides the bell,
        // bolt and "Resume when in stock" rather than offer buttons that
        // can only 404 (follow-up, 2026-09-27).
        let routes = Routes()
        routes.listingStatus = 404
        let (store, _) = store(routes)
        await store.load()
        #expect(store.unsupported)
        routes.listingStatus = 200
        await store.load()
        #expect(!store.unsupported)
        #expect(store.subs.map(\.id) == ["abc123"])
    }

    @Test func aServerErrorDoesNotClaimUnsupported() async {
        // Only a 404 means "route missing"; a 500 is a transient failure.
        StubURLProtocol.install { _ in TestSupport.json(#"{"error": {"code": "internal", "message": "x"}}"#, status: 500) }
        let store = GpuSubsStore(client: TestSupport.client(),
                                 defaults: UserDefaults(suiteName: "gpusubs-\(UUID().uuidString)")!)
        await store.load()
        #expect(!store.unsupported)
        #expect(store.error != nil)
    }

    @Test func unwatchRemovesLocally() async {
        let (store, _) = store(Routes())
        await store.load()
        await store.unwatch(store.subs[0])
        #expect(store.subs.isEmpty)
    }
}
}
