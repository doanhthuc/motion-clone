import Foundation
import Observation

@MainActor @Observable
public final class DraftStore {
    public private(set) var catalog: [Pipeline] = []
    public private(set) var draft: Draft?
    public private(set) var loaded = false
    public private(set) var lastSuccess: Date?
    public private(set) var error: APIError?
    public private(set) var message: String?
    public private(set) var isRefreshing = false
    public private(set) var isMutating = false
    public private(set) var isValidating = false
    public private(set) var validationWasStale = false
    public private(set) var needsMaterialsRefresh = false

    private let client: APIClient
    // Assignment can probe for 60 s and validation for 90 s; stay below
    // Cloudflare's 100 s origin ceiling without extending unrelated calls.
    private static let slowDraftTimeout: TimeInterval = 95

    public init(client: APIClient) {
        self.client = client
    }

    public var isStale: Bool {
        loaded && error != nil
    }

    public var isBusy: Bool {
        isMutating || isValidating
    }

    public var selectedPipeline: Pipeline? {
        guard let id = draft?.pipeline else { return nil }
        return catalog.first { $0.id == id }
    }

    public var isReady: Bool {
        draft?.validated == true && !validationWasStale
    }

    public func load() async {
        guard !isRefreshing && !isBusy else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            async let catalogResponse = client.get(PipelineCatalogResponse.self, "v1", "pipelines")
            async let draftResponse = client.get(Draft.self, "v1", "draft")
            let values = try await (catalogResponse, draftResponse)
            catalog = values.0.pipelines
            accept(values.1)
        } catch {
            let api = apiError(error)
            self.error = api
            message = api.userMessage
        }
    }

    public func refresh() async {
        guard !isRefreshing && !isBusy else { return }
        if catalog.isEmpty {
            await load()
            return
        }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            accept(try await client.get(Draft.self, "v1", "draft"))
        } catch let api {
            self.error = api
            message = api.userMessage
        }
    }

    public func selectPipeline(_ pipeline: String) async {
        await mutate {
            try await self.client.patch(
                Draft.self, body: PipelinePatch(pipeline: pipeline), "v1", "draft")
        }
    }

    public func selectProvider(_ provider: String) async {
        await mutate {
            try await self.client.patch(
                Draft.self, body: ProviderPatch(provider: provider), "v1", "draft")
        }
    }

    public func assign(role: String, materialID: String?) async {
        await mutate(materialAssignment: true) {
            try await self.client.patch(
                Draft.self, body: SlotPatch(role: role, materialID: materialID),
                timeout: Self.slowDraftTimeout, "v1", "draft")
        }
    }

    public func addToBatch() async {
        await mutate {
            try await self.client.post(Draft.self, "v1", "draft", "add-to-batch")
        }
    }

    public func dropFromBatch(_ digest: String) async {
        await mutate {
            try await self.client.delete(Draft.self, "v1", "draft", "batch", digest)
        }
    }

    public func clear() async {
        await mutate {
            try await self.client.post(Draft.self, "v1", "draft", "clear")
        }
    }

    public func validate() async {
        guard !isBusy else {
            message = "Another draft change is still in progress."
            return
        }
        isValidating = true
        error = nil
        message = nil
        defer { isValidating = false }
        do {
            let response = try await client.post(
                DraftValidationResponse.self, timeout: Self.slowDraftTimeout,
                "v1", "draft", "validate")
            accept(response.draft)
            validationWasStale = response.stale
            if response.stale {
                message = "The draft changed during validation. Validate it again."
            }
        } catch {
            let api = apiError(error)
            if case .server(status: 422, code: "invalid", message: _) = api {
                await refreshAfterAmbiguousWrite()
            } else if api.isOffline {
                await refreshAfterAmbiguousWrite()
            }
            self.error = api
            message = api.userMessage
        }
    }

    public func dismissMessage() {
        message = nil
    }

    public func acknowledgeMaterialsRefresh() {
        needsMaterialsRefresh = false
    }

    private func mutate(
        materialAssignment: Bool = false,
        _ operation: () async throws -> Draft
    ) async {
        guard !isBusy else {
            message = "Another draft change is still in progress."
            return
        }
        isMutating = true
        error = nil
        message = nil
        validationWasStale = false
        defer { isMutating = false }
        do {
            accept(try await operation())
        } catch {
            let api = apiError(error)
            if materialAssignment,
               case let .server(status: 404, code: _, message: serverMessage) = api {
                needsMaterialsRefresh = true
                await refreshAfterAmbiguousWrite()
                self.error = api
                message = serverMessage
                return
            }
            if api.isOffline {
                await refreshAfterAmbiguousWrite()
            }
            self.error = api
            message = api.userMessage
        }
    }

    private func refreshAfterAmbiguousWrite() async {
        do {
            accept(try await client.get(Draft.self, "v1", "draft"))
        } catch {
            // The original write error remains the actionable one.
        }
    }

    private func accept(_ draft: Draft) {
        self.draft = draft
        validationWasStale = false
        loaded = true
        lastSuccess = .now
        error = nil
        if let dropped = draft.dropped, !dropped.isEmpty {
            message = "Removed incompatible slots: \(dropped.joined(separator: ", "))."
        } else {
            message = nil
        }
        checkContract()
    }

    private func checkContract() {
        guard !catalog.isEmpty else {
            setContractError("The pipeline catalog is empty.")
            return
        }
        guard let pipeline = draft?.pipeline, catalog.contains(where: { $0.id == pipeline }) else {
            setContractError("The draft pipeline is unavailable in the server catalog.")
            return
        }
    }

    private func setContractError(_ detail: String) {
        let error = APIError.decoding(detail)
        self.error = error
        message = error.userMessage
    }

    private func apiError(_ error: any Error) -> APIError {
        error as? APIError ?? .transport(error.localizedDescription)
    }
}
