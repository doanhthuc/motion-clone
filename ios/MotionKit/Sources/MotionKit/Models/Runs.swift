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

/// `DELETE /v1/runs/{id}`: the run is gone; `videosDeleted` counts the
/// Outputs files that went with it (0 unless `?videos=1`).
public struct RunDeleted: Decodable, Sendable, Equatable {
    public let deleted: String
    public let videosDeleted: Int
}

public struct StageProgress: Decodable, Sendable, Equatable {
    public let name: String
    public let status: StageStatus
    public let elapsedSec: Double?
}

/// What a job was made from (`control/runs.py` `_job_setups`): material ids
/// (`owner/name`) by role. Additive — an older server, or a manifest that no
/// longer loads, sends none.
public struct JobSetup: Decodable, Sendable, Equatable {
    public let pipeline: String
    public let provider: String?
    public let inputs: [String: String]
}

public struct JobProgress: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let status: StageStatus
    public let stages: [StageProgress]
    public let setup: JobSetup?
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

/// What the Runs tab's "Now" card counts. A Phase A has written nothing to the
/// journal yet — `jobs_total` is 0 until the drain starts — so while the VPS
/// makes try-ons the previews are the only real count.
public struct LiveProgress: Equatable, Sendable {
    public enum Unit: Sendable { case looks, jobs }
    public let done: Int
    public let total: Int
    public let unit: Unit

    public init(done: Int, total: Int, unit: Unit) {
        self.done = done
        self.total = total
        self.unit = unit
    }

    /// `tryon` counts only when it belongs to `run`: the shared RunFlow may
    /// still hold an older run's previews.
    public init(run: RunSummary, tryon: TryonPreviews?) {
        guard run.status == .phaseA else {
            self.init(done: run.jobsDone, total: run.jobsTotal, unit: .jobs)
            return
        }
        let previews = tryon?.runId == run.id ? tryon?.previews ?? [] : []
        self.init(done: previews.filter { $0.status == .done }.count, total: previews.count, unit: .looks)
    }
}

extension RunDetail {
    /// A job's finished files: `<job>.mp4`, then `<job>-2.mp4` for a re-run,
    /// newest first. `a-b-cd` is a different job from `a-b-c`, and so is
    /// `a-b-c-x` — only a numeric suffix is a re-run.
    public func outputs(forJob job: String) -> [String] {
        func rerun(_ name: String) -> Int? {
            let stem = (name as NSString).deletingPathExtension
            if stem == job { return 1 }
            guard stem.hasPrefix(job + "-") else { return nil }
            return Int(stem.dropFirst(job.count + 1))
        }
        return outputs.compactMap { name in rerun(name).map { (name, $0) } }
            .sorted { $0.1 > $1.1 }
            .map(\.0)
    }
}

/// What a Runs-tab card says about a run, read from its detail: how its jobs
/// stand, and what they were made with. Until 2026-09-27 the card said only
/// "0 of 2 jobs", which could not tell a failed job from one never started.
public struct RunDigest: Equatable, Sendable {
    public let done: Int
    public let failed: Int
    public let running: Int
    /// Queued, or never reached before the run stopped.
    public let left: Int
    /// Distinct, in job order.
    public let pipelines: [String]
    public let providers: [String]

    public init(_ detail: RunDetail) {
        let jobs = detail.jobs
        done = jobs.filter { $0.status == .done }.count
        failed = jobs.filter { $0.status == .error }.count
        running = jobs.filter { $0.status == .running }.count
        left = jobs.count - done - failed - running
        func distinct(_ values: [String]) -> [String] {
            values.reduce(into: []) { if !$0.contains($1) { $0.append($1) } }
        }
        pipelines = distinct(jobs.compactMap(\.setup?.pipeline))
        providers = distinct(jobs.compactMap(\.setup?.provider))
    }

    /// "4 jobs · 1 done · 1 failed · 2 left" — zero counts are left out.
    public var countsText: String {
        let total = done + failed + running + left
        let parts = ["\(total) job\(total == 1 ? "" : "s")"] + [(done, "done"), (running, "running"), (failed, "failed"), (left, "left")]
            .filter { $0.0 > 0 }.map { "\($0.0) \($0.1)" }
        return total == 0 ? "No jobs" : parts.joined(separator: " · ")
    }

    /// "Try-on → Swap → Enhance · qwen-max", or nil when no job has a setup.
    public var setupText: String? {
        guard !pipelines.isEmpty else { return nil }
        let steps = pipelines.count == 1 ? PipelineName.steps(pipelines[0]) : "\(pipelines.count) pipelines"
        return ([steps] + providers).joined(separator: " · ")
    }
}

public enum PipelineName {
    /// Pipeline ids are stage names joined by dashes, and two stages are two
    /// words ("character-swap", "camera-motion"): "tryon-character-swap-enhance"
    /// reads "Try-on → Swap → Enhance". Unknown words pass through.
    public static func steps(_ raw: String) -> String {
        var words = raw.split(separator: "-").map(String.init)[...]
        var steps: [String] = []
        while let word = words.popFirst() {
            switch word {
            case "tryon": steps.append("Try-on")
            case "character" where words.first == "swap": words.removeFirst(); steps.append("Swap")
            case "camera" where words.first == "motion": words.removeFirst(); steps.append("Camera motion")
            default: steps.append(word.prefix(1).uppercased() + word.dropFirst())
            }
        }
        return steps.joined(separator: " → ")
    }
}

public enum RunName {
    /// Batch runs are named by when they were made ("2026-09-26-1530"); a
    /// card shows that as "Sep 26 · 15:30". Any other name is its own title.
    public static func title(_ id: String, calendar: Calendar = .current) -> String {
        let parts = id.split(separator: "-")
        guard parts.count == 4, parts[3].count == 4,
              let year = Int(parts[0]), let month = Int(parts[1]), let day = Int(parts[2]),
              let hhmm = Int(parts[3]), (1...12).contains(month), (1...31).contains(day),
              hhmm / 100 < 24, hhmm % 100 < 60 else { return id }
        let months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        let sameYear = calendar.component(.year, from: .now) == year
        let date = sameYear ? "\(months[month - 1]) \(day)" : "\(months[month - 1]) \(day), \(year)"
        return "\(date) · " + String(format: "%02d:%02d", hhmm / 100, hhmm % 100)
    }
}
