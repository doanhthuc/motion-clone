import Foundation
import Observation

@MainActor @Observable
public final class PodStore {
    public private(set) var pod: PodStatus?
    public private(set) var error: APIError?
    public private(set) var lastSuccess: Date?
    private let client: APIClient

    public init(client: APIClient) { self.client = client }

    public var isStale: Bool { pod != nil && error != nil }

    /// `GET /v1/pod` is files and memory only on the server — cheap to call.
    public func refresh() async {
        do {
            pod = try await client.get(PodStatus.self, "v1", "pod")
            error = nil
            lastSuccess = .now
        } catch {
            self.error = error
        }
    }
}
