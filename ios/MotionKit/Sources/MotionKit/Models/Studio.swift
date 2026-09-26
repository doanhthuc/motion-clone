import Foundation

/// Image Studio (spec 2026-09-26). The server names every file; the phone
/// only ever sends `{kind, id}` references.
public enum StudioRefKind: String, Codable, Sendable, Hashable {
    case material, tryon, studio, snapshot
    case runTryon = "run_tryon"
}

public struct StudioRef: Codable, Sendable, Hashable, Identifiable {
    public let kind: StudioRefKind
    public let id: String
    /// Set on references read back from a generation: the server's snapshot file.
    public let file: String?

    public init(kind: StudioRefKind, id: String, file: String? = nil) {
        self.kind = kind; self.id = id; self.file = file
    }

    /// Two refs to the same thing are the same attachment whatever their snapshot.
    public static func == (a: Self, b: Self) -> Bool { a.kind == b.kind && a.id == b.id }
    public func hash(into h: inout Hasher) { h.combine(kind); h.combine(id) }
}

public struct StudioModelInfo: Decodable, Sendable, Equatable, Identifiable {
    public let key: String
    public let label: String
    public let provider: String
    public let maxRefs: Int
    public let priceUsd: Double
    public let available: Bool
    public let `default`: Bool
    public var id: String { key }
}

public struct StudioCatalog: Decodable, Sendable, Equatable {
    public let models: [StudioModelInfo]
    public let aspects: [String]
    public let maxCount: Int
}

public struct StudioProjectSummary: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let createdAt: Double
    public let updatedAt: Double
    public let cover: String?
    public let spentUsd: Double
    public let imageCount: Int
}

public enum StudioStatus: String, Decodable, Sendable {
    case queued, running, done, error
}

public struct StudioSlot: Decodable, Sendable, Equatable {
    public let status: StudioStatus
    public let image: String?
    public let error: String?
}

public struct StudioGeneration: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let createdAt: Double
    public let prompt: String
    public let model: String
    public let aspect: String
    public let count: Int
    public let refs: [StudioRef]
    public let slots: [StudioSlot]
    public let status: StudioStatus
    public let unitPriceUsd: Double
    public let estCostUsd: Double
}

public struct StudioProject: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let createdAt: Double
    public let updatedAt: Double
    public let spentUsd: Double
    public var generations: [StudioGeneration]
}

public struct StudioProjectsResponse: Decodable, Sendable { public let projects: [StudioProjectSummary] }
struct StudioProjectResponse: Decodable, Sendable { let project: StudioProject }
struct StudioGenerationResponse: Decodable, Sendable { let generation: StudioGeneration }
struct StudioPromoteMaterial: Decodable, Sendable { let material: Material }
struct StudioPromoteEntry: Decodable, Sendable { let entry: TryonLibraryEntry }

public enum StudioPromoteTarget: String, Sendable { case material, tryon }
