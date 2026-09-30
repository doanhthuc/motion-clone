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
    /// The longest length this pipeline takes (camera: its largest preset); `nil` means
    /// no cap. A server predating the field also decodes as `nil`.
    public let maxDurationSec: Int?
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
    /// `nil` is "Full" (the driver's own measured length); a server predating the length
    /// feature also decodes as `nil`.
    public let durationSec: Int?
    /// The attached driver's own probed length in seconds — the only source the app has
    /// for it, since a batch entry's slots are bare material ids, never a probe. `nil`
    /// until a driver is attached, or on a server that predates the length feature.
    public let driverDurationS: Double?
    public var id: String { digest }

    private enum CodingKeys: String, CodingKey {
        case digest
        case runID = "runId"
        case pipeline, provider, slots, tryonSeed, durationSec, driverDurationS
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
    /// `nil` is "Full" (the driver's own measured length); a server predating the length
    /// feature also decodes as `nil`.
    public let durationSec: Int?
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

/// `.full` sends `duration_sec: null` (the driver's own measured length, today's
/// default); `.seconds(n)` overrides it — a quick 10s/15s pick or a custom value,
/// both validated server-side against the attached driver's own probed length.
public enum DurationChoice: Sendable, Equatable {
    case full
    case seconds(Int)
}

/// `PATCH /v1/draft` naming only the length (2026-09-28), the same single-field
/// shape as `PipelinePatch`/`ProviderPatch`.
public struct DurationPatch: Encodable, Sendable {
    public let duration: DurationChoice

    public init(_ duration: DurationChoice) {
        self.duration = duration
    }

    private enum CodingKeys: String, CodingKey { case durationSec }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        switch duration {
        case .full: try container.encodeNil(forKey: .durationSec)
        case .seconds(let n): try container.encode(n, forKey: .durationSec)
        }
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
    /// `nil` omits the key and keeps the draft's length. Sent beside a driver
    /// slot it is validated against that driver in the same request.
    public let duration: DurationChoice?

    public init(slots: [String: String?] = [:], seed: Seed = .keep, duration: DurationChoice? = nil) {
        self.slots = slots
        self.seed = seed
        self.duration = duration
    }

    private enum CodingKeys: String, CodingKey { case slots, tryonSeed, durationSec }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        if !slots.isEmpty { try container.encode(slots, forKey: .slots) }
        switch seed {
        case .keep: break
        case .clear: try container.encodeNil(forKey: .tryonSeed)
        case .set(let id): try container.encode(id, forKey: .tryonSeed)
        }
        switch duration {
        case nil: break
        case .full: try container.encodeNil(forKey: .durationSec)
        case .seconds(let n): try container.encode(n, forKey: .durationSec)
        }
    }
}

/// A queued batch job's edit, `PATCH /v1/draft/batch/<digest>` (2026-09-26;
/// pipeline + duration added 2026-09-28). Only what is named is sent. A
/// pipeline switch keeps whichever slots the new pipeline can still use and
/// the server refuses the whole edit (`missing_slots`) if a required one
/// can't be preserved — it never leaves the entry silently short an input.
public struct BatchEntryPatch: Encodable, Sendable, Equatable {
    public let pipeline: String?
    public let provider: String?
    public let slots: [String: String?]
    public let seed: DraftPatch.Seed
    /// `nil` keeps the entry's current length, same as `provider == nil`.
    public let duration: DurationChoice?

    public init(pipeline: String? = nil, provider: String? = nil, slots: [String: String?] = [:],
               seed: DraftPatch.Seed = .keep, duration: DurationChoice? = nil) {
        self.pipeline = pipeline
        self.provider = provider
        self.slots = slots
        self.seed = seed
        self.duration = duration
    }

    private enum CodingKeys: String, CodingKey { case pipeline, provider, slots, tryonSeed, durationSec }

    public func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(pipeline, forKey: .pipeline)
        try container.encodeIfPresent(provider, forKey: .provider)
        if !slots.isEmpty { try container.encode(slots, forKey: .slots) }
        switch seed {
        case .keep: break
        case .clear: try container.encodeNil(forKey: .tryonSeed)
        case .set(let id): try container.encode(id, forKey: .tryonSeed)
        }
        switch duration {
        case nil: break
        case .full: try container.encodeNil(forKey: .durationSec)
        case .seconds(let n): try container.encode(n, forKey: .durationSec)
        }
    }
}

extension DurationChoice {
    /// `nil` is Full, the shape the length control reads.
    public var seconds: Int? {
        if case .seconds(let n) = self { n } else { nil }
    }
}
