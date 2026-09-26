import MotionKit
import SwiftUI

/// The long-press preview of a material tile: the picture at close to screen
/// width, and a video already playing — the tile alone was too small to judge
/// a pose or an outfit, and a "Play" item that opened a second sheet was one
/// step too many (2026-09-26). The menu under it carries full screen and delete.
@MainActor
struct MaterialPeek: View {
    let material: MotionKit.Material
    let materials: MaterialsStore
    @State private var clip: FeedClip?
    @State private var image: UIImage?

    /// Wide enough to read a face on the smallest supported phone; the system
    /// shrinks a preview that does not fit, so this is a ceiling, not a demand.
    private let width: CGFloat = 360
    private let maxHeight: CGFloat = 600

    private var path: [String] { ["v1", "materials", material.owner, material.name] }

    /// The video's own shape once it is known, else the poster's, else 9:16 —
    /// most materials are phone-shot portrait.
    private var aspect: CGFloat {
        if let size = clip?.videoSize, size.width > 0, size.height > 0 { return size.width / size.height }
        if let size = image?.size, size.width > 0, size.height > 0 { return size.width / size.height }
        return 9.0 / 16.0
    }

    private var height: CGFloat { min(maxHeight, width / aspect) }

    var body: some View {
        ZStack {
            Color.black
            if let image {
                Image(uiImage: image).resizable().scaledToFit()
            }
            if let clip {
                PlayerSurface(player: clip.player, videoSize: clip.videoSize)
                if clip.buffering { ProgressView().tint(.white) }
            } else if image == nil {
                ProgressView().tint(.white)
            }
        }
        .frame(width: height == maxHeight ? maxHeight * aspect : width, height: height)
        .task(id: material.id) { await load() }
        .onDisappear {
            clip?.teardown()
            clip = nil
        }
    }

    private func load() async {
        // The cached poster first, so the peek opens with a picture and not a spinner.
        if let thumb = await materials.thumbnail(for: material) { image = UIImage(data: thumb) }
        if material.kind == .video {
            try? PlaybackAudioSession.configure()
            let clip = FeedClip(client: materials.client, path: path)
            self.clip = clip
            // Not `activate()`: that honours Auto-Play Video Previews for the
            // feed's scrolling, and a long press is an explicit ask to watch.
            clip.player.play()
            return
        }
        if let data = try? await materials.client.data(path[0], path[1], path[2], path[3]),
           let full = UIImage(data: data) {
            image = full
        }
    }
}
