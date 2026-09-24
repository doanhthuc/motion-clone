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

    /// Slots and the try-on seed in one PATCH (Phase 6: cross build, "Use in job").
    /// The 95 s timeout is unconditional, so a seed-only patch that probes nothing pays it too.
    /// A 404 trips `needsMaterialsRefresh` only on a patch that carries slots —
    /// `mutate` sets the flag solely under `materialAssignment`, which this passes as
    /// `!patch.slots.isEmpty` — and means a material this patch named is gone. A deleted seed
    /// answers `seed_not_found` instead and trips nothing: the server resolves it before writing
    /// anything (`scripts/control/drafts.py`, `patch`). The one shipped seed-only patch is
    /// `NewJobView`'s `.clear`, and it cannot 404 on a seed at all: the server resolves a library
    /// entry only for a truthy id.
    @discardableResult
    public func apply(_ patch: DraftPatch) async -> Bool {
        await mutate(materialAssignment: !patch.slots.isEmpty) {
            try await self.client.patch(
                Draft.self, body: patch, timeout: Self.slowDraftTimeout, "v1", "draft")
        }
    }

    @discardableResult
    public func addToBatch() async -> Bool {
        await mutate {
            try await self.client.post(Draft.self, "v1", "draft", "add-to-batch")
        }
    }

    @discardableResult
    public func dropFromBatch(_ digest: String) async -> Bool {
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

    /// Returns whether the write landed. `false` leaves `message` explaining why, so
    /// callers that gate UI on it never have to re-derive the failure from `error`.
    @discardableResult
    private func mutate(
        materialAssignment: Bool = false,
        _ operation: () async throws -> Draft
    ) async -> Bool {
        guard !isBusy else {
            message = "Another draft change is still in progress."
            return false
        }
        isMutating = true
        error = nil
        message = nil
        validationWasStale = false
        defer { isMutating = false }
        do {
            accept(try await operation())
            return true
        } catch {
            let api = apiError(error)
            if materialAssignment,
               case let .server(status: 404, code: code, message: serverMessage) = api,
               code != "seed_not_found" {
                needsMaterialsRefresh = true
                await refreshAfterAmbiguousWrite()
                self.error = api
                message = serverMessage
                return false
            }
            if api.isOffline {
                await refreshAfterAmbiguousWrite()
            }
            self.error = api
            message = api.userMessage
            return false
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
