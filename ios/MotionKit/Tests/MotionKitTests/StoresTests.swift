import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct StoresTests {
    @Test func runsStoreSplitsLiveFromRecent() async {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.runs) }
        let store = RunsStore(client: TestSupport.client())
        await store.refresh()
        #expect(store.loaded)
        #expect(store.live?.id == "tg-1000")
        // live = the FIRST live run (tg-1000); old-run is also live (phase_a) but not first.
        #expect(store.recent.map(\.id) == ["old-run", "weird"])
        #expect(store.error == nil && store.lastSuccess != nil && !store.isStale)
    }

    @Test func failedRefreshKeepsDataAndMarksStale() async {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.runs) }
        let store = RunsStore(client: TestSupport.client())
        await store.refresh()
        StubURLProtocol.install { _ in (502, [:], Data("down".utf8)) }
        await store.refresh()
        #expect(store.runs.count == 3)
        #expect(store.isStale)
        #expect(store.error == .server(status: 502, code: "http_502", message: "down"))
    }

    @Test func detail304KeepsPreviousDetail() async {
        StubURLProtocol.install { req in
            req.value(forHTTPHeaderField: "If-None-Match") == nil
                ? TestSupport.json(Fixtures.runDetail, etag: #""e1""#)
                : (304, [:], Data())
        }
        let store = RunDetailStore(client: TestSupport.client(), runID: "tg-1000")
        await store.refresh()
        await store.refresh()
        #expect(store.detail?.id == "tg-1000")
        #expect(store.error == nil)
    }

    @Test func pollRefreshesUntilCancelled() async {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.runDetail) }
        let store = RunDetailStore(client: TestSupport.client(), runID: "tg-1000")
        let sleeps = Counter()
        await store.poll(interval: .seconds(5)) { interval in
            #expect(interval == .seconds(5))
            if sleeps.increment() >= 2 { throw CancellationError() }
        }
        #expect(StubURLProtocol.requests.count == 2)
        #expect(StubURLProtocol.requests.allSatisfy { $0.url?.path == "/v1/runs/tg-1000" })
    }

    @Test func podAndOutputsStoresLoad() async {
        StubURLProtocol.install { req in
            req.url?.path == "/v1/pod" ? TestSupport.json(Fixtures.podLive) : TestSupport.json(Fixtures.outputs)
        }
        let pod = PodStore(client: TestSupport.client())
        await pod.refresh()
        #expect(pod.pod?.lease?.provider == "runpod")
        let outputs = OutputsStore(client: TestSupport.client())
        await outputs.refresh()
        #expect(outputs.batches.first?.files.count == 2)
        #expect(outputs.loaded)
    }
}
}

final class Counter: @unchecked Sendable {
    private let lock = NSLock()
    private var n = 0
    func increment() -> Int { lock.withLock { n += 1; return n } }
}
