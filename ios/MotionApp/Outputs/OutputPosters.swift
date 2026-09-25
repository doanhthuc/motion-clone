import ImageIO
import MotionKit
import SwiftUI
import UIKit

/// Poster frames for the outputs grid. The server renders each one once with
/// ffmpeg and keeps it in `_final/.posters/` beside the file, so it is deleted
/// with its video (control/outputs.py `poster`, `prune_posters`); the phone only
/// holds decoded images in memory for this launch.
@MainActor
final class OutputPosters {
    static let shared = OutputPosters()

    private var memory: [String: UIImage] = [:]
    private var inFlight: [String: Task<UIImage?, Never>] = [:]

    func cached(batch: String, file: OutputFile) -> UIImage? {
        memory[Self.key(batch, file)]
    }

    func poster(client: APIClient, batch: String, file: OutputFile) async -> UIImage? {
        let key = Self.key(batch, file)
        if let hit = memory[key] { return hit }
        if let running = inFlight[key] { return await running.value }
        let task = Task.detached(priority: .utility) { () -> UIImage? in
            guard let data = try? await client.data("v1", "outputs", batch, file.name, "poster") else { return nil }
            return Self.decode(data)
        }
        inFlight[key] = task
        let result = await task.value
        inFlight[key] = nil
        if let result { memory[key] = result }
        return result
    }

    /// The size is part of the key: a re-run that overwrites `…-2.mp4` gets a new poster.
    private static func key(_ batch: String, _ file: OutputFile) -> String {
        "\(batch)/\(file.name)/\(file.bytes)"
    }

    /// Decoded off the main thread, so a grid scrolling into 12 new tiles doesn't hitch.
    private nonisolated static func decode(_ data: Data) -> UIImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary)
        else { return nil }
        return UIImage(cgImage: image)
    }
}

/// A 9:16 poster tile: the frame, a duration badge for videos, nothing while it loads.
struct OutputPosterTile: View {
    let client: APIClient
    let batch: String
    let file: OutputFile
    @State private var poster: UIImage?
    @State private var failed = false

    var body: some View {
        Rectangle()
            .fill(Theme.surface)
            .aspectRatio(9 / 16, contentMode: .fit)
            .overlay {
                if let poster {
                    Image(uiImage: poster).resizable().scaledToFill()
                } else if failed {
                    Image(systemName: file.isVideo ? "play.rectangle" : "photo")
                        .font(.title3).foregroundStyle(Theme.tertiary)
                }
            }
            .clipped()
            .overlay(alignment: .bottomTrailing) {
                if let duration = file.duration {
                    Text(Self.length(duration))
                        .font(.caption.weight(.semibold).monospacedDigit())
                        .foregroundStyle(.white)
                        .shadow(color: .black.opacity(0.6), radius: 3)
                        .padding(6)
                }
            }
            .contentShape(.rect)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(file.name)
            .accessibilityValue(file.duration.map { "Video, " + Self.length($0) } ?? (file.isVideo ? "Video" : "Image"))
            .task(id: file.id) {
                if poster == nil { poster = OutputPosters.shared.cached(batch: batch, file: file) }
                guard poster == nil else { return }
                poster = await OutputPosters.shared.poster(client: client, batch: batch, file: file)
                failed = poster == nil
            }
    }

    /// "0:12", the Photos badge; `Format.clock`'s "00:12" is a running timer's shape.
    private static func length(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60)
                         : String(format: "%d:%02d", s / 60, s % 60)
    }
}
