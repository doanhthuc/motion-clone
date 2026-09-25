import Foundation
import Observation

/// Saved try-ons (API spec §5.10): list, image, delete, and "Use in job" —
/// one `PATCH {slots, tryon_seed}` through `DraftStore` (Phase 6 spec §6).
///
/// The image matches the materials the entry names, not necessarily the whole
/// draft: the server merges slots per role (`scripts/control/drafts.py:451-456`),
/// so a role the entry has no id for — the optional `background`, say — survives
/// the patch while the seed points at an image made without it.
@MainActor @Observable
public final class TryonLibraryStore {
    public private(set) var entries: [TryonLibraryEntry] = []
    public private(set) var loaded = false
    public private(set) var lastSuccess: Date?
    public private(set) var error: APIError?
    public private(set) var message: String?
    public private(set) var isLoading = false

    private let client: APIClient
    private let draft: DraftStore
    private var images: [String: Data] = [:]

    public init(client: APIClient, draft: DraftStore) {
        self.client = client
        self.draft = draft
    }

    public var isStale: Bool { loaded && error != nil }

    public func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let response = try await client.get(TryonLibraryResponse.self, "v1", "tryon-library")
            entries = response.entries.sorted { $0.savedAt > $1.savedAt }
            let ids = Set(entries.map(\.id))
            images = images.filter { ids.contains($0.key) }
            loaded = true
            lastSuccess = .now
            error = nil
        } catch {
            self.error = error
            message = error.userMessage
        }
    }

    /// An entry's image never changes, so it is cached by id.
    ///
    /// `nil` deliberately covers both "this entry has no image" and "the phone
    /// couldn't reach the server": a library tile draws the same placeholder
    /// either way, and a thumbnail is never worth an error banner. Keep it one
    /// path — `MaterialsStore.thumbnail(for:)` collapses them for the same reason.
    public func image(id: String) async -> Data? {
        if let cached = images[id] { return cached }
        guard let data = try? await client.data("v1", "tryon-library", id, "image") else { return nil }
        images[id] = data
        return data
    }

    public func delete(_ entry: TryonLibraryEntry) async {
        message = nil
        do {
            // The decoding overload: the server answers 200, `delete(_:)` accepts 204 only.
            _ = try await client.delete(OkResponse.self, "v1", "tryon-library", entry.id)
            forget(entry.id)
        } catch {
            if case .server(status: 404, code: _, message: _) = error {
                // Deleted elsewhere (the bot, another phone) — the wanted state is
                // already true, so drop it locally instead of showing a failure.
                forget(entry.id)
                message = "That saved try-on was already deleted."
            } else {
                self.error = error
                message = error.userMessage
            }
        }
    }

    /// Fills the entry's materials and seeds the job with its image. The
    /// pipeline, provider and driver stay as they are; a pipeline that cannot
    /// use a seed answers 422 and its message is shown. An entry deleted
    /// elsewhere answers `seed_not_found` and leaves the list, as `delete`'s
    /// 404 does — kept, its tile could only repeat the same refusal.
    public func use(_ entry: TryonLibraryEntry) async -> Bool {
        message = nil
        let slots = entry.materialIDs.mapValues { Optional($0) }
        let ok = await draft.apply(DraftPatch(slots: slots, seed: .set(entry.id)))
        if !ok {
            if case .server(status: 404, code: "seed_not_found", message: _) = draft.error {
                forget(entry.id)
            }
            message = draft.message ?? draft.error?.userMessage
        }
        return ok
    }

    /// Entries made from exactly these materials (the driver is ignored: it is
    /// not part of a try-on image), newest first.
    public func matches(slots: [String: String]) -> [TryonLibraryEntry] {
        let wanted = slots.filter { $0.key != "driver" }
        return entries.filter { $0.materialIDs == wanted }
    }

    /// What in the current draft would lose its image if `id` were deleted.
    public func users(of id: String) -> [String] {
        guard let current = draft.draft else { return [] }
        var names = current.batch.filter { $0.tryonSeed == id }.map(\.runID)
        if current.tryonSeed == id { names.append("the job being edited") }
        return names
    }

    public func dismissMessage() { message = nil }

    private func forget(_ id: String) {
        entries.removeAll { $0.id == id }
        images[id] = nil
    }
}
