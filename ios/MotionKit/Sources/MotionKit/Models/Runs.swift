import Foundation

public enum MotionJSON {
    /// The API is Python with snake_case keys; Swift properties are camelCase.
    public static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()
}

/// `control/runs.py` `_status`. Unknown strings decode to `.unknown` rather than
/// failing the whole list: the server can grow a status without an app release.
public enum RunStatus: String, Decodable, Sendable, Equatable {
    case running, phaseA = "phase_a", error, done, stopped, unknown

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = RunStatus(rawValue: raw) ?? .unknown
    }

    /// Something is executing right now — a drain or a Phase A.
    public var isLive: Bool { self == .running || self == .phaseA }
}

/// Journal status of a job or stage. The server stringifies a missing value,
/// so "None" arrives for a stage that never started; it maps to `.unknown`.
public enum StageStatus: String, Decodable, Sendable, Equatable {
    case pending, running, done, error, unknown

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = StageStatus(rawValue: raw) ?? .unknown
    }
}

public struct RunsResponse: Decodable, Sendable, Equatable {
    public let runs: [RunSummary]
}

public struct RunSummary: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let batch: String?
    public let status: RunStatus
    public let updatedAt: Double
    public let jobsTotal: Int
    public let jobsDone: Int
}

public struct StageProgress: Decodable, Sendable, Equatable {
    public let name: String
    public let status: StageStatus
    public let elapsedSec: Double?
}

public struct JobProgress: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let status: StageStatus
    public let stages: [StageProgress]
}

public struct RunLease: Decodable, Sendable, Equatable {
    public let provider: String
    public let provisionedAt: Double
    public let absMaxMin: Double?
    /// A flat quote (RunPod) or nil (Vast) — never the invoice.
    public let quotedUsdPerHr: Double?
}

public struct RunDetail: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let batch: String?
    public let status: RunStatus
    public let updatedAt: Double
    public let jobsTotal: Int
    public let jobsDone: Int
    public let jobs: [JobProgress]
    public let lease: RunLease?
    public let outputs: [String]
}
