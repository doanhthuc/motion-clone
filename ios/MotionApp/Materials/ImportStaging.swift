import CoreTransferable
import Foundation
import MotionKit
import UniformTypeIdentifiers

struct ImportedMedia: Transferable, Sendable {
    let url: URL
    let fileName: String

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(importedContentType: .image) { received in
            try ImportStaging.stage(received.file, securityScoped: false)
        }
        FileRepresentation(importedContentType: .movie) { received in
            try ImportStaging.stage(received.file, securityScoped: false)
        }
    }

    func removeStagedCopy() {
        try? FileManager.default.removeItem(at: url.deletingLastPathComponent())
    }
}

enum ImportStaging {
    static func stage(_ source: URL, securityScoped: Bool) throws -> ImportedMedia {
        let accessed = securityScoped && source.startAccessingSecurityScopedResource()
        defer { if accessed { source.stopAccessingSecurityScopedResource() } }

        let name = ImportedFilename.sanitize(source.lastPathComponent)
        let directory = root.appending(component: UUID().uuidString, directoryHint: .isDirectory)
        let destination = directory.appending(component: name)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: source, to: destination)
            return ImportedMedia(url: destination, fileName: name)
        } catch {
            try? FileManager.default.removeItem(at: directory)
            throw error
        }
    }

    private static var root: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appending(path: "Motion/Imports", directoryHint: .isDirectory)
    }
}
