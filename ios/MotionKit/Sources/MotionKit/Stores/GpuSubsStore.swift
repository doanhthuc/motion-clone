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
    /// Refusals keyed "gpu|datacenter" (2026-09-27): the GPU sheet and a
    /// run's retry card each show their own pair, and one surface must never
    /// show the other's error.
    private var messages: [String: String] = [:]
    /// The server answered 404 on `GET /v1/gpu/subs`: a bot deployed before
    /// the routes existed. The app hides the bell, bolt and "Resume when in
    /// stock" rather than offer buttons that can only fail (2026-09-27).
    public private(set) var unsupported = false
    private var lastSeen: Double
    /// Newest firing whose banner already had its turn. Separate from
    /// `lastSeen`: the drawer still counts a bannered firing as new until it
    /// is dismissed, but the banner itself shows once (2026-10-10 — keyed on
    /// `lastSeen` alone, an undismissed firing came back on every launch).
    private var lastBannered: Double

    private let client: APIClient
    private let defaults: UserDefaults
    static let seenKey = "gpuSubs.lastSeenFiredAt"
    static let banneredKey = "gpuSubs.lastBanneredFiredAt"

    public init(client: APIClient, defaults: UserDefaults = .standard) {
        self.client = client
        self.defaults = defaults
        lastSeen = defaults.object(forKey: Self.seenKey) as? Double ?? -1
        lastBannered = defaults.object(forKey: Self.banneredKey) as? Double ?? -1
    }

    public func load() async {
        do {
            let fresh = try await client.get(GpuSubs.self, "v1", "gpu", "subs")
            subs = fresh.subs
            fired = fresh.fired
            error = nil
            unsupported = false
            // First read on this install: what already fired is history, not news.
            if lastSeen < 0 { markSeen() }
        } catch {
            self.error = error
            // Only a 404 is "route missing"; offline or a 5xx leaves the
            // last answer standing.
            if error.isNotFound { unsupported = true }
        }
    }

    public func sub(gpu: String, datacenter: String) -> GpuSub? {
        subs.first { $0.gpu == gpu && $0.datacenter == datacenter }
    }

    public func watching(gpu: String) -> [GpuSub] { subs.filter { $0.gpu == gpu } }

    public var armed: GpuSub? { subs.first { $0.autoResume != nil } }

    public var unseen: [GpuSubFiring] { fired.filter { $0.firedAt > lastSeen } }

    /// The firing the banner should show: unseen, and not bannered before.
    public var bannerFiring: GpuSubFiring? {
        unseen.first { $0.firedAt > lastBannered }
    }

    /// Its banner had its turn (auto-hid, or was tapped through to the drawer).
    public func markBannered(_ firing: GpuSubFiring) {
        guard firing.firedAt > lastBannered else { return }
        lastBannered = firing.firedAt
        defaults.set(lastBannered, forKey: Self.banneredKey)
    }

    public func markSeen() {
        // 0, not the phone's clock, when nothing has fired (2026-09-27):
        // `fired_at` is the VPS's clock, and a phone running ahead of it
        // would silently swallow the next firing as already seen.
        lastSeen = fired.map(\.firedAt).max() ?? 0
        defaults.set(lastSeen, forKey: Self.seenKey)
        if lastSeen > lastBannered {
            lastBannered = lastSeen
            defaults.set(lastBannered, forKey: Self.banneredKey)
        }
    }

    private static func key(_ gpu: String, _ datacenter: String) -> String { "\(gpu)|\(datacenter)" }

    public func message(gpu: String, datacenter: String) -> String? {
        messages[Self.key(gpu, datacenter)]
    }

    public func dismissMessage(gpu: String, datacenter: String) {
        messages[Self.key(gpu, datacenter)] = nil
    }

    /// Subscribes (or re-subscribes) the pair. With a run id it also arms
    /// auto-resume, which the server refuses outside home or without a
    /// stock-out; the refusal is kept under the pair's `message(gpu:datacenter:)`.
    @discardableResult
    public func watch(gpu: String, datacenter: String, autoResumeRunID: String?) async -> Bool {
        let key = Self.key(gpu, datacenter)
        guard !inFlight.contains(key) else { return false }
        inFlight.insert(key)
        defer { inFlight.remove(key) }
        do {
            _ = try await client.post(
                GpuSubResponse.self,
                body: GpuSubRequest(gpu: gpu, datacenter: datacenter,
                                    autoResume: autoResumeRunID != nil, runId: autoResumeRunID),
                "v1", "gpu", "subs")
            messages[key] = nil
            // Arming moves the bolt off any other sub; re-read rather than guess.
            await load()
            return true
        } catch {
            messages[key] = error.userMessage
            return false
        }
    }

    public func unwatch(_ sub: GpuSub) async {
        let key = Self.key(sub.gpu, sub.datacenter)
        guard !inFlight.contains(key) else { return }
        inFlight.insert(key)
        defer { inFlight.remove(key) }
        do {
            subs = try await client.delete(GpuSubsRemaining.self, "v1", "gpu", "subs", sub.id).subs
            messages[key] = nil
        } catch {
            messages[key] = error.userMessage
        }
    }
}
