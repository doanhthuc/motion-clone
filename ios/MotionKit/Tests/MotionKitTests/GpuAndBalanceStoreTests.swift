import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct GpuAndBalanceStoreTests {
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _stock = TestSupport.json(Fixtures.gpuStock)
        private var _put = TestSupport.json(#"{"gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090"}"#)
        private var _balance = Fixtures.balance
        private var _vast = Fixtures.balanceVast
        var stock: (Int, [String: String], Data) {
            get { lock.withLock { _stock } } set { lock.withLock { _stock = newValue } }
        }
        var put: (Int, [String: String], Data) {
            get { lock.withLock { _put } } set { lock.withLock { _put = newValue } }
        }
        var balance: String { get { lock.withLock { _balance } } set { lock.withLock { _balance = newValue } } }
        var vast: String { get { lock.withLock { _vast } } set { lock.withLock { _vast = newValue } } }

        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            switch (request.httpMethod ?? "GET", request.url?.path ?? "") {
            case ("GET", "/v1/gpu/stock"): return stock
            case ("PUT", "/v1/pod/gpu"): return put
            case ("GET", "/v1/pod"): return TestSupport.json(Fixtures.podIdle)
            case ("GET", "/v1/balance"):
                return TestSupport.json(request.url?.query == "vast=1" ? vast : balance)
            default: return (404, [:], Data())
            }
        }
    }

    private func install(_ routes: Routes) { StubURLProtocol.install { routes.answer($0) } }

    private func gpuStore(_ routes: Routes) -> (GpuStore, PodStore) {
        install(routes)
        let pod = PodStore(client: TestSupport.client())
        return (GpuStore(client: TestSupport.client(), pod: pod), pod)
    }

    // MARK: GPU

    @Test func stockLoadsCachedAndForceAsksForFresh() async {
        let (store, _) = gpuStore(Routes())
        await store.load()
        #expect(store.stock?.gpus.count == 5)
        #expect(StubURLProtocol.requests.last?.url?.query == nil)
        await store.load(force: true)
        #expect(StubURLProtocol.requests.last?.url?.query == "force=1")
    }

    @Test func unreachableRunpodctlKeepsTheLastStock() async {
        let routes = Routes()
        let (store, _) = gpuStore(routes)
        await store.load()
        routes.stock = TestSupport.json(
            #"{"error": {"code": "upstream_unavailable", "message": "couldn't reach runpodctl: timeout"}}"#, status: 502)
        await store.load(force: true)
        #expect(store.stock?.gpus.count == 5)
        #expect(store.isStale)
    }

    @Test func selectPutsTheCatalogIdThenRefreshesThePod() async throws {
        let (store, pod) = gpuStore(Routes())
        await store.load()
        let changed = await store.select("NVIDIA GeForce RTX 4090", whileSpending: false)
        #expect(changed)
        #expect(store.stock?.selected == "NVIDIA GeForce RTX 4090")
        let requests = StubURLProtocol.requests
        let putIndex = try #require(requests.firstIndex { $0.httpMethod == "PUT" })
        let body = try #require(JSONSerialization.jsonObject(with: requests[putIndex].httpBody ?? Data()) as? [String: String])
        #expect(body == ["gpu": "NVIDIA GeForce RTX 4090"])
        let podIndex = try #require(requests.lastIndex { $0.url?.path == "/v1/pod" })
        #expect(podIndex > putIndex)
        #expect(pod.pod != nil)
    }

    @Test func selectIsRefusedWhileASpendIsInFlight() async {
        let (store, _) = gpuStore(Routes())
        await store.load()
        let changed = await store.select("NVIDIA GeForce RTX 4090", whileSpending: true)
        #expect(!changed)
        #expect(store.message?.contains("in flight") == true)
        #expect(!StubURLProtocol.requests.contains { $0.httpMethod == "PUT" })
    }

    @Test func selectingTheCurrentGpuSendsNothing() async {
        let (store, _) = gpuStore(Routes())
        await store.load()
        let changed = await store.select("NVIDIA GeForce RTX 5090", whileSpending: false)
        #expect(!changed)
        #expect(!StubURLProtocol.requests.contains { $0.httpMethod == "PUT" })
    }

    @Test func busyBotKeepsTheSelectionAndSaysSo() async {
        let routes = Routes()
        routes.put = TestSupport.json(#"{"error": {"code": "bot_busy", "message": "the bot is busy"}}"#, status: 503)
        let (store, _) = gpuStore(routes)
        await store.load()
        let changed = await store.select("NVIDIA GeForce RTX 4090", whileSpending: false)
        #expect(!changed)
        #expect(store.message == "The bot is busy. Try again in a moment.")
        #expect(store.stock?.selected == "NVIDIA GeForce RTX 5090")
    }

    // MARK: balance

    @Test func balanceNeverAsksForVastUnlessTapped() async {
        install(Routes())
        let store = BalanceStore(client: TestSupport.client())
        #expect(store.runpodLine == nil)
        await store.load()
        #expect(StubURLProtocol.requests.last?.url?.query == nil)
        #expect(store.runpodLine == "$12.34 · ≈ 12h 28m at $0.99/h")
        #expect(store.vastLine == nil)
    }

    @Test func unreadableRunpodIsNeverZero() async {
        let routes = Routes()
        routes.balance = Fixtures.balanceRunpodDown
        install(routes)
        let store = BalanceStore(client: TestSupport.client())
        await store.load()
        #expect(store.runpodLine == "Couldn't read the RunPod balance")
        #expect(store.balance?.errors == ["couldn't reach runpodctl: timeout"])
    }

    @Test func vastCreditOnlyOnRequestAndKeptAcrossReloads() async {
        install(Routes())
        let store = BalanceStore(client: TestSupport.client())
        await store.loadVast()
        #expect(StubURLProtocol.requests.last?.url?.query == "vast=1")
        #expect(store.vastLine == "$7.50")
        await store.load()
        #expect(store.vastLine == "$7.50")
    }

    @Test func unreadableVastIsNeverZero() async {
        let routes = Routes()
        routes.vast = Fixtures.balanceVastDown
        install(routes)
        let store = BalanceStore(client: TestSupport.client())
        await store.loadVast()
        #expect(store.vastLine == "Couldn't read the Vast credit")
        #expect(store.balance?.runpod?.lowRunway == true)
    }
}
}
