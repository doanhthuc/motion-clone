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
    /// Built here rather than by editing `Fixtures.swift`: `ModelsTests`'
    /// `draftSeedIsOptionalAndDecodes` does exact `.replacingOccurrences`
    /// surgery on that literal, so adding a key there could silently break
    /// assertions in another suite. Same pattern as
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
            // Two 404 shapes, because they exercise different branches: `gone`
            // is not in `entries` (the tile was never on screen), while `new`
            // IS — that is the one where the 404 branch's local `forget` is
            // observable.
            case ("DELETE", "/v1/tryon-library/gone"), ("DELETE", "/v1/tryon-library/new"):
                return TestSupport.json(#"{"error":{"code":"not_found","message":"no such try-on library entry"}}"#, status: 404)
            case ("DELETE", _): return TestSupport.json(#"{"ok":true}"#)
            // `old` was deleted elsewhere after the list loaded: "Use in job"
            // names it as the seed and the server refuses before writing.
            case ("PATCH", "/v1/draft") where (request.httpBody.map { String(decoding: $0, as: UTF8.self) } ?? "").contains(#""tryon_seed":"old""#):
                return TestSupport.json(#"{"error":{"code":"seed_not_found","message":"no such try-on library entry: old"}}"#, status: 404)
            case ("GET", "/v1/draft"), ("PATCH", "/v1/draft"): return TestSupport.json(draftJSON)
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

    /// The 404 branch's local removal, against an entry that is actually on
    /// screen. This is the case that runs when the bot or a second phone
    /// deleted it: without `forget`, the grid keeps a tile whose image and
    /// "Use in job" both 404 from then on. `deleteRemovesTheEntryAndAGoneEntryToo`
    /// cannot show it — its 404 id was never in `entries`.
    @Test func deleteOfAnEntryGoneElsewhereRemovesTheTile() async {
        let (store, _) = make()
        await store.load()
        #expect(store.entries.map(\.id) == ["bg", "new", "old"])

        await store.delete(store.entries[1])           // "new" — the stub 404s it

        #expect(store.message == "That saved try-on was already deleted.")
        #expect(store.entries.map(\.id) == ["bg", "old"])
        #expect(store.error == nil)                    // a wanted state, not a failure
    }

    @Test func useSendsMaterialIdsAndSeedInOnePatch() async throws {
        let (store, draft) = make()
        await draft.load()
        await store.load()
        let ok = await store.use(store.entries[1])
        #expect(ok)
        // "One" patch, pinned: `.last` alone would still pass a regression that
        // sent the slots and the seed separately, which the server would merge
        // per role and leave half-applied if the second one failed.
        #expect(StubURLProtocol.requests.filter { $0.httpMethod == "PATCH" }.count == 1)
        let patch = try #require(StubURLProtocol.requests.last { $0.httpMethod == "PATCH" })
        let body = try #require(JSONSerialization.jsonObject(with: patch.httpBody ?? Data()) as? [String: Any])
        #expect(body["tryon_seed"] as? String == "new")
        #expect(body["slots"] as? [String: String] == ["character": "app/me.png", "outfit": "app/o1.png"])
    }

    /// A seed deleted elsewhere (the bot, another phone) answers `seed_not_found`.
    /// The tile goes, the same way `delete`'s 404 drops it: left in place, a
    /// second tap would only repeat the same refusal.
    @Test func useOfAnEntryGoneElsewhereRemovesTheTile() async {
        let (store, draft) = make()
        await draft.load()
        await store.load()

        let ok = await store.use(store.entries[2])     // "old" — the stub refuses its seed

        #expect(!ok)
        #expect(store.message == "That saved try-on no longer exists.")
        #expect(store.entries.map(\.id) == ["bg", "new"])
    }

    /// The Saved try-ons screen renders this list as its delete confirmation's
    /// warning, so all three branches matter: basket runs, the edited job, and
    /// an id nothing references.
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
