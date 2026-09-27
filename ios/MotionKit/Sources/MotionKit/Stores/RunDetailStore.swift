import Foundation
import Observation

@MainActor @Observable
public final class RunDetailStore {
    public let runID: String
    public private(set) var detail: RunDetail?
    public private(set) var error: APIError?
    public private(set) var lastSuccess: Date?
    public let client: APIClient
    /// One read shared by every page that asks: two pages open at once, and a
    /// "loaded" flag set before the answer came made the second one give up.
    private var tryonLoad: Task<TryonPreviews?, Never>?
    private var tryonImages: [String: Data] = [:]
    /// The ETag of `detail` — this copy's, never another store's.
    private var etag: String?

    public init(client: APIClient, runID: String) {
        self.client = client
        self.runID = runID
    }

    public var isStale: Bool { detail != nil && error != nil }

    public func refresh() async {
        do {
            // nil = 304: the run did not change since the last poll.
            if let (fresh, tag) = try await client.getIfChanged(
                RunDetail.self, etag: detail == nil ? nil : etag, "v1", "runs", runID) {
                // The run changed, and a try-on regenerate is one such change:
                // read the previews again next time a page asks.
                if fresh.updatedAt != detail?.updatedAt { tryonLoad = nil }
                detail = fresh
                etag = tag
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
    /// video exists. The previews are read once per change of the run, not once
    /// per store: a regenerate after the run has jobs replaces the image under
    /// the same index, and this store lives for the whole app session — keyed
    /// on the index alone, the Runs card kept the first version (2026-09-27).
    /// Images are cached by index and `rev`, so an unchanged one is not
    /// downloaded again. A run without a try-on stage answers 404 and every
    /// job gets nil.
    public func tryonImage(forJob job: String) async -> Data? {
        if tryonLoad == nil {
            let client = client, runID = runID
            tryonLoad = Task { try? await client.get(TryonPreviews.self, "v1", "runs", runID, "tryon") }
        }
        let tryon = await tryonLoad?.value
        guard let preview = tryon?.previews.first(where: { $0.run == job }), preview.hasImage else { return nil }
        let key = "\(preview.index)@\(preview.rev ?? "")"
        if let cached = tryonImages[key] { return cached }
        guard let data = try? await client.data("v1", "runs", runID, "tryon", preview.index) else { return nil }
        tryonImages[key] = data
        return data
    }
}
