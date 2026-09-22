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

    private let client: APIClient
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

    public func startUpload(fileURL: URL, fileName: String) async {
        guard !isUploading else {
            errorMessage = "An upload is already in progress."
            return
        }
        isUploading = true
        errorMessage = nil
        defer { isUploading = false }
        do {
            let completed = try await uploader.start(
                fileURL: fileURL, fileName: fileName, progress: progressHandler())
            retainWarning(from: completed)
            await refresh()
        } catch {
            errorMessage = Self.message(for: error)
        }
    }

    public func resumePendingUpload() async {
        guard !isUploading else { return }
        guard await uploader.hasPendingUpload() else { return }
        isUploading = true
        errorMessage = nil
        defer { isUploading = false }
        do {
            if let completed = try await uploader.resume(progress: progressHandler()) {
                retainWarning(from: completed)
                await refresh()
            }
        } catch {
            errorMessage = Self.message(for: error)
        }
    }

    public func delete(_ material: Material) async {
        guard material.canDelete else {
            errorMessage = "Only materials uploaded by this app can be deleted."
            return
        }
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

    private func retainWarning(from completed: UploadCompleteResponse) {
        guard !completed.probe.warning.isEmpty else { return }
        warnings[completed.material.id] = completed.probe.warning
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
