import AVFoundation
@preconcurrency import Photos
import SwiftUI
import MotionKit

/// Full-screen, vertically paged outputs of one batch, in the style of Shorts:
/// swipe to the next file, videos loop, tap to pause, drag the bar to seek.
struct OutputFeedView: View {
    let client: APIClient
    let batch: OutputBatch
    @State private var current: String?
    @State private var playback: FeedPlayback
    @State private var saving = false
    @State private var toast: String?
    @Environment(\.scenePhase) private var scenePhase

    init(client: APIClient, batch: OutputBatch, startAt file: OutputFile) {
        self.client = client
        self.batch = batch
        _current = State(initialValue: file.id)
        _playback = State(initialValue: FeedPlayback(client: client, batch: batch.batch))
    }

    private var currentFile: OutputFile? { batch.files.first { $0.id == current } }
    private var position: Int { (batch.files.firstIndex { $0.id == current } ?? 0) + 1 }

    var body: some View {
        ZStack(alignment: .bottom) {
            ScrollView(.vertical) {
                LazyVStack(spacing: 0) {
                    ForEach(batch.files) { file in
                        FeedPage(client: client, batch: batch.batch, file: file, clip: playback.clips[file.id])
                            .containerRelativeFrame([.horizontal, .vertical])
                            .id(file.id)
                    }
                }
                .scrollTargetLayout()
            }
            .scrollTargetBehavior(.paging)
            .scrollPosition(id: $current)
            .scrollIndicators(.hidden)
            .ignoresSafeArea()

            chrome
        }
        .background(.black)
        .toolbar(.hidden, for: .tabBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await saveToPhotos() }
                } label: {
                    if saving { ProgressView() } else { Image(systemName: "arrow.down.to.line") }
                }
                .disabled(saving || currentFile == nil)
                .accessibilityLabel("Save to Photos")
            }
        }
        .onAppear {
            try? PlaybackAudioSession.configure()
            // Coming back after onDisappear tore the players down.
            if playback.current == nil { playback.focus(current, in: batch.files) }
        }
        .onChange(of: current, initial: true) { _, id in playback.focus(id, in: batch.files) }
        .onChange(of: scenePhase) { _, phase in
            phase == .active ? playback.current?.resume() : playback.current?.suspend()
        }
        .onDisappear { playback.stopAll() }
    }

    /// Caption, counter and scrub bar over a scrim, pinned above the home indicator.
    private var chrome: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let toast {
                Text(toast)
                    .font(Theme.sans(13, .medium)).foregroundStyle(Theme.ink)
                    .padding(.horizontal, 14).padding(.vertical, 8)
                    .background(.black.opacity(0.6), in: .capsule)
                    .frame(maxWidth: .infinity)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(currentFile?.name ?? "").font(Theme.mono(13, .medium)).foregroundStyle(Theme.ink)
                    .lineLimit(1)
                Text("\(batch.batch) · \(position)/\(batch.files.count)")
                    .font(Theme.mono(11)).foregroundStyle(Theme.ink.opacity(0.7)).lineLimit(1)
            }
            .shadow(color: .black.opacity(0.5), radius: 4)
            if let clip = playback.current {
                ScrubBar(clip: clip)
            } else {
                Color.clear.frame(height: 24)
            }
        }
        .padding(.horizontal, 16)
        .padding(.bottom, 4)
        .background(alignment: .bottom) {
            LinearGradient(colors: [.clear, .black.opacity(0.55)], startPoint: .top, endPoint: .bottom)
                .frame(height: 180)
                .ignoresSafeArea()
                .allowsHitTesting(false)
        }
        .animation(.easeOut(duration: 0.2), value: toast)
    }

    private func show(_ message: String) {
        toast = message
        Task {
            try? await Task.sleep(for: .seconds(2.5))
            if toast == message { toast = nil }
        }
    }

    private func saveToPhotos() async {
        guard let file = currentFile else { return }
        saving = true
        defer { saving = false }
        guard await PHPhotoLibrary.requestAuthorization(for: .addOnly) == .authorized else {
            show("Photos access denied — allow it in Settings › Motion.")
            return
        }
        do {
            // PHPhotoLibrary needs a local file, so download first.
            let local = try await client.download("v1", "outputs", batch.batch, file.name)
            try await Self.addToPhotos(local, isVideo: file.isVideo)
            try? FileManager.default.removeItem(at: local.deletingLastPathComponent())
            show("Saved to Photos.")
        } catch let e as APIError {
            show(e.userMessage)
        } catch {
            show("Couldn't save: \(error.localizedDescription)")
        }
    }

    /// PhotoKit runs its changes block on a private queue. Keeping the block in
    /// an explicitly nonisolated function avoids inheriting SwiftUI's main
    /// actor, which otherwise trips Swift 6's executor check at runtime.
    private nonisolated static func addToPhotos(_ local: URL, isVideo: Bool) async throws {
        let changes: @Sendable () -> Void = {
            if isVideo {
                PHAssetChangeRequest.creationRequestForAssetFromVideo(atFileURL: local)
            } else {
                PHAssetChangeRequest.creationRequestForAssetFromImage(atFileURL: local)
            }
        }
        try await PHPhotoLibrary.shared().performChanges(changes)
    }
}

