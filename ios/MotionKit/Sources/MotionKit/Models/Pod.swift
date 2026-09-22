import Foundation

public struct PodLease: Decodable, Sendable, Equatable {
    public let provider: String
    public let provisionedAt: Double
    public let absMaxMin: Double?
    public let quotedUsdPerHr: Double?
    public let runId: String?
}

public struct KillResult: Decodable, Sendable, Equatable {
    public let at: Double
    public let ok: Bool
    public let code: String
    public let message: String
}

public struct FailedRental: Decodable, Sendable, Equatable {
    public let gpu: String
    public let datacenter: String?
    public let stockOut: Bool
    public let detail: String
}

/// `GET /v1/pod` (`AppPod.pod` in bot.py). `migration` is not decoded in
/// phase 1 — phase 5 adds it.
public struct PodStatus: Decodable, Sendable, Equatable {
    public let runId: String?
    public let gpu: String
    public let lease: PodLease?
    public let killRunning: Bool
    public let lastKill: KillResult?
    public let failedRental: FailedRental?
}
