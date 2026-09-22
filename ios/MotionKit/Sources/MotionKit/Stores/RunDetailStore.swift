import Foundation
import Observation

@MainActor @Observable
public final class RunDetailStore {
    public let runID: String
    public private(set) var detail: RunDetail?
    public private(set) var error: APIError?
    public private(set) var lastSuccess: Date?
    private let client: APIClient

    public init(client: APIClient, runID: String) {
        self.client = client
        self.runID = runID
    }

    public var isStale: Bool { detail != nil && error != nil }

    public func refresh() async {
        do {
            // nil = 304: the run did not change since the last poll.
            if let fresh = try await client.getIfChanged(RunDetail.self, "v1", "runs", runID) {
                detail = fresh
            }
            error = nil
            lastSuccess = .now
        } catch {
            self.error = error
        }
    }

    /// Every 5 s by default (API spec §5.7). Returns when the task is
    /// cancelled — the view cancels it when it leaves the screen or the app
    /// leaves the foreground.
    public func poll(interval: Duration = .seconds(5),
                     sleep: @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }) async {
        while !Task.isCancelled {
            await refresh()
            do { try await sleep(interval) } catch { return }
        }
    }
}
