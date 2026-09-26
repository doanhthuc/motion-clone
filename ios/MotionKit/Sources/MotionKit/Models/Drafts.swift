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
    /// Names the try-on library entry this job was made from. It stays non-nil after that
    /// entry is deleted on purpose — it is a reference, never proof the entry still exists.
    public let tryonSeed: String?
    public var id: String { digest }

    private enum CodingKeys: String, CodingKey {
        case digest
        case runID = "runId"
        case pipeline, provider, slots, tryonSeed
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
    /// Names the try-on library entry the edited job was made from — a reference, not an
    /// existence check. `nil` means an ordinary job, or a server that predates Phase 6.
    public let tryonSeed: String?
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

extension Draft {
    /// The edited job's filled slots, role → material id (empty slots omitted).
    public var filledSlots: [String: String] { slots.compactMapValues(\.materialID) }
}

extension DraftBatchEntry {
    /// Same view over a batch entry's slots, whose values are already bare ids.
    public var filledSlots: [String: String] { slots.compactMapValues { $0 } }
}

/// `PATCH /v1/draft` with slots and a try-on seed in one request (Phase 6).
/// `seed` is three-state: `.keep` omits the key, `.clear` sends `null`,
/// `.set(id)` names a try-on library entry. A `nil` slot value clears it.
public struct DraftPatch: Encodable, Sendable, Equatable {
    public enum Seed: Sendable, Equatable { case keep, clear, set(String) }

    public let slots: [String: String?]
    public let seed: Seed

    public init(slots: [String: String?] = [:], seed: Seed = .keep) {
        self.slots = slots
        self.seed = seed
    }

    private enum CodingKeys: String, CodingKey { case slots, tryonSeed }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        if !slots.isEmpty { try container.encode(slots, forKey: .slots) }
        switch seed {
        case .keep: break
        case .clear: try container.encodeNil(forKey: .tryonSeed)
        case .set(let id): try container.encode(id, forKey: .tryonSeed)
        }
    }
}

/// A queued batch job's edit, `PATCH /v1/draft/batch/<digest>` (2026-09-26).
/// Only what is named is sent; the pipeline is not editable on a queued job.
public struct BatchEntryPatch: Encodable, Sendable, Equatable {
    public let provider: String?
    public let slots: [String: String?]
    public let seed: DraftPatch.Seed

    public init(provider: String? = nil, slots: [String: String?] = [:], seed: DraftPatch.Seed = .keep) {
        self.provider = provider
        self.slots = slots
        self.seed = seed
    }

    private enum CodingKeys: String, CodingKey { case provider, slots, tryonSeed }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(provider, forKey: .provider)
        if !slots.isEmpty { try container.encode(slots, forKey: .slots) }
        switch seed {
        case .keep: break
        case .clear: try container.encodeNil(forKey: .tryonSeed)
        case .set(let id): try container.encode(id, forKey: .tryonSeed)
        }
    }
}
