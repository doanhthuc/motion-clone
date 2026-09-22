import Foundation
import Observation

@MainActor @Observable
public final class OutputsStore {
    public private(set) var batches: [OutputBatch] = []
    public private(set) var loaded = false
    public private(set) var error: APIError?
    public private(set) var lastSuccess: Date?
    public let client: APIClient

    public init(client: APIClient) { self.client = client }

    public var isStale: Bool { loaded && error != nil }

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
