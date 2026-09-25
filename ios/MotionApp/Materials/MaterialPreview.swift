import AVKit
import MotionKit
import SwiftUI

/// A material at full size: a video plays (with the system controls — this
/// is for checking a driver, so scrubbing matters), an image loads at full
/// resolution. Before 2026-09-25 the app only ever showed a poster frame, so a
/// video material could not be watched at all.
@MainActor
struct MaterialPreview: View {
    let material: MotionKit.Material
    let materials: MaterialsStore
    @Environment(\.dismiss) private var dismiss
    @State private var clip: MaterialClip?
    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()
                content
            }
            .navigationTitle(material.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.hidden, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .task(id: material.id) { await load() }
        .onDisappear { clip?.stop() }
    }

    @ViewBuilder private var content: some View {
        if let clip {
            VideoPlayer(player: clip.player)
                .ignoresSafeArea(edges: .bottom)
                .accessibilityLabel("Video \(material.name)")
        } else if let image {
            Image(uiImage: image)
                .resizable()
                .scaledToFit()
                .accessibilityLabel(material.name)
        } else if failed {
            ContentUnavailableView("Couldn't load \(material.name)", systemImage: "exclamationmark.triangle")
        } else {
            ProgressView().tint(.white)
        }
    }

    private func load() async {
        let path = ["v1", "materials", material.owner, material.name]
        if material.kind == .video {
            let clip = MaterialClip(client: materials.client, path: path)
            self.clip = clip
            clip.player.play()
            return
        }
        // The poster first, so the screen is never blank while the full file comes.
        if let thumb = await materials.thumbnail(for: material) { image = UIImage(data: thumb) }
        do {
            let data = try await materials.client.data(path[0], path[1], path[2], path[3])
            if let full = UIImage(data: data) { image = full } else if image == nil { failed = true }
        } catch {
            if image == nil { failed = true }
        }
    }
}
