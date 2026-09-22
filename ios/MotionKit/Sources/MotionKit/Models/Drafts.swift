import Foundation

public enum PipelineRoleKind: String, Decodable, Sendable, Equatable {
    case image, video, unknown

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = Self(rawValue: raw) ?? .unknown
    }

    public func accepts(_ material: Material) -> Bool {
        switch (self, material.kind) {
        case (.image, .image), (.video, .video): true
        default: false
        }
    }
}

public struct PipelineProvider: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let label: String
}

public struct Pipeline: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let stages: [String]
    public let required: [String]
    public let optional: [String]
    public let roles: [String: PipelineRoleKind]
    public let providers: [PipelineProvider]
}

public struct PipelineCatalogResponse: Decodable, Sendable, Equatable {
    public let pipelines: [Pipeline]
}

public struct DraftSlot: Decodable, Sendable, Equatable {
    public let materialID: String?
    public let name: String
    public let exists: Bool
    public let probe: MaterialProbe
    public let warning: String

    private enum CodingKeys: String, CodingKey {
        case materialID = "materialId"
        case name, exists, probe, warning
    }
}

public struct DraftBatchEntry: Decodable, Sendable, Equatable, Identifiable {
    public let digest: String
    public let runID: String
    public let pipeline: String
    public let provider: String
    public let slots: [String: String?]
    public var id: String { digest }

    private enum CodingKeys: String, CodingKey {
        case digest
        case runID = "runId"
        case pipeline, provider, slots
    }
}

public struct Draft: Decodable, Sendable, Equatable {
    public let owner: String
    public let pipeline: String
    public let provider: String
    public let generation: Int
    public let slots: [String: DraftSlot]
    public let required: [String]
    public let optional: [String]
    public let missing: [String]
    public let validated: Bool?
    public let batch: [DraftBatchEntry]
    public let jobs: Int
    public let estimateMin: Int?
    public let dropped: [String]?
}

public struct DraftValidationResponse: Decodable, Sendable, Equatable {
    public let valid: Bool
    public let stale: Bool
    public let draft: Draft
    public let output: String?
}

public struct PipelinePatch: Encodable, Sendable {
    public let pipeline: String

    public init(pipeline: String) {
        self.pipeline = pipeline
    }
}

public struct ProviderPatch: Encodable, Sendable {
    public let provider: String

    public init(provider: String) {
        self.provider = provider
    }
}

public struct SlotPatch: Encodable, Sendable {
    public let slots: [String: String?]

    public init(role: String, materialID: String?) {
        slots = [role: materialID]
    }
}
