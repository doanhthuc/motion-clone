import Foundation
import Observation

@MainActor @Observable
public final class MaterialsStore {
    public private(set) var materials: [Material] = []
    public private(set) var loaded = false
    public private(set) var lastSuccess: Date?
    public private(set) var errorMessage: String?
    public private(set) var uploadProgress: UploadProgress?
    public private(set) var isUploading = false
    public private(set) var hasPendingUpload = false
    public private(set) var isImportingLink = false
    /// True only after `importLink` fails with Cloudflare's 524 (the server
    /// kept downloading past the tunnel's timeout) — a caller that wants to
    /// show that case as informational rather than a failure reads this
    /// instead of pattern-matching `errorMessage`'s text (2026-09-25, used by
    /// the share extension's still-running card).
    public private(set) var linkImportStillRunning = false

    /// Public so a view can build an authenticated player for a material.
    public let client: APIClient
    private let uploader: Uploader
    private var thumbnails: [String: Data] = [:]
    private var warnings: [String: String] = [:]

    public init(client: APIClient, uploader: Uploader? = nil) {
        self.client = client
        self.uploader = uploader ?? Uploader(client: client)
    }

    public var isStale: Bool { loaded && errorMessage != nil }

    public func warning(for materialID: String) -> String? {
        warnings[materialID]
    }

    public func clearError() {
        errorMessage = nil
    }

    public func refresh() async {
        do {
            materials = try await client.get(MaterialsResponse.self, "v1", "materials").materials
            loaded = true
            lastSuccess = .now
            errorMessage = nil
        } catch {
            errorMessage = Self.message(for: error)
        }
    }

    public func thumbnail(for material: Material) async -> Data? {
        if let cached = thumbnails[material.id] { return cached }
        do {
            let data = try await client.data(
                "v1", "materials", material.owner, material.name, "thumb")
            thumbnails[material.id] = data
            return data
        } catch {
            return nil
        }
    }

    /// The new material on success, so a picker that started the upload can
    /// select it; nil after a failure, which `errorMessage` describes.
    @discardableResult
    public func startUpload(fileURL: URL, fileName: String) async -> Material? {
        guard !isUploading else {
            errorMessage = "An upload is already in progress."
            return nil
        }
        isUploading = true
        errorMessage = nil
        defer { isUploading = false }
        do {
            let completed = try await uploader.start(
                fileURL: fileURL, fileName: fileName, progress: progressHandler())
            accept(completed)
            hasPendingUpload = false
            await refresh()
            return completed.material
        } catch {
            hasPendingUpload = await uploader.hasPendingUpload()
            errorMessage = Self.message(for: error)
            return nil
        }
    }

    /// Has the server download a TikTok video into the materials. The server
    /// holds the request open for the whole download (`scripts/control/links.py`
    /// budgets 40 s per download path), so the timeout here is longer than
    /// Cloudflare's 100 s cut. A cut or transport error does not mean the
    /// download failed — it may still land — so the list is re-read either way.
    @discardableResult
    public func importLink(_ text: String) async -> Material? {
        guard let link = TikTokLink.find(in: text) else {
            errorMessage = "That isn't a TikTok link."
            return nil
        }
        guard !isImportingLink else { return nil }
        isImportingLink = true
        errorMessage = nil
        linkImportStillRunning = false
        defer { isImportingLink = false }
        do {
            let completed = try await client.post(
                UploadCompleteResponse.self, body: LinkImportRequest(url: link),
                timeout: 120, "v1", "materials", "link")
            accept(completed)
            await refresh()
            return completed.material
        } catch {
            // 524 is Cloudflare giving up on the open request, not the server
            // failing: the download carries on and lands in the list.
            let stillRunning: Bool
            let message: String
            if case .server(status: 524, _, _) = error {
                stillRunning = true
                message = "The download is still running. It will appear in Materials when it finishes."
            } else {
                stillRunning = false
                message = error.userMessage
            }
            // refresh() runs first because it can overwrite errorMessage (nil
            // on success, its own message on failure) — this catch's message
            // must be what's left standing, same ordering the code had before
            // `linkImportStillRunning` existed.
            await refresh()
            linkImportStillRunning = stillRunning
            errorMessage = message
            return nil
        }
    }

    /// Files `material` under `role` (nil hands it back to the server's
    /// history). The row is replaced with the server's answer.
    public func setRole(_ role: MaterialRole?, for material: Material) async {
        do {
            let updated = try await client.put(
                MaterialResponse.self, body: MaterialRoleRequest(role: role),
                "v1", "materials", material.owner, material.name, "role").material
            if let index = materials.firstIndex(where: { $0.id == updated.id }) {
                materials[index] = updated
            }
            errorMessage = nil
        } catch {
            errorMessage = error.userMessage
        }
    }

    public func resumePendingUpload() async {
        guard !isUploading else { return }
        hasPendingUpload = await uploader.hasPendingUpload()
        guard hasPendingUpload else { return }
        isUploading = true
        errorMessage = nil
        defer { isUploading = false }
        do {
            if let completed = try await uploader.resume(progress: progressHandler()) {
                accept(completed)
                hasPendingUpload = false
                await refresh()
            }
        } catch {
            hasPendingUpload = await uploader.hasPendingUpload()
            errorMessage = Self.message(for: error)
        }
    }

    public func discardPendingUpload() async {
        do {
            try await uploader.clearPendingUpload()
            hasPendingUpload = false
            uploadProgress = nil
            errorMessage = nil
        } catch {
            errorMessage = Self.message(for: error)
        }
    }

    public func delete(_ material: Material) async {
        do {
            try await client.delete("v1", "materials", material.owner, material.name)
            materials.removeAll { $0.id == material.id }
            thumbnails[material.id] = nil
            warnings[material.id] = nil
            errorMessage = nil
        } catch let error {
            if case .server(status: 404, _, _) = error {
                await refresh()
            } else {
                errorMessage = error.userMessage
            }
        }
    }

    private func progressHandler() -> Uploader.ProgressHandler {
        { [weak self] progress in
            await MainActor.run { self?.uploadProgress = progress }
        }
    }

    private func accept(_ completed: UploadCompleteResponse) {
        if let index = materials.firstIndex(where: { $0.id == completed.material.id }) {
            materials[index] = completed.material
        } else {
            materials.insert(completed.material, at: 0)
        }
        loaded = true
        if !completed.probe.warning.isEmpty {
            warnings[completed.material.id] = completed.probe.warning
        }
    }

    private static func message(for error: any Error) -> String {
        if let api = error as? APIError { return api.userMessage }
        if let upload = error as? UploadFailure {
            switch upload {
            case .invalidLocalFile(let message): return message
            case .uploadInProgress: return "An upload is already in progress."
            }
        }
        return error.localizedDescription
    }
}
