import Foundation
import Testing
@testable import MotionKit

@Suite struct RunPagesTests {
    private func summary(status: String, total: Int, done: Int) throws -> RunSummary {
        let json = #"{"id":"tg-1","batch":"b","status":"\#(status)","updated_at":1,"jobs_total":\#(total),"jobs_done":\#(done)}"#
        return try MotionJSON.decoder.decode(RunSummary.self, from: Data(json.utf8))
    }

    private func tryon(_ statuses: [String], run: String = "tg-1") throws -> TryonPreviews {
        let previews = statuses.enumerated().map { i, s in
            #"{"index":"\#(i)","run":"job\#(i)","status":"\#(s)","has_image":\#(s == "done")}"#
        }.joined(separator: ",")
        let json = #"{"run_id":"\#(run)","run_token":"t","phase_a_running":true,"previews":[\#(previews)]}"#
        return try MotionJSON.decoder.decode(TryonPreviews.self, from: Data(json.utf8))
    }

    /// Phase A writes no journal entries, so the run list says 0 of 0 while
    /// the VPS is busy on looks (2026-09-26). The previews are the real count.
    @Test func phaseACountsLooksFromThePreviews() throws {
        let run = try summary(status: "phase_a", total: 0, done: 0)
        let p = LiveProgress(run: run, tryon: try tryon(["done", "running", "pending"]))
        #expect(p == LiveProgress(done: 1, total: 3, unit: .looks))
    }

    @Test func phaseAWithoutPreviewsHasNoCount() throws {
        let run = try summary(status: "phase_a", total: 0, done: 0)
        #expect(LiveProgress(run: run, tryon: nil).total == 0)
        #expect(LiveProgress(run: run, tryon: try tryon(["done"], run: "other")).total == 0)
    }

    @Test func aDrainCountsJobs() throws {
        let run = try summary(status: "running", total: 3, done: 1)
        #expect(LiveProgress(run: run, tryon: try tryon(["done"])) == LiveProgress(done: 1, total: 3, unit: .jobs))
    }

    @Test func outputsMatchTheirJobIncludingReruns() throws {
        let json = #"{"id":"r","batch":"b","status":"done","updated_at":1,"jobs_total":2,"jobs_done":2,"jobs":[],"lease":null,"outputs":["a-b-c.mp4","a-b-c-2.mp4","a-b-cd.mp4","a-b-c-x.mp4"]}"#
        let detail = try MotionJSON.decoder.decode(RunDetail.self, from: Data(json.utf8))
        #expect(detail.outputs(forJob: "a-b-c") == ["a-b-c-2.mp4", "a-b-c.mp4"])
        #expect(detail.outputs(forJob: "a-b-cd") == ["a-b-cd.mp4"])
    }
}

extension URLProtocolTests {
    @Suite @MainActor struct RunDetailPagesTests {
        @Test func detailStoreFindsTheJobsTryonImage() async {
            StubURLProtocol.install { req in
                switch req.url?.path {
                case "/v1/runs/tg-1000/tryon":
                    TestSupport.json(#"{"run_id":"tg-1000","run_token":"t","phase_a_running":false,"previews":[{"index":"0","run":"model-side__ao-dai","status":"done","has_image":true},{"index":"1","run":"model-side__blazer","status":"error","has_image":false}]}"#)
                case "/v1/runs/tg-1000/tryon/0":
                    (200, ["Content-Type": "image/png"], Data([7]))
                default:
                    (404, [:], Data())
                }
            }
            let store = RunDetailStore(client: TestSupport.client(), runID: "tg-1000")
            #expect(await store.tryonImage(forJob: "model-side__ao-dai") == Data([7]))
            #expect(await store.tryonImage(forJob: "model-side__blazer") == nil)
            #expect(await store.tryonImage(forJob: "model-side__ao-dai") == Data([7]))
            #expect(StubURLProtocol.requests.filter { $0.url?.path == "/v1/runs/tg-1000/tryon" }.count == 1)
            #expect(StubURLProtocol.requests.filter { $0.url?.path == "/v1/runs/tg-1000/tryon/0" }.count == 1)
        }
    }
}
