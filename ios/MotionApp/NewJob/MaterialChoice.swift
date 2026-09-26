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
    /// Asks for confirmation; the picker that owns the dialog does the delete.
    let onDelete: () -> Void
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
        // A tap selects here, so watching comes from the long press: the peek
        // plays a video by itself, and full screen adds the scrub bar and sound controls.
        .contextMenu {
            Button("View full screen", systemImage: "arrow.up.left.and.arrow.down.right") { previewing = true }
            Button("Delete", systemImage: "trash", role: .destructive, action: onDelete)
        } preview: {
            MaterialPeek(material: material, materials: materials)
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
