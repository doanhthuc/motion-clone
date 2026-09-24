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

extension JobProgress {
    /// Seconds spent in finished stages; a running stage reports `null`.
    public var finishedSec: Double { stages.compactMap(\.elapsedSec).reduce(0, +) }
}

/// The batch progress header and row order (Phase 6 spec §5): failed and
/// running jobs first, so trouble is on screen without scrolling 12 rows.
public struct BatchSummary: Equatable, Sendable {
    public let total: Int
    public let done: Int
    public let running: Int
    public let failed: Int
    public let ordered: [JobProgress]

    public init(_ jobs: [JobProgress]) {
        total = jobs.count
        done = jobs.filter { $0.status == .done }.count
        running = jobs.filter { $0.status == .running }.count
        failed = jobs.filter { $0.status == .error }.count
        func rank(_ job: JobProgress) -> Int {
            switch job.status { case .error: 0; case .running: 1; default: 2 }
        }
        // Equal-ranked jobs keep manifest order. `sorted(by:)` already guarantees
        // a stable sort; the explicit offset keeps that intent readable here and
        // survives a swap to a non-stable algorithm. The list re-renders whenever a
        // poll returns changed data — `RunDetailStore.refresh` assigns `detail` only
        // on a non-304 — so a reshuffle would be user-visible.
        ordered = jobs.enumerated()
            .sorted { (rank($0.element), $0.offset) < (rank($1.element), $1.offset) }
            .map(\.element)
    }
}
