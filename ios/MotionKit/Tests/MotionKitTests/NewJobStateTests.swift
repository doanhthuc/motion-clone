import Foundation
import Testing
@testable import MotionKit

@Suite struct NewJobStateTests {
    private let catalog = try! MotionJSON.decoder.decode(
        PipelineCatalogResponse.self, from: Fixtures.data(Fixtures.pipelines)).pipelines
    private var tryon: Pipeline { catalog[1] }       // character, driver, outfit + background
    private var motion: Pipeline { catalog[0] }      // character, driver

    /// A draft on `pipeline` whose filled slots are `filled`, `jobs` counted by the server.
    private func draft(_ pipeline: Pipeline, filled: [String: String], jobs: Int = 0) -> Draft {
        let probe = #"{"kind":"image","width":1,"height":1,"duration_s":null,"bitrate_kbps":null,"size_bytes":1,"warning":""}"#
        let slots = filled.map { #""\#($0.key)":{"material_id":"\#($0.value)","name":"\#($0.value)","exists":true,"probe":\#(probe),"warning":""}"# }
            .joined(separator: ",")
        let missing = pipeline.required.filter { filled[$0] == nil }.map { #""\#($0)""# }.joined(separator: ",")
        let required = pipeline.required.map { #""\#($0)""# }.joined(separator: ",")
        let optional = pipeline.optional.map { #""\#($0)""# }.joined(separator: ",")
        let json = #"{"owner":"app","pipeline":"\#(pipeline.id)","provider":"gemini","generation":1,"slots":{\#(slots)},"required":[\#(required)],"optional":[\#(optional)],"missing":[\#(missing)],"validated":null,"batch":[],"jobs":\#(jobs),"estimate_min":null}"#
        return try! MotionJSON.decoder.decode(Draft.self, from: Data(json.utf8))
    }

    @Test func aTryonPipelineCrossesOutfitAndDriver() {
        let s = NewJobState(pipeline: tryon, draft: draft(tryon, filled: [:]), outfits: 0, drivers: 0)
        #expect(s.isBatch)
        #expect(s.cards == [.single("character"), .drivers, .outfits, .single("background")])
        #expect(s.missing == ["character", "driver", "outfit"])
        #expect(s.addCount == 0 && !s.canContinue && s.isFresh)
    }

    @Test func aPipelineWithoutAnOutfitKeepsEveryCardSingle() {
        let s = NewJobState(pipeline: motion, draft: draft(motion, filled: ["character": "c"]), outfits: 0, drivers: 0)
        #expect(!s.isBatch)
        #expect(s.cards == [.single("character"), .single("driver")])
        #expect(s.missing == ["driver"])
        #expect(!s.isFresh)
    }

    @Test func addCountIsOutfitsTimesDriversOnceNothingIsMissing() {
        let d = draft(tryon, filled: ["character": "c"])
        #expect(NewJobState(pipeline: tryon, draft: d, outfits: 3, drivers: 0).addCount == 0)  // driver missing
        let s = NewJobState(pipeline: tryon, draft: d, outfits: 3, drivers: 2)
        #expect(s.missing.isEmpty)
        #expect(s.addCount == 6 && s.canContinue)
    }

    @Test func aSharedDriverOnTheDraftCountsAsFilled() {
        let d = draft(tryon, filled: ["character": "c", "driver": "d"])
        let s = NewJobState(pipeline: tryon, draft: d, outfits: 2, drivers: 0)
        #expect(s.isFilled(.drivers))
        #expect(s.addCount == 2)
    }

    @Test func aSinglePipelineAddsTheEditedJobAndContinuesOnServerJobs() {
        let complete = draft(motion, filled: ["character": "c", "driver": "d"], jobs: 1)
        let s = NewJobState(pipeline: motion, draft: complete, outfits: 0, drivers: 0)
        #expect(s.addCount == 1 && s.canContinue)
        // Basket only, edited job incomplete: nothing to add, still something to run.
        let basketOnly = draft(motion, filled: ["character": "c"], jobs: 2)
        let t = NewJobState(pipeline: motion, draft: basketOnly, outfits: 0, drivers: 0)
        #expect(t.addCount == 0 && t.canContinue)
    }

    @Test func nextSkipsFilledAndOptionalCardsAndEndsWithNil() {
        let d = draft(tryon, filled: ["character": "c"])
        let s = NewJobState(pipeline: tryon, draft: d, outfits: 0, drivers: 0)
        #expect(s.next(after: .single("character")) == .drivers)
        let t = NewJobState(pipeline: tryon, draft: d, outfits: 0, drivers: 1)
        #expect(t.next(after: .drivers) == .outfits)
        let u = NewJobState(pipeline: tryon, draft: d, outfits: 1, drivers: 1)
        #expect(u.next(after: .outfits) == nil)       // background is optional
    }

    /// Review finding 1: picked outfits that cannot be added (a required card
    /// is empty) must block Continue even when the basket has jobs, or Continue
    /// runs the basket and silently leaves the outfits on screen behind.
    @Test func pickedOutfitsThatCannotBeAddedBlockContinue() {
        let d = draft(tryon, filled: ["driver": "d"], jobs: 2)   // Character missing
        let s = NewJobState(pipeline: tryon, draft: d, outfits: 3, drivers: 0)
        #expect(s.addCount == 0)
        #expect(!s.canContinue)
        // Nothing composed: the basket alone can still run.
        #expect(NewJobState(pipeline: tryon, draft: d, outfits: 0, drivers: 0).canContinue)
    }
}
