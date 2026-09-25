import MotionKit
import SwiftUI

/// A tappable material tile with its selected state, shared by the single
/// picker and the outfit/driver multi-pickers.
@MainActor
struct MaterialChoice: View {
    let material: MotionKit.Material
    let materials: MaterialsStore
    let selected: Bool
    var compact = false
    let onSelect: () -> Void
    @State private var thumbnail: Data?

    var body: some View {
        Button(action: onSelect) {
            MaterialCard(
                material: material,
                warning: materials.warning(for: material.id),
                thumbnail: thumbnail,
                selected: selected,
                compact: compact)
                .overlay(alignment: .topTrailing) {
                    if selected {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.title3)
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(Theme.onAccent, Theme.accent)
                            .padding(8)
                    }
                }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(material.name)
        .accessibilityValue(selected ? "Selected" : "Not selected")
        .task(id: material.id) {
            thumbnail = await materials.thumbnail(for: material)
        }
    }
}
