import Foundation
import Testing
@testable import MotionKit

@Suite struct RunDigestTests {
    private func detail(_ jobs: [(String, String?)]) throws -> RunDetail {
        let rows = jobs.enumerated().map { i, job in
            let setup = job.1.map { #","setup":{"pipeline":"\#($0)","provider":"qwen-max","inputs":{}}"# } ?? ""
            return #"{"id":"j\#(i)","status":"\#(job.0)","stages":[]\#(setup)}"#
        }.joined(separator: ",")
        let json = #"{"id":"r","batch":"b","status":"stopped","updated_at":1,"jobs_total":\#(jobs.count),"jobs_done":0,"jobs":[\#(rows)],"lease":null,"outputs":[]}"#
        return try MotionJSON.decoder.decode(RunDetail.self, from: Data(json.utf8))
    }

    @Test func countsEachStatusAndLeavesZerosOut() throws {
        let d = RunDigest(try detail([("done", nil), ("error", nil), ("pending", nil), ("None", nil)]))
        #expect((d.done, d.failed, d.running, d.left) == (1, 1, 0, 2))
        #expect(d.countsText == "4 jobs · 1 done · 1 failed · 2 left")
        #expect(RunDigest(try detail([])).countsText == "No jobs")
    }

    @Test func setupNamesTheOnePipelineAndProvider() throws {
        let one = RunDigest(try detail([("pending", "tryon-character-swap-enhance"), ("pending", "tryon-character-swap-enhance")]))
        #expect(one.setupText == "Try-on → Swap → Enhance · qwen-max")
        let two = RunDigest(try detail([("pending", "motion-enhance"), ("pending", "tryon-motion-enhance")]))
        #expect(two.setupText == "2 pipelines · qwen-max")
        #expect(RunDigest(try detail([("pending", nil)])).setupText == nil)
    }

    @Test func pipelineStepsJoinTwoWordStages() {
        #expect(PipelineName.steps("tryon-camera-motion-enhance") == "Try-on → Camera motion → Enhance")
        #expect(PipelineName.steps("character-swap") == "Swap")
        #expect(PipelineName.steps("character") == "Character")
    }

    @Test func dateNamedRunsReadAsDates() {
        var utc = Calendar(identifier: .gregorian)
        utc.timeZone = .gmt
        let year = utc.component(.year, from: .now)
        #expect(RunName.title("\(year)-09-26-1530", calendar: utc) == "Sep 26 · 15:30")
        #expect(RunName.title("2019-01-02-0905", calendar: utc) == "Jan 2, 2019 · 09:05")
        #expect(RunName.title("tg-1959705051") == "tg-1959705051")
        #expect(RunName.title("2026-13-01-1530") == "2026-13-01-1530")
    }
}
