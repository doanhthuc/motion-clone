import AVKit
@preconcurrency import Photos
import SwiftUI
import MotionKit

struct OutputPlayerView: View {
    let client: APIClient
    let batch: String
    let file: OutputFile
    @State private var player: AVPlayer?
    @State private var resourceLoader: AuthenticatedAssetResourceLoader?
    @State private var image: UIImage?
    @State private var error: APIError?
    @State private var saving = false
    @State private var saved: String?

    var body: some View {
        VStack(spacing: 16) {
            Group {
                if file.isVideo, let player {
                    VideoPlayer(player: player)
                } else if let image {
                    Image(uiImage: image).resizable().scaledToFit()
                } else if let error {
                    ErrorBanner(error: error)
                } else {
                    ProgressView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipShape(.rect(cornerRadius: 16))

            Button {
                Task { await saveToPhotos() }
            } label: {
                HStack { if saving { ProgressView().tint(Theme.limeInk) }; Text("Save to Photos") }
                    .font(Theme.sans(15, .bold)).foregroundStyle(Theme.limeInk)
                    .frame(maxWidth: .infinity).frame(height: 54)
                    .background(Theme.lime, in: .rect(cornerRadius: 15))
            }
            .disabled(saving)
            if let saved { Text(saved).font(Theme.mono(12)).foregroundStyle(Theme.ink2) }
        }
        .padding(20)
        .background(Theme.bg)
        .navigationTitle(file.name)
        .navigationBarTitleDisplayMode(.inline)
        .task { await load() }
        .onDisappear {
            player?.pause()
            resourceLoader?.cancelAll()
        }
    }

    private func load() async {
        if file.isVideo {
            try? PlaybackAudioSession.configure()
            let loader = AuthenticatedAssetResourceLoader(client: client, batch: batch, fileName: file.name)
            resourceLoader = loader
            let p = AVPlayer(playerItem: AVPlayerItem(asset: loader.makeAsset()))
            player = p
            p.play()
        } else {
            do { image = UIImage(data: try await client.data("v1", "outputs", batch, file.name)) }
            catch { self.error = error }
        }
    }

    private func saveToPhotos() async {
        saving = true
        defer { saving = false }
        guard await PHPhotoLibrary.requestAuthorization(for: .addOnly) == .authorized else {
            saved = "Photos access denied — allow it in Settings › Motion."
            return
        }
        do {
            // PHPhotoLibrary needs a local file, so download first.
            let local = try await client.download("v1", "outputs", batch, file.name)
            try await Self.addToPhotos(local, isVideo: file.isVideo)
            try? FileManager.default.removeItem(at: local.deletingLastPathComponent())
            saved = "Saved to Photos."
        } catch let e as APIError {
            saved = e.userMessage
        } catch {
            saved = "Couldn't save: \(error.localizedDescription)"
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
