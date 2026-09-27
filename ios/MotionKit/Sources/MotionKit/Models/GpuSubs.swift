import Foundation

/// `GET /v1/gpu/subs` (bot.py `AppPod.gpu_subs`, 2026-09-27 spec §1): the
/// bot's one-shot stock watches — the same list `/subscribe` writes — and the
/// last ten firings, newest first.
public struct GpuSubs: Decodable, Sendable, Equatable {
    public let subs: [GpuSub]
    public let fired: [GpuSubFiring]
}

public struct GpuSub: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let gpu: String
    public let name: String
    public let datacenter: String
    public let createdAt: Double?
    /// Set on at most one sub: when it fires, the stuck run resumes on its own.
    public let autoResume: GpuSubAutoResume?
}

public struct GpuSubAutoResume: Decodable, Sendable, Equatable {
    public let runId: String
    /// The GPU's $/h when armed; a firing above it only notifies.
    public let maxUsdPerHr: Double
}

public struct GpuSubFiring: Decodable, Sendable, Equatable, Identifiable {
    public let subId: String
    public let gpu: String
    public let name: String
    public let datacenter: String
    public let stock: String
    public let usdPerHr: Double?
    public let firedAt: Double
    /// "notified", "resumed" or "resume_refused".
    public let action: String
    public let reason: String?

    public var id: String { "\(subId)@\(firedAt)" }
    public var resumed: Bool { action == "resumed" }
    public var refused: Bool { action == "resume_refused" }
}

/// `POST /v1/gpu/subs` — an upsert on (gpu, datacenter).
public struct GpuSubRequest: Encodable, Sendable {
    public let gpu: String
    public let datacenter: String
    public let autoResume: Bool
    public let runId: String?
}

public struct GpuSubResponse: Decodable, Sendable { public let sub: GpuSub }
public struct GpuSubsRemaining: Decodable, Sendable { public let subs: [GpuSub] }
