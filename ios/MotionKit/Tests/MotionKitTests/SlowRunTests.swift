import Foundation
import Testing
@testable import MotionKit

@Suite struct SlowRunTests {
    private func run(_ id: String, slow: Int?) -> RunSummary {
        RunSummary(id: id, batch: nil, status: .running, updatedAt: 1, jobsTotal: 1, jobsDone: 0, slowStages: slow)
    }

    @Test func aStageDecodesItsWarning() throws {
        let json = #"""
        {"name": "camera-motion", "status": "running", "elapsed_sec": null,
         "slow_warning": {"at": 1790741000.0, "ceiling_min": 60, "gpu": "slow",
                          "detail": "median SM clock 7% of max, median power 58 W"}}
        """#
        let stage = try MotionJSON.decoder.decode(StageProgress.self, from: Data(json.utf8))
        #expect(stage.slowWarning?.isThrottled == true)
        #expect(stage.slowWarning?.detail.contains("7%") == true)
    }

    @Test func anOlderServerSendsNoWarning() throws {
        let json = #"{"name": "motion", "status": "running", "elapsed_sec": null}"#
        let stage = try MotionJSON.decoder.decode(StageProgress.self, from: Data(json.utf8))
        #expect(stage.slowWarning == nil)
    }

    @Test func aWarningThatIsNotThrottleIsNotCalledOne() throws {
        let json = #"{"gpu": "ok", "detail": "median SM clock 91% of max"}"#
        let warning = try MotionJSON.decoder.decode(SlowWarning.self, from: Data(json.utf8))
        #expect(!warning.isThrottled)
    }

    @Test func notifiesWhenTheCountRisesNotWhenItStays() {
        #expect(SlowRunNotice.newlySlow(before: ["a": 0], after: [run("a", slow: 1)]).map(\.runID) == ["a"])
        #expect(SlowRunNotice.newlySlow(before: ["a": 1], after: [run("a", slow: 1)]).isEmpty)
        #expect(SlowRunNotice.newlySlow(before: ["a": 1], after: [run("a", slow: 0)]).isEmpty)
        #expect(SlowRunNotice.newlySlow(before: [:], after: [run("a", slow: nil)]).isEmpty)
    }

    @Test func aFirstSightingOfASlowRunNotifiesOnce() {
        #expect(SlowRunNotice.newlySlow(before: [:], after: [run("a", slow: 2)]).count == 1)
    }
}
