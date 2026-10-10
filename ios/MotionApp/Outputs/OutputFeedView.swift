import AVFoundation
import SwiftUI
import MotionKit

/// Full-screen, vertically paged outputs of one batch, in the style of Shorts:
/// swipe to the next file, videos loop, tap to pause, drag the bar to seek,
/// long-press for Save, Share, speed and Delete.
struct OutputFeedView: View {
    let client: APIClient
    let batch: OutputBatch
    @State private var current: String?
    @State private var playback: FeedPlayback
    @State private var exporter = MediaExporter()
    @State private var showingActions = false
    /// What's still on the server: starts as the batch, loses what Delete takes.
    @State private var files: [OutputFile]
    @State private var confirmDelete = false
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    /// The counter line under the scrub bar, scaled with Dynamic Type so the strip
    /// grows instead of clipping it at the accessibility sizes.
    @ScaledMetric(relativeTo: .body) private var counterHeight: CGFloat = 25

    init(client: APIClient, batch: OutputBatch, startAt file: OutputFile) {
        self.client = client
        self.batch = batch
        _current = State(initialValue: file.id)
        _files = State(initialValue: batch.files)
        _playback = State(initialValue: FeedPlayback(client: client, batch: batch.batch))
    }

    private var currentFile: OutputFile? { files.first { $0.id == current } }
    private var position: Int { (files.firstIndex { $0.id == current } ?? 0) + 1 }

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
                        ForEach(files) { file in
                            FeedPage(client: client, batch: batch.batch, file: file, clip: playback.clips[file.id],
                                     strip: stripHeight + bottomInset,
                                     onMore: { showingActions = true },
                                     onSave: { Task { await save() } },
                                     onShare: { Task { await exporter.share(downloadCurrent) } },
                                     onDelete: { confirmDelete = true })
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
                // Nothing over the top of the video but Back, whose glass keeps
                // it readable on its own: no gradient, and no iOS 26 scroll-edge
                // blur under the bar. Both were there for the Share and Save
                // buttons that moved into the long-press sheet on 2026-09-25.
                .scrollEdgeEffectHidden(true, for: .all)

                chrome
            }
        }
        .background(.black)
        .toolbar(.hidden, for: .tabBar)
        .toolbarBackground(.hidden, for: .navigationBar)
        .navigationBarTitleDisplayMode(.inline)
        // Space plays and pauses from a hardware keyboard, as in every other player.
        .background {
            Button("Play or Pause") { playback.current?.togglePause() }
                .keyboardShortcut(.space, modifiers: [])
                .opacity(0)
                .accessibilityHidden(true)
        }
        .mediaActions(isPresented: $showingActions, exporter: exporter,
                      isVideo: currentFile?.isVideo ?? false,
                      rate: currentFile?.isVideo == true
                          ? Binding(get: { playback.rate }, set: { playback.rate = $0 }) : nil,
                      onDelete: { confirmDelete = true },
                      download: downloadCurrent)
        .confirmationDialog(currentFile?.isVideo == false ? "Delete this image?" : "Delete this video?",
                            isPresented: $confirmDelete, titleVisibility: .visible) {
            Button("Delete", role: .destructive) { Task { await deleteCurrent() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Removed from the VPS. Copies already saved to Photos stay.")
        }
        .onAppear {
            try? PlaybackAudioSession.configure()
            // Coming back after onDisappear tore the players down.
            if playback.current == nil { playback.focus(current, in: files) }
        }
        .onChange(of: current, initial: true) { _, id in
            playback.focus(id, in: files)
            exporter.toast = nil
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
                MediaStatusView(exporter: exporter)
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
                Text("\(position)/\(files.count)")
                    .font(.footnote.monospacedDigit()).foregroundStyle(Theme.secondary).lineLimit(1)
                    .accessibilityLabel("\(position) of \(files.count)")
            }
            .padding(.horizontal, 16)
            .frame(height: stripHeight, alignment: .top)
        }
    }

    /// PHPhotoLibrary and the share sheet both need a local file, so download first.
    private func downloadCurrent() async throws -> URL {
        guard let file = currentFile else { throw CancellationError() }
        return try await client.download("v1", "outputs", batch.batch, file.name)
    }

    /// Photos' order: the next file takes its place, the previous one at the
    /// end of the batch, and the feed closes when nothing is left.
    private func deleteCurrent() async {
        guard let outputs = model.outputs, let file = currentFile,
              let index = files.firstIndex(where: { $0.id == file.id }) else { return }
        let kept = await outputs.delete([OutputsStore.key(batch.batch, file)])
        guard kept.isEmpty else {
            exporter.show(outputs.message ?? "Couldn't delete.")
            outputs.dismissMessage()
            return
        }
        playback.stopAll()
        files.remove(at: index)
        if files.isEmpty { dismiss(); return }
        current = files[min(index, files.count - 1)].id
        playback.focus(current, in: files)
    }

    private func save() async {
        await exporter.saveToPhotos(isVideo: currentFile?.isVideo ?? false, downloadCurrent)
    }
}

/// One full-screen page: the video or image above a black strip of `strip` points.
private struct FeedPage: View {
    let client: APIClient
    let batch: String
    let file: OutputFile
    let clip: FeedClip?
    let strip: CGFloat
    let onMore: () -> Void
    let onSave: () -> Void
    let onShare: () -> Void
    let onDelete: () -> Void
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
        .onLongPressGesture(minimumDuration: 0.35, perform: onMore)
        // Tap-to-pause is a gesture on a bare surface; VoiceOver and Switch Control
        // get the same thing as a named action on one element per page. The error
        // banner keeps its own children so its Retry button stays reachable.
        .accessibilityElement(children: error == nil ? .ignore : .contain)
        .accessibilityLabel(file.name)
        .accessibilityValue(file.isVideo ? (paused ? "Paused" : "Playing") : "")
        .accessibilityAddTraits(file.isVideo ? .startsMediaSession : .isImage)
        .accessibilityAction(named: paused ? "Play" : "Pause") { clip?.togglePause() }
        // The long press has no VoiceOver gesture; its two actions do, directly.
        .accessibilityAction(named: "Save to Photos", onSave)
        .accessibilityAction(named: "Share", onShare)
        .accessibilityAction(named: "Delete", onDelete)
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
