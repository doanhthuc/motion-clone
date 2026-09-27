import Foundation
import Observation

/// `GET/POST/DELETE /v1/gpu/subs` (2026-09-27 spec §2). The Telegram message
/// is the notification; this store only lets the phone see and change the
/// list, and tells the stage which firings are new since the app last looked.
@MainActor @Observable
public final class GpuSubsStore {
    public private(set) var subs: [GpuSub] = []
    public private(set) var fired: [GpuSubFiring] = []
    public private(set) var error: APIError?
    /// "gpu|datacenter" keys with a request in flight.
    public private(set) var inFlight: Set<String> = []
    public private(set) var message: String?
    private var lastSeen: Double

    private let client: APIClient
    private let defaults: UserDefaults
    static let seenKey = "gpuSubs.lastSeenFiredAt"

    public init(client: APIClient, defaults: UserDefaults = .standard) {
        self.client = client
        self.defaults = defaults
        lastSeen = defaults.object(forKey: Self.seenKey) as? Double ?? -1
    }

    public func load() async {
        do {
            let fresh = try await client.get(GpuSubs.self, "v1", "gpu", "subs")
            subs = fresh.subs
            fired = fresh.fired
            error = nil
            // First read on this install: what already fired is history, not news.
            if lastSeen < 0 { markSeen() }
        } catch {
            self.error = error
        }
    }

    public func sub(gpu: String, datacenter: String) -> GpuSub? {
        subs.first { $0.gpu == gpu && $0.datacenter == datacenter }
    }

    public func watching(gpu: String) -> [GpuSub] { subs.filter { $0.gpu == gpu } }

    public var armed: GpuSub? { subs.first { $0.autoResume != nil } }

    public var unseen: [GpuSubFiring] { fired.filter { $0.firedAt > lastSeen } }

    public func markSeen() {
        lastSeen = fired.map(\.firedAt).max() ?? Date().timeIntervalSince1970
        defaults.set(lastSeen, forKey: Self.seenKey)
    }

    public func dismissMessage() { message = nil }

    /// Subscribes (or re-subscribes) the pair. With a run id it also arms
    /// auto-resume, which the server refuses outside home or without a
    /// stock-out; the refusal is shown as `message`.
    @discardableResult
    public func watch(gpu: String, datacenter: String, autoResumeRunID: String?) async -> Bool {
        let key = "\(gpu)|\(datacenter)"
        guard !inFlight.contains(key) else { return false }
        inFlight.insert(key)
        defer { inFlight.remove(key) }
        do {
            _ = try await client.post(
                GpuSubResponse.self,
                body: GpuSubRequest(gpu: gpu, datacenter: datacenter,
                                    autoResume: autoResumeRunID != nil, runId: autoResumeRunID),
                "v1", "gpu", "subs")
            message = nil
            // Arming moves the bolt off any other sub; re-read rather than guess.
            await load()
            return true
        } catch {
            message = error.userMessage
            return false
        }
    }

    public func unwatch(_ sub: GpuSub) async {
        let key = "\(sub.gpu)|\(sub.datacenter)"
        guard !inFlight.contains(key) else { return }
        inFlight.insert(key)
        defer { inFlight.remove(key) }
        do {
            subs = try await client.delete(GpuSubsRemaining.self, "v1", "gpu", "subs", sub.id).subs
            message = nil
        } catch {
            message = error.userMessage
        }
    }
}
