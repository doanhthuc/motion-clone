import MotionKit
import PhotosUI
import SwiftUI

/// Photos item → staged file → chunked upload, for callers that need the
/// material that came out the other end (a picker selects it straight away).
@MainActor
enum MediaImport {
    struct Unreadable: LocalizedError {
        var errorDescription: String? { "The selected item could not be imported." }
    }

    /// nil when the upload itself failed; `MaterialsStore.errorMessage` says why.
    static func upload(_ item: PhotosPickerItem, to store: MaterialsStore) async throws -> MotionKit.Material? {
        guard let imported = try await item.loadTransferable(type: ImportedMedia.self) else {
            throw Unreadable()
        }
        defer { imported.removeStagedCopy() }
        return await store.startUpload(fileURL: imported.url, fileName: imported.fileName)
    }
}
