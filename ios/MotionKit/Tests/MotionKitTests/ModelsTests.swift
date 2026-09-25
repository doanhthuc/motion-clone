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

    @Test func batchSummaryCountsAndPutsTroubleFirst() throws {
        let json = #"""
        [{"id":"a","status":"done","stages":[{"name":"tryon","status":"done","elapsed_sec":6},{"name":"motion","status":"done","elapsed_sec":60}]},
         {"id":"b","status":"running","stages":[{"name":"tryon","status":"done","elapsed_sec":7},{"name":"motion","status":"running","elapsed_sec":null}]},
         {"id":"c","status":"error","stages":[{"name":"tryon","status":"error","elapsed_sec":12}]},
         {"id":"d","status":"pending","stages":[]}]
        """#
        let jobs = try decoder.decode([JobProgress].self, from: Fixtures.data(json))
        let summary = BatchSummary(jobs)
        #expect((summary.total, summary.done, summary.running, summary.failed) == (4, 1, 1, 1))
        #expect(summary.ordered.map(\.id) == ["c", "b", "a", "d"])
        #expect(jobs[0].finishedSec == 66)
        #expect(jobs[1].finishedSec == 7)
        // A failed stage keeps its elapsed on the wire — `scripts/batchlib/runner.py:220-221`
        // stamps `status="error"` and `elapsed_sec` together (`:237` for `done`), and
        // `scripts/control/runs.py:96` only yields `null` for a stage still running — so a
        // failed job's row shows its pre-failure seconds rather than nothing.
        #expect(jobs[2].finishedSec == 12)
    }

    @Test func batchSummaryCountsEachStatusSeparately() throws {
        // 12 jobs — `BatchComposer.maxJobs` — with a distinct count for every
        // present status (done 5, pending 4, running 2, error 1) and no `.unknown`,
        // so repointing any counter at any other status changes an asserted number.
        // A fixture with one job per status cannot tell `done` from `failed`.
        // `stages` stays empty because no counter reads it; the wire shape of a
        // stage's elapsed is pinned by `batchSummaryCountsAndPutsTroubleFirst`.
        let json = #"""
        [{"id":"j1","status":"done","stages":[]},{"id":"j2","status":"pending","stages":[]},
         {"id":"j3","status":"done","stages":[]},{"id":"j4","status":"running","stages":[]},
         {"id":"j5","status":"pending","stages":[]},{"id":"j6","status":"done","stages":[]},
         {"id":"j7","status":"error","stages":[]},{"id":"j8","status":"pending","stages":[]},
         {"id":"j9","status":"done","stages":[]},{"id":"j10","status":"running","stages":[]},
         {"id":"j11","status":"pending","stages":[]},{"id":"j12","status":"done","stages":[]}]
        """#
        let jobs = try decoder.decode([JobProgress].self, from: Fixtures.data(json))
        let summary = BatchSummary(jobs)
        #expect((summary.total, summary.done, summary.running, summary.failed) == (12, 5, 2, 1))
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

    @Test func decodesPodMigration() throws {
        let migrating = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podMigrating))
        #expect(migrating.migration?.running == true)
        #expect(migrating.migration?.toDc == "EU-CZ-1")
        #expect(migrating.migration?.fractionCopied == 0.25)
        let idle = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podIdle))
        #expect(idle.migration?.running == false)
        #expect(idle.migration?.fractionCopied == nil)
        let live = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podLive))
        #expect(live.migration == nil)
    }

    @Test func decodesGpuStockAndItsDestinations() throws {
        let stock = try decoder.decode(GpuStock.self, from: Fixtures.data(Fixtures.gpuStock))
        #expect(stock.selected == "NVIDIA GeForce RTX 5090")
        #expect(stock.homeDatacenter == "EU-RO-1")
        #expect(stock.gpus.count == 5)
        #expect(stock.gpus[0].home?.stock == "Low")
        #expect(stock.gpus[2].usdPerHr == nil && stock.gpus[2].soldOutEverywhere)
        #expect(stock.destinations == [
            MigrationDestination(datacenter: "EU-CZ-1", gpus: ["RTX 5090 (High)", "RTX 4090 (Medium)"]),
            MigrationDestination(datacenter: "US-TX-3", gpus: ["RTX 4090 (Low)"]),
        ])
    }

    @Test func gpuRowSummaryNeverInventsAPriceOrStock() throws {
        let stock = try decoder.decode(GpuStock.self, from: Fixtures.data(Fixtures.gpuStock))
        #expect(stock.gpus[0].summary == "$0.99/h · home stock Low")
        #expect(stock.gpus[1].summary == "$0.69/h · home stock unknown")
        #expect(stock.gpus[2].summary == "Sold out everywhere")
    }

    @Test func decodesBalances() throws {
        let ok = try decoder.decode(Balance.self, from: Fixtures.data(Fixtures.balance))
        #expect(ok.runpod?.usd == 12.34 && ok.runpod?.runwayHours == 12.46)
        #expect(ok.vast == nil)
        let down = try decoder.decode(Balance.self, from: Fixtures.data(Fixtures.balanceRunpodDown))
        #expect(down.runpod == nil)
        #expect(down.errors == ["couldn't reach runpodctl: timeout"])
        let vastDown = try decoder.decode(Balance.self, from: Fixtures.data(Fixtures.balanceVastDown))
        #expect(vastDown.vast != nil && vastDown.vast?.usd == nil)
        #expect(vastDown.runpod?.lowRunway == true)
    }

    @Test func decodesMigrateAsk() throws {
        let ask = try decoder.decode(MigrateAsk.self, from: Fixtures.data(Fixtures.migrateAsk))
        #expect(ask.toDc == "EU-CZ-1" && ask.homeDatacenter == "EU-RO-1")
        #expect(ask.confirmToken == "tok-abc" && ask.expiresInSec == 600)
        #expect(ask.warning.contains("Cannot be undone"))
    }

    @Test func decodesOutputs() throws {
        let o = try decoder.decode(OutputsResponse.self, from: Fixtures.data(Fixtures.outputs))
        #expect(o.outputs[0].files.map(\.isVideo) == [true, false])
        // Only videos carry a duration; an image (or an unprobeable video) decodes as nil.
        #expect(o.outputs[0].files.map(\.duration) == [12.4, nil])
        #expect(o.outputs[0].id == "2026-09-21-0900")
    }

    @Test func decodesMaterialsAndKeepsUnknownKinds() throws {
        let response = try decoder.decode(MaterialsResponse.self, from: Fixtures.data(Fixtures.materials))
        #expect(response.materials.count == 2)
        #expect(response.materials[0].kind == .image)
        #expect(response.materials[1].kind == .unknown)
    }

    @Test func decodesUploadLifecycleResponses() throws {
        let opened = try decoder.decode(UploadOpenResponse.self, from: Fixtures.data(Fixtures.uploadOpen))
        #expect(opened.uploadId == "abc123")
        #expect(opened.chunkSize == 33_554_432)
        #expect(opened.chunksTotal == 2)

        let status = try decoder.decode(UploadStatus.self, from: Fixtures.data(Fixtures.uploadStatus))
        #expect(status.fileName == "driver.mp4")
        #expect(status.received == [0])
        #expect(status.material == nil)
        #expect(status.probe == nil)

        let complete = try decoder.decode(
            UploadCompleteResponse.self, from: Fixtures.data(Fixtures.uploadComplete))
        #expect(complete.material.kind == .video)
        #expect(complete.probe.width == 1080)
        #expect(complete.probe.durationS == 12.5)
        #expect(complete.probe.warning == "Video is larger than recommended.")
    }

    @Test func pipelineAndDraftModelsDecode() throws {
        let catalog = try MotionJSON.decoder.decode(
            PipelineCatalogResponse.self, from: Fixtures.data(Fixtures.pipelines))
        #expect(catalog.pipelines[1].providers.map(\.id) == ["gemini", "qwen-max"])
        #expect(catalog.pipelines[1].roles["background"] == .unknown)

        let draft = try MotionJSON.decoder.decode(Draft.self, from: Fixtures.data(Fixtures.draft))
        #expect(draft.generation == 4)
        #expect(draft.validated == nil)
        #expect(draft.slots["character"]?.probe.kind == "image")
        #expect(draft.batch.first?.digest == "abc123def0")
        #expect(draft.jobs == 1 && draft.estimateMin == nil)
    }

    @Test func draftProbeDecodesWhenNestedWarningIsOmitted() throws {
        let payload = #"""
        {"owner":"app","pipeline":"tryon-motion-enhance","provider":"gemini","generation":5,
         "slots":{"outfit":{"material_id":"app/dress.png","name":"dress.png","exists":true,
           "probe":{"kind":"image","width":1024,"height":1536,"duration_s":null,
           "bitrate_kbps":null,"size_bytes":900},"warning":""}},
         "required":["character","driver","outfit"],"optional":["background"],
         "missing":["character","driver"],"validated":null,"batch":[],
         "jobs":0,"estimate_min":null}
        """#

        let draft = try decoder.decode(Draft.self, from: Data(payload.utf8))

        #expect(draft.slots["outfit"]?.probe.warning == "")
    }

    @Test func roleKindsFilterOnlyCompatibleMaterials() throws {
        let image = Material(id: "app/a.png", owner: "app", name: "a.png",
                             bytes: 1, updatedAt: 1, kind: .image)
        let video = Material(id: "app/a.mp4", owner: "app", name: "a.mp4",
                             bytes: 1, updatedAt: 1, kind: .video)
        #expect(PipelineRoleKind.image.accepts(image))
        #expect(!PipelineRoleKind.image.accepts(video))
        #expect(PipelineRoleKind.video.accepts(video))
        #expect(!PipelineRoleKind.unknown.accepts(image))
    }

    @Test func validationResponseKeepsNestedAuthoritativeDraft() throws {
        let result = try MotionJSON.decoder.decode(
            DraftValidationResponse.self, from: Fixtures.data(Fixtures.validatedDraft))
        #expect(result.valid && !result.stale)
        #expect(result.draft.validated == true)
        #expect(result.draft.estimateMin == 48)
    }

    @Test func draftSeedIsOptionalAndDecodes() throws {
        let old = try decoder.decode(Draft.self, from: Fixtures.data(Fixtures.draft))
        #expect(old.tryonSeed == nil)
        #expect(old.batch[0].tryonSeed == nil)
        #expect(old.filledSlots == ["character": "app/model.png"])
        #expect(old.batch[0].filledSlots == ["character": "app/model.png", "outfit": "app/dress.png"])

        // `Fixtures.draft` predates Phase 6 and carries no `tryon_seed`, so the seeded
        // shape is synthesized from it rather than adding a second near-identical fixture.
        let seeded = Fixtures.draft
            .replacingOccurrences(of: #""estimate_min":null}"#,
                                  with: #""estimate_min":null,"tryon_seed":"s1"}"#)
            .replacingOccurrences(of: #""provider":"gemini","slots":{"character""#,
                                  with: #""provider":"gemini","tryon_seed":"s0","slots":{"character""#)
        let draft = try decoder.decode(Draft.self, from: Fixtures.data(seeded))
        #expect(draft.tryonSeed == "s1")
        #expect(draft.batch[0].tryonSeed == "s0")
    }

    @Test func unfilledDraftSlotsAreDroppedFromFilledSlots() throws {
        // `Fixtures.draft` fills its only slot, so it cannot tell `compactMapValues(\.materialID)`
        // from `mapValues { $0.materialID ?? "" }`. `TryonLibraryStore.matches(slots:)` matches
        // library entries by dictionary equality over `filledSlots`, where an empty-string role
        // would never match — so the nil-drop needs a payload that actually carries a null
        // material id.
        let payload = #"""
        {"owner":"app","pipeline":"tryon-motion-enhance","provider":"gemini","generation":4,
         "slots":{"character":{"material_id":"app/model.png","name":"model.png","exists":true,
           "probe":{"kind":"image","width":1024,"height":1536,"duration_s":null,
           "bitrate_kbps":null,"size_bytes":900},"warning":""},
          "outfit":{"material_id":null,"name":"dress.png","exists":true,
           "probe":{"kind":"image","width":1024,"height":1536,"duration_s":null,
           "bitrate_kbps":null,"size_bytes":900},"warning":""}},
         "required":["character","driver","outfit"],"optional":["background"],
         "missing":["driver","outfit"],"validated":null,
         "batch":[],"jobs":0,"estimate_min":null}
        """#

        let draft = try decoder.decode(Draft.self, from: Data(payload.utf8))

        #expect(draft.slots.count == 2)
        #expect(draft.slots["outfit"]?.materialID == nil)
        #expect(draft.filledSlots == ["character": "app/model.png"])
    }

    @Test func draftPatchEncodesThreeSeedStates() throws {
        // `APIClient` uses the same key-encoding strategy on every write call; `.sortedKeys` is
        // added here only to make the expected bytes deterministic.
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = .sortedKeys
        func text(_ p: DraftPatch) throws -> String { String(decoding: try encoder.encode(p), as: UTF8.self) }
        #expect(try text(DraftPatch(slots: ["outfit": "app/o.png"], seed: .set("s1")))
                == #"{"slots":{"outfit":"app\/o.png"},"tryon_seed":"s1"}"#)
        #expect(try text(DraftPatch(slots: ["outfit": "app/o.png"], seed: .clear))
                == #"{"slots":{"outfit":"app\/o.png"},"tryon_seed":null}"#)
        #expect(try text(DraftPatch(slots: ["outfit": nil])) == #"{"slots":{"outfit":null}}"#)
        #expect(try text(DraftPatch(seed: .clear)) == #"{"tryon_seed":null}"#)
    }

    @Test func libraryEntriesDecode() throws {
        let json = #"{"entries":[{"id":"a1","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o.png"},"provider":"gemini","saved_at":1790000300.5}]}"#
        let response = try decoder.decode(TryonLibraryResponse.self, from: Fixtures.data(json))
        #expect(response.entries == [TryonLibraryEntry(id: "a1", materialIDs: ["character": "app/me.png", "outfit": "app/o.png"],
                                                       provider: "gemini", savedAt: 1790000300.5)])
    }
}

@Suite struct RunFlowModelTests {
    @Test func tryonPreviewsDecode() throws {
        let t = try MotionJSON.decoder.decode(TryonPreviews.self, from: Fixtures.data(Fixtures.tryonRunning))
        #expect(t.runId == "tg-1000" && t.runToken == "1790000000123.4" && t.phaseARunning)
        #expect(t.previews.map(\.id) == ["0", "1"])
        #expect(t.previews[0].status == .running && !t.previews[0].hasImage)
    }

    /// Phase 6 shared try-on fields are additive — a fixture that predates
    /// them (`Fixtures.tryonRunning`) must still decode, with both nil.
    @Test func tryonPreviewWithoutSharedKeysDecodesNil() throws {
        let t = try MotionJSON.decoder.decode(TryonPreviews.self, from: Fixtures.data(Fixtures.tryonRunning))
        #expect(t.previews[0].sharedFrom == nil)
        #expect(t.previews[0].shares == nil)
    }

    @Test func tryonPreviewWithSharedKeysDecode() throws {
        let json = #"""
        {"run_id":"tg-1000","run_token":"1.1","phase_a_running":false,
         "previews":[{"index":"0","run":"o1d1","status":"done","has_image":true,
                      "shared_from":null,"shares":["1"]},
                     {"index":"1","run":"o1d2","status":"done","has_image":true,
                      "shared_from":"0","shares":[]}]}
        """#
        let t = try MotionJSON.decoder.decode(TryonPreviews.self, from: Fixtures.data(json))
        #expect(t.previews[0].sharedFrom == nil && t.previews[0].shares == ["1"])
        #expect(t.previews[1].sharedFrom == "0" && t.previews[1].shares == [])
    }

    @Test func rentPanelDecodes() throws {
        let p = try MotionJSON.decoder.decode(RentPanel.self, from: Fixtures.data(Fixtures.rentPanel))
        #expect(p.panelToken == "1790000000123.4.9" && p.afterPhaseA && p.jobs == 2 && p.estimateMin == 84)
        #expect(p.runpod.usdPerHr == 0.99 && !p.runpod.soldOut && p.runpod.datacenter == "EU-RO-1")
        #expect(p.vast.canSpend && p.vast.sessionUsd == 1.05)
    }

    @Test func soldOutPanelDecodesNulls() throws {
        let p = try MotionJSON.decoder.decode(RentPanel.self, from: Fixtures.data(Fixtures.rentPanelSoldOut))
        #expect(p.runpod.soldOut && p.runpod.usdPerHr == nil && p.runpod.stock == nil)
        #expect(!p.vast.canSpend && p.vast.blockers.count == 1)
    }

    @Test func keepRecordDecodes() throws {
        let r = try MotionJSON.decoder.decode(TryonLibraryRecord.self, from: Fixtures.data(Fixtures.keepRecord))
        #expect(r.id == "a1b2c3" && r.provider == "gemini")
    }

    @Test func quoteIsMinutesTimesRate() {
        #expect(CostEstimate.quote(estimateMin: 84, usdPerHr: 0.99) == 84.0 / 60 * 0.99)
        #expect(CostEstimate.quote(estimateMin: 84, usdPerHr: nil) == nil)
        #expect(CostEstimate.quote(estimateMin: 0, usdPerHr: 0.99) == 0)
    }
}
