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

    private var text: SlotText { SlotText(role: role, required: required, kind: kind, slot: slot) }

    var body: some View {
        Button(action: onTap) {
            HStack(spacing: 12) {
                thumbnailView
                    .frame(width: 44, height: 44)
                    .clipShape(.rect(cornerRadius: Theme.Radius.small))

                VStack(alignment: .leading, spacing: 2) {
                    Text(text.title)
                        .font(.body)
                        .foregroundStyle(Theme.label)
                    Text(text.value)
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
        .accessibilityLabel(text.title)
        .accessibilityValue(text.state)
        .accessibilityHint("Choose a material")
    }

    @ViewBuilder private var thumbnailView: some View {
        if text.assigned, let thumbnail, let image = UIImage(data: thumbnail) {
            Image(uiImage: image).resizable().scaledToFill()
        } else {
            ZStack {
                Theme.surfaceRaised
                Image(systemName: text.iconName)
                    .font(.body)
                    .foregroundStyle(Theme.tertiary)
            }
        }
    }
}

/// The same slot as a picture-first tile, for a grid of inputs at the top of
/// New Job — the shape generation apps (Kling, Runway, Higgsfield) give their
/// inputs, because the materials are what the user came to pick. Its
/// accessibility contract is `SlotRow`'s exactly: the UI smokes find a slot
/// by its role label and read "Missing required" from its value.
@MainActor
struct SlotTile: View {
    let role: String
    let required: Bool
    let kind: PipelineRoleKind
    let slot: DraftSlot?
    let thumbnail: Data?
    let disabled: Bool
    /// Empties the slot in one tap. Before 2026-09-26 the only way was to open
    /// the picker and deselect, which read as "change", not "remove".
    var onClear: (() -> Void)?
    let onTap: () -> Void

    private var text: SlotText { SlotText(role: role, required: required, kind: kind, slot: slot) }

    var body: some View {
        Button(action: onTap) {
            VStack(alignment: .leading, spacing: 6) {
                Color.clear
                    .aspectRatio(3 / 4, contentMode: .fit)
                    .overlay { picture }
                    .clipShape(.rect(cornerRadius: Theme.Radius.medium))
                    .overlay(alignment: .bottomLeading) {
                        if text.assigned, kind == .video {
                            Image(systemName: "video.fill")
                                .font(.caption2).foregroundStyle(.white)
                                .shadow(color: .black.opacity(0.6), radius: 3)
                                .padding(8)
                        }
                    }
                    // Leading, so the clear button owns the trailing corner.
                    .overlay(alignment: .topLeading) {
                        if let warning = slot?.warning, !warning.isEmpty {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .font(.footnote)
                                .foregroundStyle(Theme.warning)
                                .padding(6)
                                .background(.black.opacity(0.5), in: .circle)
                                .padding(6)
                        }
                    }
                VStack(alignment: .leading, spacing: 1) {
                    Text(text.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(Theme.label)
                        .lineLimit(1).minimumScaleFactor(0.8)
                    Text(text.assigned ? text.value : (required ? "Required" : "Optional"))
                        .font(.caption)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1).truncationMode(.middle)
                }
            }
            .contentShape(.rect)
        }
        // Plain, so each tile in a grid row takes its own taps instead of the
        // whole `List` row acting as one button.
        .buttonStyle(.plain)
        .disabled(disabled)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(text.title)
        .accessibilityValue(text.state)
        .accessibilityHint("Choose a material")
        // Outside the tile's `Button`, so its tap is its own and not the
        // tile's; a button nested in a button takes whichever wins the gesture.
        .overlay(alignment: .topTrailing) {
            if text.assigned, !disabled, let onClear {
                Button(action: onClear) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3)
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.white, .black.opacity(0.55))
                        .padding(4)
                        .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear \(text.title)")
            }
        }
    }

    /// An empty slot is the dashed add tile `BatchAddTile` already uses, so
    /// "tap to fill" reads the same in both modes.
    @ViewBuilder private var picture: some View {
        if text.assigned, let thumbnail, let image = UIImage(data: thumbnail) {
            Image(uiImage: image).resizable().scaledToFill()
        } else if text.assigned {
            ZStack {
                Theme.surfaceRaised
                Image(systemName: text.iconName).font(.title3).foregroundStyle(Theme.tertiary)
            }
        } else {
            RoundedRectangle(cornerRadius: Theme.Radius.medium)
                .strokeBorder(Theme.accent.opacity(0.6), style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
                .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
                .overlay {
                    VStack(spacing: 6) {
                        Image(systemName: "plus")
                            .font(.title2.weight(.medium))
                            .foregroundStyle(Theme.accent)
                        Image(systemName: text.iconName)
                            .font(.footnote)
                            .foregroundStyle(Theme.tertiary)
                    }
                }
        }
    }
}

/// The words a slot shows, shared by `SlotRow` and `SlotTile` so the two
/// shapes cannot drift on what VoiceOver and the UI smokes read.
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
