import AVFoundation
import MotionKit
import Observation
import PhotosUI
import SwiftUI
import UIKit

/// Several photos or files uploaded one after another, each filed under the
/// role chosen before the pick. `MaterialsStore` runs one upload at a time
/// (its journal resumes a single interrupted upload), so this is a queue
/// rather than parallel uploads.
///
/// Owned by the Materials tab and shared with its See all screens, so a
/// batch keeps going, and stays visible, when the user moves between them.
@MainActor @Observable
final class MaterialUploadQueue {
    enum State: Equatable {
        case waiting, uploading, done, failed(String)
    }

    struct Item: Identifiable {
        let id = UUID()
        fileprivate let source: Source
        var name: String
        var thumbnail: UIImage?
        var state: State = .waiting
    }

    fileprivate enum Source {
        case photo(PhotosPickerItem)
        case file(URL)
    }

    private(set) var items: [Item] = []
    /// Where this batch is being filed; nil leaves images Unsorted.
    private(set) var role: MaterialRole?
    private let store: MaterialsStore
    private var runner: Task<Void, Never>?
    private var autoClear: Task<Void, Never>?

    init(store: MaterialsStore) {
        self.store = store
    }

    var isRunning: Bool { runner != nil }
    var total: Int { items.count }
    var doneCount: Int { items.count { $0.state == .done } }
    var failedCount: Int { items.count { if case .failed = $0.state { true } else { false } } }
    var current: Item? { items.first { $0.state == .uploading } }

    /// The store's progress while it belongs to the current item. Between
    /// items it still holds the previous file at 100%.
    var currentProgress: UploadProgress? {
        guard let current, let progress = store.uploadProgress,
              progress.fileName == current.name else { return nil }
        return progress
    }

    /// 0…1 of the current item's bytes.
    var currentFraction: Double {
        guard let progress = currentProgress, progress.totalBytes > 0 else { return 0 }
        return Double(progress.bytesSent) / Double(progress.totalBytes)
    }

    /// 0…1 across the whole batch: finished items plus the current one's bytes.
    var fraction: Double {
        guard total > 0 else { return 0 }
        let finished = Double(items.count { $0.state != .waiting && $0.state != .uploading })
        return min(1, (finished + currentFraction) / Double(total))
    }

    func add(photos: [PhotosPickerItem], role: MaterialRole?) {
        enqueue(photos.enumerated().map { index, item in
            Item(source: .photo(item), name: "Item \(index + 1)")
        }, role: role)
    }

    func add(files: [URL], role: MaterialRole?) {
        enqueue(files.map { Item(source: .file($0), name: $0.lastPathComponent) }, role: role)
    }

    /// Hides a finished batch. A running one is never cleared from here.
    func clear() {
        guard !isRunning else { return }
        autoClear?.cancel()
        items = []
    }

    private func enqueue(_ new: [Item], role: MaterialRole?) {
        guard !new.isEmpty, !isRunning else { return }
        autoClear?.cancel()
        items = new
        self.role = role
        runner = Task { await run() }
    }

    private func run() async {
        for index in items.indices {
            // A failed upload that left a resumable checkpoint blocks every
            // later start (`UploadFailure.uploadInProgress`): stop here and
            // let the Materials banner offer Retry / Discard.
            if store.hasPendingUpload {
                items[index].state = .failed("Not started")
                continue
            }
            items[index].state = .uploading
            items[index].state = await upload(index)
        }
        runner = nil
        if failedCount == 0 {
            autoClear = Task {
                try? await Task.sleep(for: .seconds(2.5))
                guard !Task.isCancelled else { return }
                withAnimation(.snappy) { items = [] }
            }
        }
    }

    private func upload(_ index: Int) async -> State {
        let staged: ImportedMedia
        do {
            switch items[index].source {
            case .photo(let item):
                guard let media = try await item.loadTransferable(type: ImportedMedia.self) else {
                    return .failed("Couldn't read this item")
                }
                staged = media
            case .file(let url):
                staged = try await ImportStaging.stageAsync(url, securityScoped: true)
            }
        } catch {
            return .failed(error.localizedDescription)
        }
        defer { staged.removeStagedCopy() }
        items[index].name = staged.fileName
        items[index].thumbnail = await Self.thumbnail(of: staged.url)

        guard let material = await store.startUpload(fileURL: staged.url, fileName: staged.fileName) else {
            let message = store.errorMessage ?? "Upload failed"
            // Kept on the store only when it can resume: the banner is the
            // one place with Retry / Discard. Otherwise it shows here, once.
            if !store.hasPendingUpload { store.clearError() }
            return .failed(message)
        }
        if let role, MaterialRole.options(for: material.kind).contains(role) {
            await store.setRole(role, for: material)
        }
        return .done
    }

    /// A small preview from the staged copy, off the main thread.
    private static func thumbnail(of url: URL) async -> UIImage? {
        let side: CGFloat = 120
        if let image = await Task.detached(priority: .utility, operation: {
            UIImage(contentsOfFile: url.path)?.preparingThumbnail(of: CGSize(width: side, height: side * 1.25))
        }).value {
            return image
        }
        let generator = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: side * 2, height: side * 2)
        guard let frame = try? await generator.image(at: .zero).image else { return nil }
        return UIImage(cgImage: frame)
    }
}
