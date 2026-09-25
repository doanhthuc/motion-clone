import Foundation

public struct OutputsResponse: Decodable, Sendable, Equatable {
    public let outputs: [OutputBatch]
}

public struct OutputBatch: Decodable, Sendable, Equatable, Identifiable {
    public let batch: String
    public let updatedAt: Double
    public let files: [OutputFile]
    public var id: String { batch }

    /// A run's detail lists only file names; the app builds a batch from them to open the feed.
    public init(batch: String, updatedAt: Double, files: [OutputFile]) {
        self.batch = batch
        self.updatedAt = updatedAt
        self.files = files
    }
}

public struct OutputFile: Decodable, Sendable, Equatable, Identifiable {
    public let name: String
    public let bytes: Int
    public var id: String { name }

    public init(name: String, bytes: Int) {
        self.name = name
        self.bytes = bytes
    }
    /// `control/outputs.py` OUTPUT_SUFFIXES: .mp4/.mov are video, the rest images.
    public var isVideo: Bool {
        let lower = name.lowercased()
        return lower.hasSuffix(".mp4") || lower.hasSuffix(".mov")
    }
}
