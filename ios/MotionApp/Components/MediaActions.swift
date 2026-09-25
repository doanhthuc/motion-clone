import MotionKit
@preconcurrency import Photos
import SwiftUI

/// Save to Photos and Share for one full-screen video or image, shared by the
/// Outputs feed and the material preview. Both need a local file first, so
/// each downloads, then hands the file to PhotoKit or the share sheet.
@MainActor @Observable
final class MediaExporter {
    enum Busy { case saving, sharing }

    struct Toast: Equatable {
        let message: String
        /// A denied Photos permission: stays until dismissed and offers the way out.
        var opensSettings = false
    }

    struct SharedFile: Identifiable {
        let url: URL
        var id: URL { url }
    }

    private(set) var busy: Busy?
    var toast: Toast?
    var sharing: SharedFile?

    func share(_ download: @escaping () async throws -> URL) async {
        busy = .sharing
        defer { busy = nil }
        do {
            sharing = SharedFile(url: try await download())
        } catch let e as APIError {
            show(e.userMessage)
        } catch {
            show("Couldn't share: \(error.localizedDescription)")
        }
    }

    func saveToPhotos(isVideo: Bool, _ download: @escaping () async throws -> URL) async {
        busy = .saving
        defer { busy = nil }
        guard await PHPhotoLibrary.requestAuthorization(for: .addOnly) == .authorized else {
            show("Photos access is off for Motion.", opensSettings: true)
            return
        }
        do {
            let local = try await download()
            try await Self.addToPhotos(local, isVideo: isVideo)
            try? FileManager.default.removeItem(at: local.deletingLastPathComponent())
            show("Saved to Photos.")
        } catch let e as APIError {
            show(e.userMessage)
        } catch {
            show("Couldn't save: \(error.localizedDescription)")
        }
    }

