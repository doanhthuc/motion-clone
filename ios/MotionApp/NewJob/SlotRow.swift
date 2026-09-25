import MotionKit
import SwiftUI

/// One material slot as a navigation-style row: thumbnail, role, current value,
/// chevron. An empty slot is neutral — "Missing required" only earns a warning
/// color once validation says so, not on a form nobody has touched.
@MainActor
struct SlotRow: View {
    let role: String
    let required: Bool
    let kind: PipelineRoleKind
    let slot: DraftSlot?
    let thumbnail: Data?
    let disabled: Bool
    let onTap: () -> Void

    private var assigned: Bool {
        slot?.materialID != nil && slot?.exists == true
    }

    /// What VoiceOver and the UI smokes read; the visible line is `valueText`.
    private var stateText: String {
        assigned ? (slot?.name ?? "Assigned") : (required ? "Missing required" : "Empty optional")
    }

    private var valueText: String {
        if assigned { return slot?.name ?? "Assigned" }
        return required ? "Choose \(kindNoun)" : "Optional · \(kindNoun)"
    }

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 12) {
                thumbnailView
                    .frame(width: 44, height: 44)
                    .clipShape(.rect(cornerRadius: Theme.Radius.small))

                VStack(alignment: .leading, spacing: 2) {
                    Text(displayName(role))
                        .font(.body)
                        .foregroundStyle(Theme.label)
                    Text(valueText)
                        .font(.subheadline)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1).truncationMode(.middle)
                    if let warning = slot?.warning, !warning.isEmpty {
                        Label(warning, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote)
                            .foregroundStyle(Theme.warning)
                            .lineLimit(2)
                    }
                }

                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(Theme.tertiary)
            }
            .contentShape(.rect)
        }
        .disabled(disabled)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(displayName(role))
        .accessibilityValue(stateText)
        .accessibilityHint("Choose a material")
    }

    @ViewBuilder private var thumbnailView: some View {
        if assigned, let thumbnail, let image = UIImage(data: thumbnail) {
            Image(uiImage: image).resizable().scaledToFill()
        } else {
            ZStack {
                Theme.surfaceRaised
                Image(systemName: iconName)
                    .font(.body)
                    .foregroundStyle(Theme.tertiary)
            }
        }
    }

    private var iconName: String {
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

    private func displayName(_ value: String) -> String {
        value.replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
    }
}
