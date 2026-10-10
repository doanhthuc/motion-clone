import Foundation
import Observation

@MainActor @Observable
public final class RunsStore {
    public private(set) var runs: [RunSummary] = []
    public private(set) var loaded = false
    public private(set) var error: APIError?
    public private(set) var lastSuccess: Date?
    private let client: APIClient
    /// One detail store per run, kept across refreshes: a card reads its run's
    /// jobs from it, and opening the card shows that copy at once.
    @ObservationIgnored private var details: [String: RunDetailStore] = [:]

    /// Told about a run whose slow-stage count just went up. Set by the app; nil in tests.
    @ObservationIgnored public var onSlowRun: (@MainActor (SlowRunNotice) -> Void)?
    @ObservationIgnored private var slowSeen: [String: Int] = [:]

    public init(client: APIClient) { self.client = client }

    /// The run shown as the hero card: the first one executing right now.
    public var live: RunSummary? { runs.first { $0.status.isLive } }
    public var recent: [RunSummary] { runs.filter { $0.id != live?.id } }
    public var isStale: Bool { loaded && error != nil }

    public func detailStore(for runID: String) -> RunDetailStore {
        if let store = details[runID] { return store }
        let store = RunDetailStore(client: client, runID: runID)
        details[runID] = store
        return store
    }

    /// Deletes the run on the VPS, and its Outputs videos with `withVideos`.
    /// Gone from the list at once on success; on a refusal (running, pod
    /// attached) the list is untouched and the error is thrown for the caller
    /// to show.
    @discardableResult
    public func delete(_ runID: String, withVideos: Bool) async throws(APIError) -> RunDeleted {
        let result = try await client.delete(
            RunDeleted.self, query: [URLQueryItem(name: "videos", value: withVideos ? "1" : "0")],
            "v1", "runs", runID)
        runs.removeAll { $0.id == runID }
        details[runID] = nil
        return result
    }

    /// `delete(_:withVideos:)` for each run in turn, past a failure. A run
    /// already gone counts as deleted.
    public func delete(_ runIDs: [String], withVideos: Bool) async -> BulkRunDeletion {
        var result = BulkRunDeletion()
        for id in runIDs {
            do throws(APIError) {
                result.videosDeleted += try await delete(id, withVideos: withVideos).videosDeleted
                result.deleted += 1
            } catch {
                if case .server(status: 404, _, _) = error {
                    runs.removeAll { $0.id == id }
                    result.deleted += 1
                } else {
                    result.failed.append((id, error))
                }
            }
        }
        return result
    }

    public func refresh() async {
        do {
            runs = try await client.get(RunsResponse.self, "v1", "runs").runs
            for notice in SlowRunNotice.newlySlow(before: slowSeen, after: runs) { onSlowRun?(notice) }
            slowSeen = Dictionary(uniqueKeysWithValues: runs.map { ($0.id, $0.slowStages ?? 0) })
            loaded = true
            error = nil
            lastSuccess = .now
        } catch {
            self.error = error
        }
    }
}

public struct BulkRunDeletion: Sendable {
    public var deleted = 0
    public var videosDeleted = 0
    public var failed: [(id: String, error: APIError)] = []
    public init() {}
}
