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

    @Test func bulkRunDeleteCountsGoneRunsAndKeepsRefusedOnes() async {
        StubURLProtocol.install { req in
            guard req.httpMethod == "DELETE" else { return TestSupport.json(Fixtures.runs) }
            switch req.url?.path {
            case "/v1/runs/old-run": return TestSupport.json(#"{"deleted":"old-run","videos_deleted":2}"#)
            case "/v1/runs/weird": return TestSupport.json(#"{"error":{"code":"not_found","message":"no run"}}"#, status: 404)
            default: return TestSupport.json(#"{"error":{"code":"run_live","message":"running"}}"#, status: 409)
            }
        }
        let store = RunsStore(client: TestSupport.client())
        await store.refresh()
        let result = await store.delete(["old-run", "weird", "tg-1000"], withVideos: true)
        #expect(result.deleted == 2)
        #expect(result.videosDeleted == 2)
        #expect(result.failed.map(\.id) == ["tg-1000"])
        #expect(store.runs.map(\.id) == ["tg-1000"])
    }

    @Test func outputDeleteDropsFilesAndEmptyBatchesAndKeepsRefusals() async {
        let list = #"{"outputs":[{"batch":"b2","updated_at":2,"files":[{"name":"a.mp4","bytes":1},{"name":"b.mp4","bytes":1}]},{"batch":"b1","updated_at":1,"files":[{"name":"a.mp4","bytes":1}]}]}"#
        StubURLProtocol.install { req in
            guard req.httpMethod == "DELETE" else { return TestSupport.json(list) }
            switch req.url?.path {
            case "/v1/outputs/b2/b.mp4": return TestSupport.json(#"{"error":{"code":"busy","message":"a run is still writing this batch"}}"#, status: 409)
            case "/v1/outputs/b1/a.mp4": return TestSupport.json(#"{"error":{"code":"not_found","message":"no such output"}}"#, status: 404)
            default: return (204, [:], Data())
            }
        }
        let store = OutputsStore(client: TestSupport.client())
        await store.refresh()
        let kept = await store.delete(["b2/a.mp4", "b2/b.mp4", "b1/a.mp4"])
        #expect(kept == ["b2/b.mp4"])
        #expect(store.batches.map(\.batch) == ["b2"])
        #expect(store.batches.first?.files.map(\.name) == ["b.mp4"])
        #expect(store.message?.hasPrefix("Couldn't delete 1 of 3: ") == true)
    }

    @Test func outputDeleteWorksBeforeTheListLoads() async {
        StubURLProtocol.install { _ in (204, [:], Data()) }
        let store = OutputsStore(client: TestSupport.client())
        let kept = await store.delete(["2026-10-08-0857/a-2.mp4"])
        #expect(kept.isEmpty)
        #expect(StubURLProtocol.requests.last?.httpMethod == "DELETE")
        #expect(StubURLProtocol.requests.last?.url?.path == "/v1/outputs/2026-10-08-0857/a-2.mp4")
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

    /// Back out of a run and open it again: the new store starts empty, but the
    /// shared client still holds the first visit's ETag. A 304 then left the
    /// screen on its loader forever (2026-09-26).
    @Test func freshStoreIgnoresTheClientsCachedETag() async {
        StubURLProtocol.install { req in
            req.value(forHTTPHeaderField: "If-None-Match") == nil
                ? TestSupport.json(Fixtures.runDetail, etag: #""e1""#)
                : (304, [:], Data())
        }
        let client = TestSupport.client()
        await RunDetailStore(client: client, runID: "tg-1000").refresh()
        let reopened = RunDetailStore(client: client, runID: "tg-1000")
        await reopened.refresh()
        #expect(reopened.detail?.id == "tg-1000")
    }

    /// Two stores on one run: the Runs card's and the run screen's. The card
    /// saw the run go live; the screen, holding the older "stopped" copy, sent
    /// the card's ETag, got 304 and stayed "Stopped" under a live pod
    /// (2026-09-27). Each store must revalidate its own copy.
    @Test func aStoreRevalidatesItsOwnCopyNotAnotherStores() async {
        let live = Counter()
        StubURLProtocol.install { req in
            let body = live.value == 0
                ? Fixtures.runDetail.replacingOccurrences(of: #""status": "running""#, with: #""status": "stopped""#)
                : Fixtures.runDetail
            let tag = live.value == 0 ? #""stopped""# : #""running""#
            return req.value(forHTTPHeaderField: "If-None-Match") == tag
                ? (304, ["ETag": tag], Data())
                : TestSupport.json(body, etag: tag)
        }
        let client = TestSupport.client()
        let screen = RunDetailStore(client: client, runID: "tg-1000")
        await screen.refresh()
        #expect(screen.detail?.status == .stopped)

        _ = live.increment()   // the drain rents a pod
        let card = RunDetailStore(client: client, runID: "tg-1000")
        await card.refresh()
        #expect(card.detail?.status == .running)

        await screen.refresh()
        #expect(screen.detail?.status == .running)
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
    var value: Int { lock.withLock { n } }
}
