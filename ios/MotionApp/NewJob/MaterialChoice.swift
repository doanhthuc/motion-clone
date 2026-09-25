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
    @State private var previewing = false

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
        // A tap selects here, so watching comes from the long-press menu.
        .contextMenu {
            Button(material.kind == .video ? "Play" : "Preview",
                   systemImage: material.kind == .video ? "play.fill" : "eye") { previewing = true }
        }
        .sheet(isPresented: $previewing) {
            MaterialPreview(material: material, materials: materials, modal: true)
        }
        .accessibilityLabel(material.name)
        .accessibilityValue(selected ? "Selected" : "Not selected")
        .task(id: material.id) {
            thumbnail = await materials.thumbnail(for: material)
        }
    }
}
