import AVFoundation
import ImageIO
import MotionKit
import SwiftUI
import UIKit

/// Poster frames for the outputs grid, made on the phone from the file itself.
/// The server has no output thumbnails, and adding ffmpeg work to the 1 GB
/// droplet for a grid is the thing `materials.thumbnail` already caps at two at
/// a time. A poster here costs a few range requests through the same
/// authenticated loader the feed plays with, then lives in Caches.
@MainActor
final class OutputPosters {
    static let shared = OutputPosters()

    struct Poster: @unchecked Sendable {
        let image: UIImage
        /// Seconds; nil for a still image.
        let duration: Double?
    }

    private var memory: [String: Poster] = [:]
    private var inFlight: [String: Task<Poster?, Never>] = [:]
    /// Three at a time: a grid of 12 asking at once would queue 12 range
    /// streams behind the tunnel and none would finish first.
    private let gate = Gate(limit: 3)
    private let dir = URL.cachesDirectory.appending(path: "output-posters", directoryHint: .isDirectory)

    func cached(batch: String, file: OutputFile) -> Poster? {
        memory[Self.key(batch, file)]
    }

    func poster(client: APIClient, batch: String, file: OutputFile) async -> Poster? {
        let key = Self.key(batch, file)
        if let hit = memory[key] { return hit }
        if let running = inFlight[key] { return await running.value }
        let dir = self.dir, gate = self.gate
        let task = Task.detached(priority: .utility) { () -> Poster? in
            if let disk = Self.read(dir, key) { return disk }
            await gate.wait()
            let made = await Self.make(client: client, batch: batch, file: file)
            await gate.signal()
            if let made { Self.write(made, dir, key) }
            return made
        }
        inFlight[key] = task
        let result = await task.value
        inFlight[key] = nil
        if let result { memory[key] = result }
        return result
    }

    /// The size is part of the key: a re-run that overwrites `…-2.mp4` gets a new poster.
    private static func key(_ batch: String, _ file: OutputFile) -> String {
        "\(batch)__\(file.name)__\(file.bytes)"
            .addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? UUID().uuidString
    }

    private nonisolated static func make(client: APIClient, batch: String, file: OutputFile) async -> Poster? {
        if file.isVideo {
            let loader = AuthenticatedAssetResourceLoader(client: client, batch: batch, fileName: file.name)
            defer { loader.cancelAll() }
            let asset = loader.makeAsset()
            let generator = AVAssetImageGenerator(asset: asset)
            generator.appliesPreferredTrackTransform = true
            generator.maximumSize = CGSize(width: 480, height: 480)
            // Any keyframe near the start: an exact frame would decode from the
            // previous keyframe and fetch more bytes for a 130pt tile.
            generator.requestedTimeToleranceBefore = CMTime(seconds: 0.5, preferredTimescale: 600)
            generator.requestedTimeToleranceAfter = CMTime(seconds: 2, preferredTimescale: 600)
            guard let (cgImage, _) = try? await generator.image(at: CMTime(seconds: 0.5, preferredTimescale: 600))
            else { return nil }
            let duration = try? await asset.load(.duration).seconds
            return Poster(image: UIImage(cgImage: cgImage), duration: duration.flatMap { $0.isFinite ? $0 : nil })
        }
        guard let data = try? await client.data("v1", "outputs", batch, file.name),
              let source = CGImageSourceCreateWithData(data as CFData, nil),
              let cgImage = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                  kCGImageSourceCreateThumbnailFromImageAlways: true,
                  kCGImageSourceCreateThumbnailWithTransform: true,
                  kCGImageSourceThumbnailMaxPixelSize: 480,
              ] as CFDictionary)
        else { return nil }
        return Poster(image: UIImage(cgImage: cgImage), duration: nil)
    }

    private nonisolated static func read(_ dir: URL, _ key: String) -> Poster? {
        guard let data = try? Data(contentsOf: dir.appending(path: "\(key).jpg")),
              let image = UIImage(data: data) else { return nil }
        let duration = (try? String(contentsOf: dir.appending(path: "\(key).dur"), encoding: .utf8))
            .flatMap(Double.init)
        return Poster(image: image, duration: duration)
    }

    private nonisolated static func write(_ poster: Poster, _ dir: URL, _ key: String) {
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try? poster.image.jpegData(compressionQuality: 0.8)?.write(to: dir.appending(path: "\(key).jpg"))
        if let duration = poster.duration {
            try? String(duration).write(to: dir.appending(path: "\(key).dur"), atomically: true, encoding: .utf8)
        }
    }

    private actor Gate {
        private var available: Int
        private var waiters: [CheckedContinuation<Void, Never>] = []
        init(limit: Int) { available = limit }

        func wait() async {
            if available > 0 { available -= 1; return }
            await withCheckedContinuation { waiters.append($0) }
        }

        func signal() {
            if waiters.isEmpty { available += 1 } else { waiters.removeFirst().resume() }
        }
    }
}

/// A 9:16 poster tile: the frame, a duration badge for videos, nothing while it loads.
struct OutputPosterTile: View {
    let client: APIClient
    let batch: String
    let file: OutputFile
    @State private var poster: OutputPosters.Poster?
    @State private var failed = false

    var body: some View {
        Rectangle()
            .fill(Theme.surface)
            .aspectRatio(9 / 16, contentMode: .fit)
            .overlay {
                if let poster {
                    Image(uiImage: poster.image).resizable().scaledToFill()
                } else if failed {
                    Image(systemName: file.isVideo ? "play.rectangle" : "photo")
                        .font(.title3).foregroundStyle(Theme.tertiary)
                }
            }
            .clipped()
            .overlay(alignment: .bottomTrailing) {
                if let duration = poster?.duration {
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
            .accessibilityValue(poster?.duration.map { "Video, " + Self.length($0) } ?? (file.isVideo ? "Video" : "Image"))
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
