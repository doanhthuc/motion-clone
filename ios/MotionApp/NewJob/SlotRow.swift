import MotionKit
import SwiftUI

/// The words a slot shows, shared by `SlotCard`, the batch entries and the
/// action bar so none of them drift on what VoiceOver and the UI smokes read.
struct SlotText {
    let role: String
    let required: Bool
    let kind: PipelineRoleKind
    let slot: DraftSlot?

    var assigned: Bool { slot?.materialID != nil && slot?.exists == true }

    /// `tryon_character` → "Tryon Character".
    var title: String {
        role.replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
    }

    /// What VoiceOver and the UI smokes read; the visible line is `value`.
    var state: String {
        assigned ? (slot?.name ?? "Assigned") : (required ? "Missing required" : "Empty optional")
    }

    var value: String {
        if assigned { return slot?.name ?? "Assigned" }
        return required ? "Choose \(kindNoun)" : "Optional · \(kindNoun)"
    }

    var iconName: String {
        switch kind {
        case .image: "photo"
        case .video: "film"
        case .unknown: "questionmark.square.dashed"
        }
    }

    private var kindNoun: String {
        switch kind {
        case .image: "an image"
        case .video: "a video"
        case .unknown: "a material"
        }
    }
}
