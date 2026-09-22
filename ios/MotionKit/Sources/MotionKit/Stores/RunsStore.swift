import Foundation
import Observation

@MainActor @Observable
public final class RunsStore {
    public private(set) var runs: [RunSummary] = []
    public private(set) var loaded = false
    public private(set) var error: APIError?
    public private(set) var lastSuccess: Date?
    private let client: APIClient

    public init(client: APIClient) { self.client = client }

    /// The run shown as the hero card: the first one executing right now.
    public var live: RunSummary? { runs.first { $0.status.isLive } }
    public var recent: [RunSummary] { runs.filter { $0.id != live?.id } }
    public var isStale: Bool { loaded && error != nil }

    public func refresh() async {
        do {
            runs = try await client.get(RunsResponse.self, "v1", "runs").runs
            loaded = true
            error = nil
            lastSuccess = .now
        } catch {
            self.error = error
        }
    }
}
