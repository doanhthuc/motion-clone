import Foundation
import Observation

/// `GET /v1/balance[?vast=1]` (Phase 5 design §5). The Vast credit is its own
/// ~30 s subprocess on the server, so it is read only on an explicit tap.
@MainActor @Observable
public final class BalanceStore {
    public private(set) var balance: Balance?
    /// The last Vast credit read, kept across plain reloads (which omit it).
    public private(set) var vast: VastBalance?
    public private(set) var error: APIError?
    public private(set) var isLoading = false
    public private(set) var isLoadingVast = false

    private let client: APIClient

    public init(client: APIClient) { self.client = client }

    public func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            balance = try await client.get(Balance.self, timeout: 45, "v1", "balance")
            error = nil
        } catch {
            self.error = error
        }
    }

    public func loadVast() async {
        isLoadingVast = true
        defer { isLoadingVast = false }
        do {
            let fresh = try await client.get(Balance.self, query: [URLQueryItem(name: "vast", value: "1")],
                                             timeout: 90, "v1", "balance")
            balance = fresh
            vast = fresh.vast
            error = nil
        } catch {
            self.error = error
        }
    }

    /// nil until a read succeeded. An unreadable account is never "$0".
    public var runpodLine: String? {
        guard let balance else { return nil }
        guard let runpod = balance.runpod else { return "Couldn't read the RunPod balance" }
        return "\(Format.usd(runpod.usd)) · ≈ \(Format.runway(hours: runpod.runwayHours)) at \(Format.usd(runpod.usdPerHr))/h"
    }

    public var vastLine: String? {
        guard let vast else { return nil }
        return vast.usd.map(Format.usd) ?? "Couldn't read the Vast credit"
    }
}
