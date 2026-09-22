import Foundation

public struct UploadCheckpoint: Codable, Sendable, Equatable {
    public let uploadId: String
    public let fileName: String
    public let fileSize: Int64
    public let localFileName: String

    public init(uploadId: String, fileName: String, fileSize: Int64, localFileName: String) {
        self.uploadId = uploadId
        self.fileName = fileName
        self.fileSize = fileSize
        self.localFileName = localFileName
    }
}

public struct UploadCheckpointJournal: Sendable {
    public let root: URL

    public init() { self.root = Self.defaultRoot() }
    public init(root: URL) { self.root = root }

    public func load() throws -> UploadCheckpoint? {
        let url = root.appending(component: "checkpoint.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try JSONDecoder().decode(UploadCheckpoint.self, from: Data(contentsOf: url))
    }

    public func save(_ checkpoint: UploadCheckpoint) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let destination = root.appending(component: "checkpoint.json")
        let temporary = root.appending(component: ".checkpoint.\(UUID().uuidString).tmp")
        let data = try JSONEncoder().encode(checkpoint)
        try data.write(to: temporary, options: .atomic)
        if FileManager.default.fileExists(atPath: destination.path) {
            _ = try FileManager.default.replaceItemAt(destination, withItemAt: temporary)
        } else {
            try FileManager.default.moveItem(at: temporary, to: destination)
        }
    }

    public func sourceURL(for checkpoint: UploadCheckpoint) -> URL {
        root.appending(component: (checkpoint.localFileName as NSString).lastPathComponent)
    }

    public func clear() throws {
        guard FileManager.default.fileExists(atPath: root.path) else { return }
        try FileManager.default.removeItem(at: root)
    }

    private static func defaultRoot() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appending(path: "Motion/Uploads/current", directoryHint: .isDirectory)
    }
}
