import Foundation
import Observation

/// `GET /v1/gpu/stock` and `PUT /v1/pod/gpu` (Phase 5 design §5). Shared by
/// the Pod tab, the rent panel's Change GPU sheet and the migrate sheet.
@MainActor @Observable
public final class GpuStore {
    public private(set) var stock: GpuStock?
    public private(set) var error: APIError?
    public private(set) var isLoading = false
    /// The catalog id being PUT, while it is in flight.
    public private(set) var selecting: String?
    public private(set) var message: String?

    private let client: APIClient
    private let pod: PodStore

    public init(client: APIClient, pod: PodStore) {
        self.client = client
        self.pod = pod
    }

    public var isStale: Bool { stock != nil && error != nil }

    /// `force` only from pull-to-refresh or Retry: uncached it is a runpodctl
    /// round trip (~30 s worst case). A 502 keeps the last good list.
    public func load(force: Bool = false) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let fresh = force
                ? try await client.get(GpuStock.self, query: [URLQueryItem(name: "force", value: "1")],
                                       timeout: 60, "v1", "gpu", "stock")
                : try await client.get(GpuStock.self, timeout: 60, "v1", "gpu", "stock")
            stock = fresh
            error = nil
        } catch {
            self.error = error
        }
    }

    /// True when the server changed `.env`'s GPU. Not a spend (no key), but
    /// refused while one is in flight: the confirm being sent was priced for
    /// the current card.
    @discardableResult
    public func select(_ gpu: String, whileSpending spending: Bool) async -> Bool {
        guard selecting == nil, gpu != stock?.selected else { return false }
        guard !spending else {
            message = "A spend request is in flight — wait for its answer before changing the GPU."
            return false
        }
        selecting = gpu
        message = nil
        defer { selecting = nil }
        do {
            let chosen = try await client.put(GpuSelection.self, body: GpuSelectionRequest(gpu: gpu),
                                              "v1", "pod", "gpu")
            stock?.selected = chosen.gpu
            await pod.refresh()
            return true
        } catch {
            message = error.userMessage
            return false
        }
    }
}