    /// Success fades on its own; an error that needs a trip to Settings waits for the
    /// person, because a timer is too short for anyone who reads slowly. Either way
    /// VoiceOver hears it, since the toast never takes focus.
    func show(_ message: String, opensSettings: Bool = false) {
        let next = Toast(message: message, opensSettings: opensSettings)
        toast = next
        AccessibilityNotification.Announcement(message).post()
        guard !opensSettings else { return }
        Task {
            try? await Task.sleep(for: .seconds(2.5))
            if toast == next { toast = nil }
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

/// What a long press on the media opens, in the shape TikTok uses: grouped rows
/// in a sheet that rises from the bottom. It replaced the Share and Save buttons
/// in the navigation bar on 2026-09-25; those sat over the top of the video,
/// under a gradient and the bar's scroll-edge blur, to keep them readable.
struct MediaActionSheet: View {
    let isVideo: Bool
    /// `nil` for an image, which has no speed.
    let rate: Binding<Float>?
    let onSave: () -> Void
    let onShare: () -> Void

    static let rates: [Float] = [0.5, 1, 1.5, 2]

    var body: some View {
        List {
            Section {
                Button(action: onSave) {
                    Label(isVideo ? "Save video" : "Save image", systemImage: "arrow.down.to.line")
                }
                .accessibilityIdentifier("media.save")
                .listRowBackground(Theme.surfaceRaised)
                Button(action: onShare) {
                    Label("Share", systemImage: "square.and.arrow.up")
                }
                .accessibilityIdentifier("media.share")
                .listRowBackground(Theme.surfaceRaised)
            }
            if let rate {
                Section {
                    HStack(spacing: 12) {
                        Label("Speed", systemImage: "gauge.with.dots.needle.33percent")
                            .layoutPriority(1)
                        Spacer(minLength: 8)
                        Picker("Speed", selection: rate) {
                            ForEach(Self.rates, id: \.self) { value in
                                Text(Self.label(value)).tag(value)
                            }
                        }
                        .pickerStyle(.segmented)
                        .frame(maxWidth: 200)
                        .accessibilityIdentifier("media.speed")
                    }
                    .listRowBackground(Theme.surfaceRaised)
                }
            }
        }
        .foregroundStyle(Theme.label)
        .tint(Theme.label)
        .listSectionSpacing(12)
        .contentMargins(.top, 8, for: .scrollContent)
        .scrollDisabled(true)
        // Opaque, with lighter groups on it, as TikTok's sheet is: the default
        // iOS 26 glass let the video show through and the rows went muddy.
        .scrollContentBackground(.hidden)
        .presentationBackground(Theme.surface)
        .presentationDetents([.height(rate == nil ? 170 : 236)])
        .presentationDragIndicator(.visible)
    }

    /// 1 → "1.0×", 1.5 → "1.5×", as TikTok labels them.
    static func label(_ rate: Float) -> String {
        String(format: "%.1f×", rate)
    }
}

extension View {
    /// The long-press sheet plus what it leads to: the share sheet and the
    /// toast. The chosen action runs once the sheet has gone, because the share
    /// sheet cannot present while another sheet is still on screen.
    func mediaActions(isPresented: Binding<Bool>, exporter: MediaExporter, isVideo: Bool,
                      rate: Binding<Float>?, download: @escaping () async throws -> URL) -> some View {
        modifier(MediaActionsModifier(isPresented: isPresented, exporter: exporter, isVideo: isVideo,
                                      rate: rate, download: download))
    }
}

private struct MediaActionsModifier: ViewModifier {
    @Binding var isPresented: Bool
    let exporter: MediaExporter
    let isVideo: Bool
    let rate: Binding<Float>?
    let download: () async throws -> URL
    @State private var pending: Pending?

    private enum Pending { case save, share }

    func body(content: Content) -> some View {
        content
            .sensoryFeedback(.impact(weight: .medium), trigger: isPresented) { _, shown in shown }
            .sheet(isPresented: $isPresented, onDismiss: runPending) {
                MediaActionSheet(isVideo: isVideo, rate: rate,
                                 onSave: { choose(.save) }, onShare: { choose(.share) })
            }
            .sheet(item: Binding(get: { exporter.sharing }, set: { exporter.sharing = $0 })) { shared in
                ActivityView(items: [shared.url])
                    .presentationDetents([.medium, .large])
                    .onDisappear { try? FileManager.default.removeItem(at: shared.url.deletingLastPathComponent()) }
            }
    }

    private func choose(_ action: Pending) {
        guard exporter.busy == nil else { return }
        pending = action
        isPresented = false
    }

    private func runPending() {
        guard let action = pending else { return }
        pending = nil
        Task {
            switch action {
            case .save: await exporter.saveToPhotos(isVideo: isVideo, download)
            case .share: await exporter.share(download)
            }
        }
    }
}

/// The feedback line over the bottom of the media: a spinner while the file
/// downloads (the bar buttons used to spin in its place), then "Saved to
/// Photos.", or the way to Settings when Photos access is off.
struct MediaStatusView: View {
    let exporter: MediaExporter
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Group {
            if let busy = exporter.busy {
                HStack(spacing: 10) {
                    ProgressView().tint(.white)
                    Text(busy == .saving ? "Saving…" : "Preparing…")
                        .font(.subheadline.weight(.medium)).foregroundStyle(Theme.label)
                }
                .padding(.horizontal, 14).padding(.vertical, 8)
                .background(.black.opacity(0.6), in: .capsule)
                .frame(maxWidth: .infinity)
            } else if let toast = exporter.toast {
                toastView(toast)
            }
        }
        .transition(reduceMotion ? .opacity : .opacity.combined(with: .move(edge: .bottom)))
        .animation(.easeOut(duration: 0.2), value: exporter.toast)
        .animation(.easeOut(duration: 0.2), value: exporter.busy)
    }

    private func toastView(_ toast: MediaExporter.Toast) -> some View {
        HStack(spacing: 12) {
            Text(toast.message)
                .font(.subheadline.weight(.medium)).foregroundStyle(Theme.label)
            if toast.opensSettings {
                Button("Open Settings") {
                    if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
                }
                .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.accent)
                .frame(minHeight: 44)
                Button { exporter.toast = nil } label: {
                    Image(systemName: "xmark").font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.secondary)
                        .frame(width: 44, height: 44)
                }
                .accessibilityLabel("Dismiss")
            }
        }
        .padding(.leading, 14).padding(.trailing, toast.opensSettings ? 0 : 14).padding(.vertical, toast.opensSettings ? 0 : 8)
        .background(.black.opacity(0.6), in: .capsule)
        .frame(maxWidth: .infinity)
    }
}

/// The system share sheet, which SwiftUI's ShareLink can't be used for here: the
/// file has to be downloaded before there is anything to share.
struct ActivityView: UIViewControllerRepresentable {
    let items: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: items, applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