/// One full-screen page: a video surface or an image, letterboxed on black.
private struct FeedPage: View {
    let client: APIClient
    let batch: String
    let file: OutputFile
    let clip: FeedClip?
    @State private var image: UIImage?
    @State private var error: APIError?

    var body: some View {
        ZStack {
            Color.black
            if file.isVideo {
                if let clip {
                    PlayerSurface(player: clip.player, fill: clip.isPortrait)
                        .ignoresSafeArea()
                    if clip.buffering && !clip.userPaused { ProgressView().tint(.white) }
                    Image(systemName: "play.fill")
                        .font(.system(size: 56))
                        .foregroundStyle(.white.opacity(0.85))
                        .shadow(color: .black.opacity(0.4), radius: 8)
                        .opacity(clip.userPaused ? 1 : 0)
                        .scaleEffect(clip.userPaused ? 1 : 1.3)
                        .animation(.spring(duration: 0.25), value: clip.userPaused)
                        .allowsHitTesting(false)
                }
            } else if let image {
                Image(uiImage: image).resizable().scaledToFit()
            } else if let error {
                ErrorBanner(error: error).padding(20)
            } else {
                ProgressView().tint(.white)
            }
        }
        .contentShape(.rect)
        .onTapGesture { clip?.togglePause() }
        .task(id: file.id) { await loadImage() }
    }

    private func loadImage() async {
        guard !file.isVideo, image == nil else { return }
        do { image = UIImage(data: try await client.data("v1", "outputs", batch, file.name)) }
        catch { self.error = error }
    }
}

/// AVPlayerLayer without AVKit's controls.
private struct PlayerSurface: UIViewRepresentable {
    let player: AVPlayer
    let fill: Bool

    final class LayerView: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }

    func makeUIView(context: Context) -> LayerView {
        let view = LayerView()
        view.backgroundColor = .black
        view.clipsToBounds = true  // aspect-fill must not bleed into the neighbouring pages
        view.playerLayer.player = player
        return view
    }

    func updateUIView(_ view: LayerView, context: Context) {
        if view.playerLayer.player !== player { view.playerLayer.player = player }
        view.playerLayer.videoGravity = fill ? .resizeAspectFill : .resizeAspect
    }
}

/// Hairline progress that thickens under the finger and shows the time while seeking.
private struct ScrubBar: View {
    let clip: FeedClip
    @State private var dragFraction: Double?

    var body: some View {
        let active = dragFraction != nil
        let fraction = dragFraction ?? clip.fraction
        VStack(spacing: 6) {
            if active {
                Text("\(Self.clock(fraction * clip.duration)) / \(Self.clock(clip.duration))")
                    .font(Theme.mono(13, .medium)).foregroundStyle(Theme.ink)
                    .shadow(color: .black.opacity(0.5), radius: 4)
                    .frame(maxWidth: .infinity)
                    .transition(.opacity)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(.white.opacity(0.25))
                    Capsule().fill(active ? Theme.lime : .white)
                        .frame(width: geo.size.width * fraction)
                }
                .frame(height: active ? 6 : 3)
                .frame(maxHeight: .infinity, alignment: .bottom)
                .contentShape(.rect)
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            let f = min(1, max(0, value.location.x / geo.size.width))
                            if dragFraction == nil { clip.beginScrub() }
                            dragFraction = f
                            clip.scrub(to: f)
                        }
                        .onEnded { value in
                            let f = min(1, max(0, value.location.x / geo.size.width))
                            clip.endScrub(at: f)
                            dragFraction = nil
                        }
                )
            }
            .frame(height: 24)
        }
        .animation(.easeOut(duration: 0.15), value: active)
        .sensoryFeedback(.selection, trigger: active)
    }

    private static func clock(_ seconds: Double) -> String {
        let s = Int(seconds.isFinite ? seconds.rounded(.down) : 0)
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}
