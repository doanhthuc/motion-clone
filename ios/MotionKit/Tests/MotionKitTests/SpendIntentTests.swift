import Foundation
import Testing
@testable import MotionKit

@Suite struct SpendIntentTests {
    private func object(_ intent: SpendIntent) throws -> [String: Any] {
        try #require(JSONSerialization.jsonObject(with: intent.body()) as? [String: Any])
    }

    @Test func guidanceHasExactlyTheThreeWireValues() {
        #expect(Guidance.allCases.map(\.rawValue) == ["keep_face", "tighter_crop", "match_lighting"])
    }

    @Test func phaseAPostsAnEmptyObject() throws {
        #expect(SpendIntent.phaseA.path == ["v1", "runs", "phase-a"])
        #expect(try object(.phaseA).isEmpty)
        #expect(SpendIntent.phaseA.runID == nil)
    }

    @Test func regenBodyCarriesTokenAndOrderedGuidance() throws {
        let intent = SpendIntent.regen(runID: "tg-1000", index: "2", runToken: "123.4",
                                       guidance: [.keepFace, .matchLighting])
        #expect(intent.path == ["v1", "runs", "tg-1000", "tryon", "2", "regen"])
        let body = try object(intent)
        #expect(body["run_token"] as? String == "123.4")
        #expect(body["guidance"] as? [String] == ["keep_face", "match_lighting"])
    }

    @Test func confirmOmitsNilFields() throws {
        let bare = SpendIntent.confirm(runID: "tg-1000", provider: .vast, panelToken: "9.1",
                                       gpu: nil, tryon: nil)
        #expect(bare.path == ["v1", "runs", "tg-1000", "confirm"])
        #expect(try object(bare).keys.sorted() == ["panel_token", "provider"])
        let full = SpendIntent.confirm(runID: "tg-1000", provider: .runpod, panelToken: "9.1",
                                       gpu: "NVIDIA GeForce RTX 5090", tryon: .reuse)
        let body = try object(full)
        #expect(body["provider"] as? String == "runpod")
        #expect(body["gpu"] as? String == "NVIDIA GeForce RTX 5090")
        #expect(body["tryon"] as? String == "reuse")
    }

    @Test func resumeBody() throws {
        let intent = SpendIntent.resume(runID: "tg-1000", provider: .runpod, runToken: "77",
                                        gpu: "NVIDIA GeForce RTX 5090")
        #expect(intent.path == ["v1", "runs", "tg-1000", "resume"])
        #expect(intent.runID == "tg-1000")
        let body = try object(intent)
        #expect(body["run_token"] as? String == "77")
        #expect(body["provider"] as? String == "runpod")
    }

    @Test func migrateCarriesDestinationAndTokenAndNoRun() throws {
        let intent = SpendIntent.migrate(toDc: "EU-CZ-1", confirmToken: "tok-abc")
        #expect(intent.path == ["v1", "pod", "migrate"])
        #expect(intent.kind == .migrate)
        #expect(intent.runID == nil)
        let body = try object(intent)
        #expect(body["to_dc"] as? String == "EU-CZ-1")
        #expect(body["confirm_token"] as? String == "tok-abc")
        #expect(body.keys.count == 2)
    }

    @Test func intentRoundTripsThroughCodable() throws {
        let intent = SpendIntent.confirm(runID: "tg-1000", provider: .runpod, panelToken: "t",
                                         gpu: "g", tryon: .rerun)
        let decoded = try JSONDecoder().decode(SpendIntent.self, from: JSONEncoder().encode(intent))
        #expect(decoded == intent)
        #expect(decoded.kind == .confirm)
    }
}
