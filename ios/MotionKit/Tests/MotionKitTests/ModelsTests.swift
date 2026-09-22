import Foundation
import Testing
@testable import MotionKit

@Suite struct ModelsTests {
    let decoder = MotionJSON.decoder

    @Test func decodesRunList() throws {
        let r = try decoder.decode(RunsResponse.self, from: Fixtures.data(Fixtures.runs))
        #expect(r.runs.count == 3)
        #expect(r.runs[0].id == "tg-1000")
        #expect(r.runs[0].status == .running)
        #expect(r.runs[0].jobsDone == 1 && r.runs[0].jobsTotal == 3)
        #expect(r.runs[1].batch == nil)
        #expect(r.runs[1].status == .phaseA)
        #expect(r.runs[2].status == .unknown)
        #expect(r.runs[0].status.isLive && r.runs[1].status.isLive && !r.runs[2].status.isLive)
    }

    @Test func decodesRunDetail() throws {
        let d = try decoder.decode(RunDetail.self, from: Fixtures.data(Fixtures.runDetail))
        #expect(d.jobs.count == 2)
        #expect(d.jobs[1].stages.map(\.status) == [.done, .running, .unknown])
        #expect(d.jobs[0].stages[1].elapsedSec == 2040)
        #expect(d.jobs[1].stages[1].elapsedSec == nil)
        #expect(d.lease?.quotedUsdPerHr == 0.99)
        #expect(d.outputs == ["model-side__ao-dai.mp4"])
    }

    @Test func vastLeaseHasNoQuote() throws {
        let d = try decoder.decode(RunDetail.self, from: Fixtures.data(Fixtures.runDetailVast))
        #expect(d.lease?.provider == "vast")
        #expect(d.lease?.quotedUsdPerHr == nil)
    }

    @Test func decodesPod() throws {
        let live = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podLive))
        #expect(live.lease?.runId == "tg-1000")
        #expect(live.lastKill?.code == "destroy_unverified")
        #expect(live.lastKill?.ok == false)
        let idle = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podIdle))
        #expect(idle.lease == nil)
        #expect(idle.failedRental?.stockOut == true)
    }

    @Test func decodesOutputs() throws {
        let o = try decoder.decode(OutputsResponse.self, from: Fixtures.data(Fixtures.outputs))
        #expect(o.outputs[0].files.map(\.isVideo) == [true, false])
        #expect(o.outputs[0].id == "2026-09-21-0900")
    }
}
