import Foundation
import Testing
@testable import MotionKit

@Suite struct BatchEntryEditTests {
    private func entry(provider: String = "gemini", seed: String? = nil) -> DraftBatchEntry {
        let seedJSON = seed.map { "\"\($0)\"" } ?? "null"
        let json = """
        {"digest":"abc123def0","run_id":"r1","pipeline":"tryon-motion-enhance","provider":"\(provider)",
         "tryon_seed":\(seedJSON),
         "slots":{"character":"app/me.png","outfit":"app/dress.png","driver":"app/dance.mp4"}}
        """
        return try! MotionJSON.decoder.decode(DraftBatchEntry.self, from: Data(json.utf8))
    }

    @Test func replacingAMaterialSendsOnlyThatSlot() {
        #expect(BatchEntryEdit.replacing("character", with: "app/bg.png", in: entry())
                == BatchEntryPatch(slots: ["character": "app/bg.png"]))
    }

    /// A saved try-on was made from the old character/outfit pair; kept, it
    /// would seed a job with an image that does not match its own materials.
    @Test func replacingThePairOfASeededJobDropsTheSeed() {
        let seeded = entry(seed: "s1")
        #expect(BatchEntryEdit.replacing("outfit", with: "app/bg.png", in: seeded).seed == .clear)
        #expect(BatchEntryEdit.replacing("character", with: "app/bg.png", in: seeded).seed == .clear)
        #expect(BatchEntryEdit.replacing("driver", with: "app/d2.mp4", in: seeded).seed == .keep)
    }

    /// The server refuses a seed beside a pod provider (`not_local`); the
    /// switch drops it in the same request instead.
    @Test func aPodProviderDropsTheSeedAndAHostedOneKeepsIt() {
        let seeded = entry(seed: "s1")
        #expect(BatchEntryEdit.provider("qwen", in: seeded) == BatchEntryPatch(provider: "qwen", seed: .clear))
        #expect(BatchEntryEdit.provider("qwen-max", in: seeded) == BatchEntryPatch(provider: "qwen-max"))
        #expect(BatchEntryEdit.provider("qwen", in: entry()) == BatchEntryPatch(provider: "qwen"))
    }

    @Test func aSeedIsOfferedOnlyWithAHostedProvider() {
        #expect(BatchEntryEdit.canSeed(entry(provider: "gemini")))
        #expect(!BatchEntryEdit.canSeed(entry(provider: "qwen")))
    }
}
