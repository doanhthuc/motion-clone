import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct TryonLibraryStoreTests {
    // `nonisolated` because StubURLProtocol's handler is `@Sendable` and runs off
    // the main actor; a suite-isolated static cannot be read from there.
    nonisolated static let library = #"""
    {"entries":[
      {"id":"old","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"gemini","saved_at":100},
      {"id":"new","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png"},"provider":"qwen-max","saved_at":200},
      {"id":"bg","owner":"app","material_ids":{"character":"app/me.png","outfit":"app/o1.png","background":"app/bg.png"},"provider":"gemini","saved_at":300}
    ]}
    """#

    /// `Fixtures.draft` with seeds: the edited job and basket entry `model__dress`
    /// both carry `tryon_seed: "seed-1"`, and a second basket entry carries
    /// `"other"` so a non-matching seed has to be filtered out, not just matched.
    ///
    /// Built here rather than by editing `Fixtures.swift`: Task 2's model test does
    /// exact `.replacingOccurrences` surgery on that literal, so adding a key there
    /// could silently break assertions in another suite. Same pattern as
    /// `DraftStoreTests.draftResponse`. If an anchor ever stops matching the
    /// replacement no-ops, and `usersListsBasketRunsBeforeTheEditedJob` then fails
    /// loudly instead of passing vacuously.
    nonisolated static let seededDraft = Fixtures.draft
        .replacingOccurrences(
            of: #""driver":null}}]"#,
            with: #""driver":null},"tryon_seed":"seed-1"},{"digest":"fff999","run_id":"model__ao-dai","pipeline":"tryon-motion-enhance","provider":"gemini","slots":{"character":"app/model.png"},"tryon_seed":"other"}]"#)
        .replacingOccurrences(
            of: #""jobs":1,"estimate_min":null}"#,
            with: #""jobs":1,"estimate_min":null,"tryon_seed":"seed-1"}"#)

    private func make(draft draftJSON: String = Fixtures.draft) -> (TryonLibraryStore, DraftStore) {
        StubURLProtocol.install { request in
            let path = request.url?.path ?? ""
            switch (request.httpMethod ?? "GET", path) {
            case ("GET", "/v1/tryon-library"): return TestSupport.json(Self.library)
            case ("GET", "/v1/pipelines"): return TestSupport.json(Fixtures.pipelines)
            case ("GET", "/v1/draft"), ("PATCH", "/v1/draft"): return TestSupport.json(draftJSON)
            case ("DELETE", "/v1/tryon-library/gone"):
                return TestSupport.json(#"{"error":{"code":"not_found","message":"no such try-on library entry"}}"#, status: 404)
            case ("DELETE", _): return TestSupport.json(#"{"ok":true}"#)
            case ("GET", _) where path.hasSuffix("/image"): return (200, ["Content-Type": "image/png"], Data("png".utf8))
            default: return (404, [:], Data())
            }
        }
        let client = TestSupport.client()
        let draft = DraftStore(client: client)
        return (TryonLibraryStore(client: client, draft: draft), draft)
    }

    @Test func loadSortsNewestFirst() async {
        let (store, _) = make()
        await store.load()
        #expect(store.entries.map(\.id) == ["bg", "new", "old"])
        #expect(store.loaded && !store.isStale)
    }

    @Test func matchesCompareEveryNonDriverRoleExactly() async {
        let (store, _) = make()
        await store.load()
        #expect(store.matches(slots: ["character": "app/me.png", "outfit": "app/o1.png",
                                      "driver": "app/dance.mp4"]).map(\.id) == ["new", "old"])
        #expect(store.matches(slots: ["character": "app/me.png", "outfit": "app/o1.png",
                                      "background": "app/bg.png"]).map(\.id) == ["bg"])
        #expect(store.matches(slots: ["character": "app/me.png", "outfit": "app/o2.png"]).isEmpty)
    }

    @Test func imagesAreCachedById() async {
        let (store, _) = make()
        await store.load()
        _ = await store.image(id: "new")
        _ = await store.image(id: "new")
        #expect(StubURLProtocol.requests.filter { $0.url?.path == "/v1/tryon-library/new/image" }.count == 1)
    }

    @Test func deleteRemovesTheEntryAndAGoneEntryToo() async {
        let (store, _) = make()
        await store.load()
        await store.delete(store.entries[0])
        #expect(store.entries.map(\.id) == ["new", "old"])
        await store.delete(TryonLibraryEntry(id: "gone", materialIDs: [:], provider: "gemini", savedAt: 1))
        #expect(store.message == "That saved try-on was already deleted.")
    }

    @Test func useSendsMaterialIdsAndSeedInOnePatch() async throws {
        let (store, draft) = make()
        await draft.load()
        await store.load()
        let ok = await store.use(store.entries[1])
        #expect(ok)
        let patch = try #require(StubURLProtocol.requests.last { $0.httpMethod == "PATCH" })
        let body = try #require(JSONSerialization.jsonObject(with: patch.httpBody ?? Data()) as? [String: Any])
        #expect(body["tryon_seed"] as? String == "new")
        #expect(body["slots"] as? [String: String] == ["character": "app/me.png", "outfit": "app/o1.png"])
    }

    /// Task 7 renders this list as the delete confirmation's warning, so all three
    /// branches matter: basket runs, the edited job, and an id nothing references.
    @Test func usersListsBasketRunsBeforeTheEditedJob() async {
        let (store, draft) = make(draft: Self.seededDraft)
        // No draft loaded yet — nothing to warn about, and nothing to crash on.
        #expect(store.users(of: "seed-1").isEmpty)

        await draft.load()
        await store.load()

        #expect(store.users(of: "seed-1") == ["model__dress", "the job being edited"])
        // A seed only the basket uses: no edited-job line, and `model__dress`
        // (seeded with `seed-1`) must not leak in.
        #expect(store.users(of: "other") == ["model__ao-dai"])
        #expect(store.users(of: "no-such-seed").isEmpty)
    }
}
}
