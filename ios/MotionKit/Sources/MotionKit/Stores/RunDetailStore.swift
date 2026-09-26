import Foundation
import Observation

@MainActor @Observable
public final class RunDetailStore {
    public let runID: String
    public private(set) var detail: RunDetail?
    public private(set) var error: APIError?
    public private(set) var lastSuccess: Date?
    public let client: APIClient
    private var tryon: TryonPreviews?
    private var tryonLoaded = false
    private var tryonImages: [String: Data] = [:]

    public init(client: APIClient, runID: String) {
        self.client = client
        self.runID = runID
    }

    public var isStale: Bool { detail != nil && error != nil }

    public func refresh() async {
        do {
            // nil = 304: the run did not change since the last poll.
            if let fresh = try await client.getIfChanged(RunDetail.self, haveCopy: detail != nil,
                                                           "v1", "runs", runID) {
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

    /// The try-on a job was made from — the one picture a job has before its
    /// video exists. Read once per screen: Phase A is over by the time a run has
    /// jobs, so the previews do not move under a detail poll. A run without a
    /// try-on stage answers 404 and every job gets nil.
    public func tryonImage(forJob job: String) async -> Data? {
        if !tryonLoaded {
            tryonLoaded = true
            tryon = try? await client.get(TryonPreviews.self, "v1", "runs", runID, "tryon")
        }
        guard let preview = tryon?.previews.first(where: { $0.run == job }), preview.hasImage else { return nil }
        if let cached = tryonImages[preview.index] { return cached }
        guard let data = try? await client.data("v1", "runs", runID, "tryon", preview.index) else { return nil }
        tryonImages[preview.index] = data
        return data
    }
}
