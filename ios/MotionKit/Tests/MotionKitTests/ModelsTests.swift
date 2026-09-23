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

    @Test func decodesMaterialsAndKeepsUnknownKinds() throws {
        let response = try decoder.decode(MaterialsResponse.self, from: Fixtures.data(Fixtures.materials))
        #expect(response.materials.count == 2)
        #expect(response.materials[0].kind == .image)
        #expect(response.materials[0].canDelete)
        #expect(response.materials[1].kind == .unknown)
        #expect(!response.materials[1].canDelete)
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
        #expect(catalog.pipelines[1].roles["mask"] == .unknown)

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
         "required":["character","driver","outfit"],"optional":["mask"],
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
}

@Suite struct RunFlowModelTests {
    @Test func tryonPreviewsDecode() throws {
        let t = try MotionJSON.decoder.decode(TryonPreviews.self, from: Fixtures.data(Fixtures.tryonRunning))
        #expect(t.runId == "tg-1000" && t.runToken == "1790000000123.4" && t.phaseARunning)
        #expect(t.previews.map(\.id) == ["0", "1"])
        #expect(t.previews[0].status == .running && !t.previews[0].hasImage)
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
