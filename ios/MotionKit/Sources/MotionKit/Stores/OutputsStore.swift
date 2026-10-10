import Foundation
import Observation

@MainActor @Observable
public final class OutputsStore {
    public private(set) var batches: [OutputBatch] = []
    public private(set) var loaded = false
    public private(set) var error: APIError?
    /// The last delete's refusal, shown over the grid until dismissed.
    public private(set) var message: String?
    public private(set) var lastSuccess: Date?
    public let client: APIClient

    public init(client: APIClient) { self.client = client }

    public var isStale: Bool { loaded && error != nil }

    /// The id a selection holds for one file: batch and name together, since
    /// two batches can each have an `IMG7145-…-2.mp4`.
    public static func key(_ batch: String, _ file: OutputFile) -> String { "\(batch)/\(file.name)" }

    public func file(for key: String) -> (batch: String, file: OutputFile)? {
        for batch in batches {
            if let file = batch.files.first(where: { Self.key(batch.batch, $0) == key }) { return (batch.batch, file) }
        }
        return nil
    }

    /// `DELETE /v1/outputs/<batch>/<name>` for each, one at a time and past a
    /// failure. A file already gone counts as deleted. Returns the keys still
    /// on the server, for a selection to keep.
    @discardableResult
    public func delete(_ keys: [String]) async -> Set<String> {
        var failed: [(String, APIError)] = []
        for key in keys {
            // Parsed, not looked up: the feed opened from a run deletes before
            // this tab's list was ever loaded. A batch is one path component.
            let parts = key.split(separator: "/", maxSplits: 1).map(String.init)
            guard parts.count == 2 else { continue }
            let (batch, name) = (parts[0], parts[1])
            do {
                try await client.delete("v1", "outputs", batch, name)
                forget(batch, name)
            } catch {
                if case .server(status: 404, _, _) = error { forget(batch, name) } else { failed.append((key, error)) }
            }
        }
        message = MaterialsStore.bulkMessage(failed: failed.map(\.1), of: keys.count)
        return Set(failed.map(\.0))
    }

    public func dismissMessage() { message = nil }

    private func forget(_ batch: String, _ name: String) {
        batches = batches.compactMap { b in
            guard b.batch == batch else { return b }
            let files = b.files.filter { $0.name != name }
            return files.isEmpty ? nil : OutputBatch(batch: b.batch, updatedAt: b.updatedAt, files: files)
        }
    }

    public func refresh() async {
        do {
            batches = try await client.get(OutputsResponse.self, "v1", "outputs").outputs
            loaded = true
            error = nil
            lastSuccess = .now
        } catch {
            self.error = error
        }
    }
}
