import MotionKit
import SwiftUI

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

    private var stateText: String {
        assigned ? (slot?.name ?? "Assigned") : (required ? "Missing required" : "Empty optional")
    }

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 12) {
                thumbnailView
                    .frame(width: 48, height: 48)
                    .clipShape(.rect(cornerRadius: 10))

                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 7) {
                        Text(displayName(role))
                            .font(Theme.sans(15, .semibold))
                            .foregroundStyle(Theme.ink1)
                        Text(required ? "Required" : "Optional")
                            .font(Theme.mono(9, .medium))
                            .foregroundStyle(required ? Theme.amber : Theme.ink3)
                    }

                    Text(stateText)
                        .font(Theme.sans(12))
                        .foregroundStyle(assigned ? Theme.ink2 : (required ? Theme.amber : Theme.ink3))
                        .lineLimit(1)

                    if let warning = slot?.warning, !warning.isEmpty {
                        Label(warning, systemImage: "exclamationmark.triangle.fill")
                            .font(Theme.sans(10, .medium))
                            .foregroundStyle(Theme.amber)
                            .lineLimit(1)
                    } else {
                        Text(kindLabel)
                            .font(Theme.mono(9))
                            .foregroundStyle(Theme.ink3)
                    }
                }

                Spacer(minLength: 0)
                Text(assigned ? "Change" : "Choose")
                    .font(Theme.sans(12, .semibold))
                    .foregroundStyle(Theme.lime)
            }
            .padding(11)
            .card(border: required && !assigned ? Theme.amber.opacity(0.35) : Theme.line)
        }
        .buttonStyle(.plain)
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
                assigned ? Theme.surface2 : Theme.surface3
                Image(systemName: iconName)
                    .font(.system(size: 19, weight: .medium))
                    .foregroundStyle(assigned ? Theme.ink3 : (required ? Theme.amber : Theme.ink3))
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

    private var kindLabel: String {
        switch kind {
        case .image: "IMAGE"
        case .video: "VIDEO"
        case .unknown: "UNKNOWN TYPE"
        }
    }

    private func displayName(_ value: String) -> String {
        value.replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
    }
}
