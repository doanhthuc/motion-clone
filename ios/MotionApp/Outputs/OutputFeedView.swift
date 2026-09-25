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
    @State private var busy: Busy?
    @State private var toast: Toast?
    @State private var sharing: SharedFile?
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// The counter line under the scrub bar, scaled with Dynamic Type so the strip
    /// grows instead of clipping it at the accessibility sizes.
    @ScaledMetric(relativeTo: .body) private var counterHeight: CGFloat = 25

    private enum Busy { case saving, sharing }

    private struct SharedFile: Identifiable {
        let url: URL
        var id: URL { url }
    }

    private struct Toast: Equatable {
        let message: String
        /// A denied Photos permission: stays until dismissed and offers the way out.
        var opensSettings = false
    }

    init(client: APIClient, batch: OutputBatch, startAt file: OutputFile) {
        self.client = client
        self.batch = batch
        _current = State(initialValue: file.id)
        _playback = State(initialValue: FeedPlayback(client: client, batch: batch.batch))
    }

    private var currentFile: OutputFile? { batch.files.first { $0.id == current } }
    private var position: Int { (batch.files.firstIndex { $0.id == current } ?? 0) + 1 }

    /// TikTok's layout: the video owns the screen down to a black strip the height
    /// of a tab bar, and the scrub bar and counter live in that strip. Filling
    /// this shorter area crops ~10% of a 9:16 clip's width on a 402×874pt
    /// iPhone 17 Pro, against ~18% when filling the whole screen. 49pt at the
    /// default text size: the scrub bar's 24pt plus the counter line.
    private var stripHeight: CGFloat { ScrubBar.height + counterHeight }

    var body: some View {
        GeometryReader { geo in
            let bottomInset = geo.safeAreaInsets.bottom
            ZStack(alignment: .bottom) {
                ScrollView(.vertical) {
                    LazyVStack(spacing: 0) {
                        ForEach(batch.files) { file in
                            FeedPage(client: client, batch: batch.batch, file: file, clip: playback.clips[file.id],
                                     strip: stripHeight + bottomInset)
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
                // Keeps the lime Back button readable over a bright frame.
                .overlay(alignment: .top) {
                    LinearGradient(colors: [.black.opacity(0.45), .clear], startPoint: .top, endPoint: .bottom)
                        .frame(height: geo.safeAreaInsets.top + 64)
                        .offset(y: -geo.safeAreaInsets.top)
                        .allowsHitTesting(false)
                }

                chrome
            }
        }
        .background(.black)
        .toolbar(.hidden, for: .tabBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    Task { await share() }
                } label: {
                    if busy == .sharing { ProgressView() } else { Image(systemName: "square.and.arrow.up") }
                }
                .disabled(busy != nil || currentFile == nil)
                .accessibilityLabel("Share")
                Button {
                    Task { await saveToPhotos() }
                } label: {
                    if busy == .saving { ProgressView() } else { Image(systemName: "arrow.down.to.line") }
                }
                .disabled(busy != nil || currentFile == nil)
                .accessibilityLabel("Save to Photos")
            }
        }
        // Space plays and pauses from a hardware keyboard, as in every other player.
        .background {
            Button("Play or Pause") { playback.current?.togglePause() }
                .keyboardShortcut(.space, modifiers: [])
                .opacity(0)
                .accessibilityHidden(true)
        }
        .sheet(item: $sharing) { shared in
            ActivityView(items: [shared.url])
                .presentationDetents([.medium, .large])
                .onDisappear { try? FileManager.default.removeItem(at: shared.url.deletingLastPathComponent()) }
        }
        .onAppear {
            try? PlaybackAudioSession.configure()
            // Coming back after onDisappear tore the players down.
            if playback.current == nil { playback.focus(current, in: batch.files) }
        }
        .onChange(of: current, initial: true) { _, id in
            playback.focus(id, in: batch.files)
            toast = nil
        }
        .onChange(of: scenePhase) { _, phase in
            phase == .active ? playback.current?.resume() : playback.current?.suspend()
        }
        .onDisappear { playback.stopAll() }
    }

    /// Caption over the bottom of the video, then the scrub bar and counter in the strip.
    private var chrome: some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                if let toast { toastView(toast) }
                // Files in a batch share their prefix and differ at the end (…-2.mp4),
                // so a long name at large text sizes gives up its middle.
                Text(currentFile?.name ?? "").font(.subheadline.weight(.medium).monospacedDigit()).foregroundStyle(Theme.label)
                    .lineLimit(1).truncationMode(.middle)
                    .shadow(color: .black.opacity(0.5), radius: 4)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 4)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(alignment: .bottom) {
                LinearGradient(colors: [.clear, .black.opacity(0.5)], startPoint: .top, endPoint: .bottom)
                    .frame(height: 140)
                    .allowsHitTesting(false)
            }

            VStack(alignment: .leading, spacing: 0) {
                if let clip = playback.current {
                    ScrubBar(clip: clip)
                } else {
                    Color.clear.frame(height: ScrubBar.height)
                }
                // The batch name is on the screen that opened this feed; the position is what's new here.
                Text("\(position)/\(batch.files.count)")
                    .font(.footnote.monospacedDigit()).foregroundStyle(Theme.secondary).lineLimit(1)
                    .accessibilityLabel("\(position) of \(batch.files.count)")
            }
            .padding(.horizontal, 16)
            .frame(height: stripHeight, alignment: .top)
        }
        .animation(.easeOut(duration: 0.2), value: toast)
    }

    private func toastView(_ toast: Toast) -> some View {
        HStack(spacing: 12) {
            Text(toast.message)
                .font(.subheadline.weight(.medium)).foregroundStyle(Theme.label)
            if toast.opensSettings {
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                }
                .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.accent)
                .frame(minHeight: 44)
                Button { self.toast = nil } label: {
                    Image(systemName: "xmark").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.secondary)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Dismiss")
            }
        }
        .padding(.leading, 14).padding(.trailing, toast.opensSettings ? 0 : 14).padding(.vertical, toast.opensSettings ? 0 : 8)
        .background(.black.opacity(0.6), in: .capsule)
        .frame(maxWidth: .infinity)
        .transition(reduceMotion ? .opacity : .opacity.combined(with: .move(edge: .bottom)))
    }

    /// Success fades on its own; an error that needs a trip to Settings waits for the
    /// person, because a timer is too short for anyone who reads slowly. Either way
    /// VoiceOver hears it, since the toast never takes focus.
    private func show(_ message: String, opensSettings: Bool = false) {
        let next = Toast(message: message, opensSettings: opensSettings)
        toast = next
        AccessibilityNotification.Announcement(message).post()
        guard !opensSettings else { return }
        Task {
            try? await Task.sleep(for: .seconds(2.5))
            if toast == next { toast = nil }
        }
    }

    /// PHPhotoLibrary and the share sheet both need a local file, so download first.
    private func downloadCurrent() async throws -> (URL, OutputFile)? {
        guard let file = currentFile else { return nil }
        return (try await client.download("v1", "outputs", batch.batch, file.name), file)
    }

    private func share() async {
        busy = .sharing
        defer { busy = nil }
        do {
            guard let (local, _) = try await downloadCurrent() else { return }
            sharing = SharedFile(url: local)
        } catch let e as APIError {
            show(e.userMessage)
        } catch {
            show("Couldn't share: \(error.localizedDescription)")
        }
    }

    private func saveToPhotos() async {
        guard currentFile != nil else { return }
        busy = .saving
        defer { busy = nil }
        guard await PHPhotoLibrary.requestAuthorization(for: .addOnly) == .authorized else {
            show("Photos access is off for Motion.", opensSettings: true)
            return
        }
        do {
            guard let (local, file) = try await downloadCurrent() else { return }
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

/// One full-screen page: the video or image above a black strip of `strip` points.
private struct FeedPage: View {
    let client: APIClient
    let batch: String
    let file: OutputFile
    let clip: FeedClip?
    let strip: CGFloat
    @State private var image: UIImage?
    @State private var error: APIError?

    private var paused: Bool { clip?.userPaused ?? false }

    var body: some View {
        VStack(spacing: 0) {
            media.frame(maxWidth: .infinity, maxHeight: .infinity).clipped()
            Color.black.frame(height: strip)
        }
        .background(.black)
        .contentShape(.rect)
        .onTapGesture { clip?.togglePause() }
        // Tap-to-pause is a gesture on a bare surface; VoiceOver and Switch Control
        // get the same thing as a named action on one element per page. The error
        // banner keeps its own children so its Retry button stays reachable.
        .accessibilityElement(children: error == nil ? .ignore : .contain)
        .accessibilityLabel(file.name)
        .accessibilityValue(file.isVideo ? (paused ? "Paused" : "Playing") : "")
        .accessibilityAddTraits(file.isVideo ? .startsMediaSession : .isImage)
        .accessibilityAction(named: paused ? "Play" : "Pause") { clip?.togglePause() }
        .task(id: file.id) { await loadImage() }
    }

    private var media: some View {
        ZStack {
            Color.black
            if file.isVideo {
                if let clip { ClipSurface(clip: clip) }
            } else if let image {
                Image(uiImage: image).resizable().scaledToFit()
            } else if let error {
                ErrorBanner(error: error) {
                    self.error = nil
                    await loadImage()
                }
                .padding(20)
            } else {
                ProgressView().tint(.white)
            }
        }
    }

    private func loadImage() async {
        guard !file.isVideo, image == nil else { return }
        do { image = UIImage(data: try await client.data("v1", "outputs", batch, file.name)) }
        catch { self.error = error }
    }
}

/// The system share sheet, which SwiftUI's ShareLink can't be used for here: the
/// file has to be downloaded before there is anything to share.
private struct ActivityView: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
