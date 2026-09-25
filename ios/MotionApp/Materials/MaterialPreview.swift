import MotionKit
import SwiftUI

/// A material at full size, in the Outputs feed's player: the video owns the
/// screen above a black strip that holds the scrub bar and the name, tap
/// pauses, and it loops. An image loads at full resolution. Before 2026-09-25
/// the app only ever showed a poster frame, so a video could not be watched.
/// A long press opens the same Save / Share / speed sheet as the feed.
@MainActor
struct MaterialPreview: View {
    let material: MotionKit.Material
    let materials: MaterialsStore
    /// Presented as a sheet (from a picker, swipe down to close) rather than
    /// pushed (from the Materials tab, swipe from the edge like the Outputs feed).
    var modal = false
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @State private var clip: FeedClip?
    @State private var image: UIImage?
    @State private var failed = false
    @State private var exporter = MediaExporter()
    @State private var showingActions = false

    private var path: [String] { ["v1", "materials", material.owner, material.name] }
    private var isVideo: Bool { material.kind == .video }
    private var paused: Bool { clip?.userPaused ?? false }

    var body: some View {
        if modal {
            NavigationStack {
                screen.toolbar {
                    ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
                }
            }
        } else {
            screen.toolbar(.hidden, for: .tabBar)
        }
    }

    private var screen: some View {
        VStack(spacing: 0) {
            media
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .clipped()
                .contentShape(.rect)
                .onTapGesture { clip?.togglePause() }
                .onLongPressGesture(minimumDuration: 0.35) { showingActions = true }
                .overlay(alignment: .bottom) {
                    MediaStatusView(exporter: exporter).padding(.horizontal, 16).padding(.bottom, 12)
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(material.name)
                .accessibilityValue(isVideo ? (paused ? "Paused" : "Playing") : "")
                .accessibilityAddTraits(isVideo ? .startsMediaSession : .isImage)
                .accessibilityAction(named: paused ? "Play" : "Pause") { clip?.togglePause() }
                // The long press has no VoiceOver gesture; its two actions do, directly.
                .accessibilityAction(named: "Save to Photos") {
                    Task { await exporter.saveToPhotos(isVideo: isVideo, download) }
                }
                .accessibilityAction(named: "Share") { Task { await exporter.share(download) } }
            strip
        }
        .background(.black)
        .toolbarBackground(.hidden, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
        // Space plays and pauses from a hardware keyboard, as in the feed.
        .background {
            Button("Play or Pause") { clip?.togglePause() }
                .keyboardShortcut(.space, modifiers: [])
                .opacity(0)
                .accessibilityHidden(true)
        }
        .mediaActions(isPresented: $showingActions, exporter: exporter, isVideo: isVideo,
                      rate: clip.map { clip in Binding(get: { clip.rate }, set: { clip.rate = $0 }) },
                      download: download)
        .task(id: material.id) { await load() }
        .onChange(of: scenePhase) { _, phase in
            phase == .active ? clip?.resume() : clip?.suspend()
        }
        .onDisappear { clip?.teardown() }
    }

    @ViewBuilder private var media: some View {
        ZStack {
            Color.black
            if let clip {
                ClipSurface(clip: clip)
            } else if let image {
                Image(uiImage: image).resizable().scaledToFit()
            } else if failed {
                ContentUnavailableView("Couldn't load \(material.name)", systemImage: "exclamationmark.triangle")
            } else {
                ProgressView().tint(.white)
            }
        }
    }

    /// The feed's bottom strip: scrub bar on the video's edge, the name under it.
    private var strip: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let clip {
                ScrubBar(clip: clip)
            }
            Text(material.name)
                .font(.footnote.monospacedDigit()).foregroundStyle(Theme.secondary)
                .lineLimit(1).truncationMode(.middle)
                .padding(.vertical, 8)
        }
        .padding(.horizontal, 16)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func download() async throws -> URL {
        try await materials.client.download(path[0], path[1], path[2], path[3])
    }

    private func load() async {
        if isVideo {
            try? PlaybackAudioSession.configure()
            let clip = FeedClip(client: materials.client, path: path)
            self.clip = clip
            clip.activate()
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
