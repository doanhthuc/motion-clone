import Foundation

public struct PodLease: Decodable, Sendable, Equatable {
    public let provider: String
    public let provisionedAt: Double
    public let absMaxMin: Double?
    public let quotedUsdPerHr: Double?
    public let runId: String?
}

/// Codable so `PodStore` can keep the last unverified kill across launches.
public struct KillResult: Codable, Sendable, Equatable {
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
    /// The cloud the failed rental was on (additive; null from an older bot,
    /// which only ever recorded RunPod failures the app could act on).
    public let provider: String?

    /// Where Retry rental re-rents: the cloud that failed, not always RunPod —
    /// it used to be hard-coded, so a failed Vast rental retried on RunPod.
    public var spendProvider: SpendProvider { provider == "vast" ? .vast : .runpod }
}

/// `GET /v1/pod`'s `migration` (bot.py `AppPod._migration_state`), read from
/// files only. Every field but `running` stays null until volume_migrate.py
/// writes it; the phone computes elapsed from `startedAt` itself.
public struct PodMigration: Decodable, Sendable, Equatable {
    public let running: Bool
    public let phase: String?
    public let toDc: String?
    public let startedAt: Double?
    public let bytesCopied: Double?
    public let totalBytes: Double?

    /// Public so the app can render a fixed migration under a UI-test launch
    /// argument (`-UITestPreviewMigration`, 2026-09-27) without a real one.
    public init(running: Bool, phase: String?, toDc: String?, startedAt: Double?,
                bytesCopied: Double?, totalBytes: Double?) {
        self.running = running
        self.phase = phase
        self.toDc = toDc
        self.startedAt = startedAt
        self.bytesCopied = bytesCopied
        self.totalBytes = totalBytes
    }

    /// nil until both byte counts exist — no bar is better than a made-up one.
    public var fractionCopied: Double? {
        guard let bytesCopied, let totalBytes, totalBytes > 0 else { return nil }
        return min(1, max(0, bytesCopied / totalBytes))
    }
}

/// `GET /v1/pod` (`AppPod.pod` in bot.py). Files and memory only on the
/// server, and taken without `BOT_LOCK`, so it answers while a kill holds it.
public struct PodStatus: Decodable, Sendable, Equatable {
    public let runId: String?
    public let gpu: String
    public let lease: PodLease?
    public let migration: PodMigration?
    public let killRunning: Bool
    public let lastKill: KillResult?
    public let failedRental: FailedRental?
}
