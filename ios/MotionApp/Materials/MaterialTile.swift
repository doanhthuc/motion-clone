import MotionKit
import SwiftUI

/// One material in the library, used by the category rows and the See all
/// grid alike: tap previews, long-press moves it to another role or deletes it.
@MainActor
struct MaterialTile: View {
    let material: MotionKit.Material
    let store: MaterialsStore
    let onOpen: () -> Void
    /// Asks for confirmation; the screen that owns the dialog does the delete.
    let onDelete: () -> Void
    @State private var thumbnail: Data?

    var body: some View {
        Button(action: onOpen) {
            MaterialCard(material: material, warning: store.warning(for: material.id),
                         thumbnail: thumbnail, compact: true)
        }
        .buttonStyle(.plain)
        .accessibilityHint(material.kind == .video ? "Plays the video" : "Shows the full image")
        .contextMenu {
            if material.kind == .image {
                Menu("Move to", systemImage: "folder") {
                    ForEach(MaterialRole.options(for: .image), id: \.self) { role in
                        Button(role.title) { Task { await store.setRole(role, for: material) } }
                            .disabled(material.materialRole == role)
                    }
                }
            }
            Button("Delete", systemImage: "trash", role: .destructive, action: onDelete)
        }
        .task(id: material.id) { thumbnail = await store.thumbnail(for: material) }
    }
}

/// The delete confirmation, shared by every screen that shows tiles.
struct MaterialDeleteDialog: ViewModifier {
    @Binding var candidate: MotionKit.Material?
    let store: MaterialsStore

    func body(content: Content) -> some View {
        content.confirmationDialog(
            "Delete this material?",
            isPresented: Binding(get: { candidate != nil }, set: { if !$0 { candidate = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                guard let material = candidate else { return }
                candidate = nil
                Task { await store.delete(material) }
            }
            Button("Cancel", role: .cancel) { candidate = nil }
        } message: {
            Text(candidate?.name ?? "")
        }
    }
}
