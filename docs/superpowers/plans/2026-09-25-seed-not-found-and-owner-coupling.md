# `seed_not_found` and One Owner Constant — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deleted try-on library seed reaches the phone as its own `404 seed_not_found` with its own sentence and no materials reload; and `bot.py` builds saved-try-on material ids from `materials.APP_OWNER` instead of the literal `"app"`.

**Architecture:** One raise site in `scripts/control/drafts.py` changes code; the HTTP status table in `scripts/httpapi/server.py` maps it to 404 (the default for an unmapped code is 400). The iOS `DraftStore.mutate` 404 branch excludes that code, and `APIError.userMessage` gets a copy line for it. Separately, `AppRuns.tryon_save_info` sources its owner prefix from the constant, pinned by a test comparing it against `materials.material_item`.

**Tech Stack:** Python 3 stdlib (`unittest`), Swift 6 / Swift Testing (`ios/MotionKit`).

**Spec:** `docs/superpowers/specs/2026-09-25-seed-not-found-and-owner-coupling-design.md` — read it before starting any task; the plan argues from it.

## Global Constraints

- New code string is exactly `seed_not_found`; HTTP status 404 (added to `_DOMAIN_STATUS`, never left to the 400 default).
- Only `drafts.py:389` changes code. `_resolve` (`:359`, `:363`), the under-lock re-check (`:445`) and `drop_from_batch` (`:481`) keep `not_found`.
- `runs.py`'s `OUTCOME_STATUS` does **not** gain an entry.
- iOS copy, verbatim: `"That saved try-on no longer exists."` The bare-404 sentence `"Not found — it may have been removed from Telegram."` stays for everything else.
- `bot.py`'s owner fix is `f"{materials.APP_OWNER}/{path.name}"` — never a `material_item(...)` call (it stats; a past run's inputs may be gone).
- Write in English. No `# #region ALD` markers. Comments explain why, matching surrounding density.
- Nothing here rents a pod or calls a try-on provider.
- `motions-studio/setup/scrub-secrets.sh --check` must exit 0 before every commit (repo is public).
- Work on branch `seed-not-found-owner-constant` (created before Task 1). One branch, one PR.

## Review Focus

1. **A cross-build patch (valid seed + stale `slots.outfit`)** must still report `not_found` and trip the materials refresh — the reason the "skip on any seed" approach was rejected. Pinned in Task 1 (server) and Task 3 (app `not_found` regression half).
2. **A `seed_not_found` refusal must write nothing** — the app skips its re-read on the strength of that. Pinned in Task 1 (draft file byte-identical).
3. **The HTTP layer must say 404, not 400**, end to end. Pinned in Task 1 (`_DOMAIN_STATUS` unit + a live-route refusal case).
4. **A `seed_not_found` on the app must not issue a GET `/v1/draft`** (no `refreshAfterAmbiguousWrite`) — only the PATCH. Pinned in Task 3 by asserting the request method list.
5. **Old server → new app**: a seed 404 still arriving as `not_found` falls through to today's material path, no crash. Covered by Task 3's `not_found` half (same code path) — no extra test.

---

### Task 0 (controller, not a subagent): branch

- [ ] `git checkout -b seed-not-found-owner-constant` from clean `main`.

### Task 1: Server — `seed_not_found` code and its 404 mapping

**Files:**
- Modify: `scripts/control/drafts.py:389`
- Modify: `scripts/httpapi/server.py:38-45` (`_DOMAIN_STATUS`)
- Test: `scripts/tests/test_batch_control_drafts.py` (`test_tryon_seed_refusals`, ~`:384-390`, plus new tests in the same class)
- Test: `scripts/tests/test_batch_control_http.py` (`TestDraftRoutes.test_refusal_statuses`, ~`:758-771`, plus a new unit test)
- Modify: `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` §5.6 status table (~`:228`)

**Interfaces:**
- Produces: `DraftError("seed_not_found", "no such try-on library entry: <id>")` from `DraftStore.patch`; HTTP `404 {"error":{"code":"seed_not_found",...}}` from `PATCH /v1/draft`. Task 3 relies on exactly that code string.

- [ ] **Step 1: Change the existing seed assertions and add the new store tests.** In `test_batch_control_drafts.py`, `test_tryon_seed_refusals` becomes:

```python
    def test_tryon_seed_refusals(self):
        # Its own code, not not_found: a deleted library entry is not a stale
        # material, and the phone reloads its materials list on not_found.
        self.assertRefused("seed_not_found", self.store.patch, {"tryon_seed": "nope"})
        self.assertRefused("bad_request", self.store.patch, {"tryon_seed": 3})
        # An unknown id must not half-apply the rest of the same patch.
        self.assertRefused("seed_not_found", self.store.patch,
                           {"provider": "qwen-max", "tryon_seed": "nope"})
        self.assertEqual(self.store.view()["provider"], "gemini")

    def test_seed_not_found_leaves_the_draft_byte_identical(self):
        # The app skips its ambiguity re-read on this code because the seed is
        # resolved above control.LOCK, before anything is written. Asserted
        # here rather than trusted.
        self.fill()
        before = self.store.path.read_bytes()
        self.assertRefused("seed_not_found", self.store.patch,
                           {"slots": {"background": "app/bg.png"}, "tryon_seed": "nope"})
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_a_stale_material_beside_a_valid_seed_is_still_not_found(self):
        # The cross build sends tryon_seed together with slots.outfit. A gone
        # material in that patch must keep saying not_found, so the phone
        # still refreshes its materials list — narrowing pinned from this side.
        self.fill()
        self.assertRefused("not_found", self.store.patch,
                           {"slots": {"outfit": "app/missing.png"},
                            "tryon_seed": self.saved_seed()})
```

Leave the material assertions (`:260-262`, `:304`) and `drop_from_batch`'s (`:467`) exactly as they are — they must still say `not_found`.

If `self.fill()` leaves a pipeline where `background` is not a usable role, the byte-identical test still refuses on the seed first (seed resolves before slots are even resolved, `drafts.py:385-399`), so the role choice does not matter; keep `background`.

- [ ] **Step 2: Add the HTTP tests.** In `test_batch_control_http.py`, `TestDraftRoutes.test_refusal_statuses`'s `cases` gains one row after the `app/none.png` row (which stays `not_found`):

```python
            ("PATCH", "/v1/draft", {"tryon_seed": "nope"}, 404, "seed_not_found"),
```

And add a unit test to `TestDraftRoutes` (or `TestErrors` if it reads more naturally there):

```python
    def test_seed_not_found_is_mapped_to_404(self):
        # An unmapped DraftError code falls through to _DOMAIN_STATUS.get(code, 400),
        # so a missing table entry would silently turn this 404 into a 400.
        self.assertEqual(server_module._DOMAIN_STATUS["seed_not_found"], 404)
```

The file imports names from `httpapi.server` but not the module itself; add `import httpapi.server as server_module` next to the existing `from httpapi.server import ...` line (`:23`).

- [ ] **Step 3: Run and watch them fail.**

Run: `cd scripts && python3 -m unittest tests.test_batch_control_drafts tests.test_batch_control_http 2>&1 | tail -20`
Expected: FAILs in `test_tryon_seed_refusals`, `test_seed_not_found_leaves_the_draft_byte_identical` (code is `not_found`), `test_refusal_statuses` (subTest for `tryon_seed`), and `KeyError: 'seed_not_found'`. `test_a_stale_material_beside_a_valid_seed_is_still_not_found` passes already (it is a guard).

- [ ] **Step 4: Implement.** `drafts.py:389`:

```python
                raise DraftError("seed_not_found", f"no such try-on library entry: {tryon_seed}")
```

`server.py` `_DOMAIN_STATUS`: add, next to `"not_found": 404` or as a commented entry in the style of the existing `not_local` comment:

```python
                  # DraftStore.patch's tryon_seed naming a library entry that was
                  # deleted: 404 like not_found, but its own code so the phone
                  # does not mistake it for a stale material and reload them.
                  "seed_not_found": 404,
```

Do **not** touch `runs.py`'s `OUTCOME_STATUS`, the tryon-library DELETE route at `server.py:395`, or any other `not_found` site.

- [ ] **Step 5: Run the full gate.**

Run: `cd /Users/thucpham/Desktop/motion-clone && make batch-test 2>&1 | tail -5`
Expected: `OK` (no failures, no errors).

- [ ] **Step 6: Amend the control-plane spec in place.** In `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` §5.6, change the `404` row and add a note under the table:

```markdown
| `404` | Unknown id, or a path outside its root. A draft PATCH whose `tryon_seed` names a deleted library entry is `404 seed_not_found`, distinct from a gone material's `404 not_found` |
```

```markdown
*Amended 2026-09-25:* domain error codes map to statuses through `_DOMAIN_STATUS` in
`scripts/httpapi/server.py`, and **an unmapped code becomes `400`** — so a new code needs a table
entry, not just a raise. `seed_not_found` was added this way; see
`2026-09-25-seed-not-found-and-owner-coupling-design.md`.
```

- [ ] **Step 7: Commit.**

```bash
cd /Users/thucpham/Desktop/motion-clone
motions-studio/setup/scrub-secrets.sh --check
git add scripts/control/drafts.py scripts/httpapi/server.py scripts/tests/test_batch_control_drafts.py scripts/tests/test_batch_control_http.py docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md
git commit -m "feat(api): a deleted try-on seed is 404 seed_not_found, not not_found"
```

(End the message with the `Co-Authored-By` trailer from the session's attribution rules.)

---

### Task 2: Server — one owner constant in `tryon_save_info`

**Files:**
- Modify: `scripts/tgbot/bot.py:7259`
- Test: `scripts/tests/test_batch_control_botruns.py` — new class after `TestTryonVersionImage` (~`:1201`)

**Interfaces:**
- Consumes: `materials.APP_OWNER` (`scripts/control/materials.py:199`, value `"app"`), `materials.material_item(owner: str, path: Path) -> dict` (stats the file, returns `{"id": f"{owner}/{path.name}", ...}`), `bot.AppRuns.tryon_save_info(index: str) -> tuple[Path, dict, str] | None`. `materials` is already imported in `bot.py:55`; the test module does not import it yet — add `from control import materials` beside `import control.drafts as drafts` (`:17`).
- Produces: nothing new.

- [ ] **Step 1: Write the coupling test.** Reuse `_AppRunsFixture`'s helpers exactly as `TestTryonVersionImage._seed_tryon_run_with_version` does:

```python
class TestTryonSaveInfoMaterialIds(_AppRunsFixture):
    """tryon_save_info builds a saved try-on's material_ids by hand, without
    stat-ing (a past run's inputs may be gone). Pinned against material_item —
    a second, independent builder of the same id — so the two cannot drift.
    Patching APP_OWNER and checking both moved would pass vacuously: both
    sides read the same constant."""

    def test_material_ids_match_material_item(self):
        job = self._tryon_job()
        manifest = self._write_live_manifest(job)
        run = manifest.runs[0]
        image = self.root / "out" / "batch1" / "runs" / run.id / "01-tryon.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"img")
        self._write_journal(run.id, tryon={"status": "done", "file": str(image)})

        info = self.runs.tryon_save_info("0")
        self.assertIsNotNone(info)
        _image, material_ids, _provider = info
        expected = {role: materials.material_item(materials.APP_OWNER, path)["id"]
                    for role, path in run.inputs.items() if role != "driver"}
        self.assertTrue(expected)
        self.assertEqual(material_ids, expected)
```

- [ ] **Step 2: Run it.**

Run: `cd scripts && python3 -m unittest tests.test_batch_control_botruns.TestTryonSaveInfoMaterialIds -v`
Expected: PASS already — `APP_OWNER == "app"`, so the literal and the constant agree today. This test is a drift guard, not a red-first test. To prove it can fail, temporarily edit `bot.py:7259` to `f"ap/{path.name}"`, run again, confirm FAIL, then revert that edit before Step 3.

- [ ] **Step 3: Implement.** `bot.py:7259`:

```python
        material_ids = {role: f"{materials.APP_OWNER}/{path.name}" for role, path in run.inputs.items()
                        if role != "driver"}
```

Wrap to match the file's line length if needed. Do not call `material_item` here.

- [ ] **Step 4: Run the full gate.**

Run: `cd /Users/thucpham/Desktop/motion-clone && make batch-test 2>&1 | tail -5`
Expected: `OK`. `test_batch_control_invariants.py` must be among the passing modules (no `start_drain` call site moved).

- [ ] **Step 5: Commit.**

```bash
cd /Users/thucpham/Desktop/motion-clone
motions-studio/setup/scrub-secrets.sh --check
git add scripts/tgbot/bot.py scripts/tests/test_batch_control_botruns.py
git commit -m "refactor(bot): saved try-on material ids use materials.APP_OWNER"
```

(With the `Co-Authored-By` trailer.)

---

### Task 3: App — don't treat `seed_not_found` as a stale material

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/API/APIError.swift` (`userMessage`, ~`:32-35`)
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/DraftStore.swift` (`apply` doc comment `:99-110`, `mutate` catch `:196-204`)
- Test: `ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift` (`userMessages()`, `:295`)
- Test: `ios/MotionKit/Tests/MotionKitTests/DraftStoreTests.swift` (new tests near `missingMaterialRefreshesTheDraftAndKeepsTheServerMessage`, `:313`)
- Modify: `docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md:57-58`

**Interfaces:**
- Consumes: server code string `seed_not_found` at status 404 (Task 1).
- Produces: `APIError.server(status: 404, code: "seed_not_found", message: _).userMessage == "That saved try-on no longer exists."`.

- [ ] **Step 1: Write the failing tests.** In `APIClientTests.userMessages()`, append:

```swift
        // A deleted try-on library seed has its own code and its own sentence;
        // every other 404 keeps the material one.
        #expect(APIError.server(status: 404, code: "seed_not_found",
                                message: "no such try-on library entry: s1").userMessage
                == "That saved try-on no longer exists.")
        #expect(APIError.server(status: 404, code: "not_found", message: "x").userMessage
                == "Not found — it may have been removed from Telegram.")
```

In `DraftStoreTests`, after `missingMaterialRefreshesTheDraftAndKeepsTheServerMessage`, add both halves — the second is the regression guard (without it, deleting the 404 branch entirely would pass):

```swift
    @Test func deletedSeedDoesNotRefreshMaterialsOrRereadTheDraft() async {
        StubURLProtocol.install { request in
            request.url?.path == "/v1/pipelines"
                ? TestSupport.json(Fixtures.pipelines)
                : TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()
        StubURLProtocol.install { request in
            if request.httpMethod == "PATCH" {
                return TestSupport.json(
                    #"{"error":{"code":"seed_not_found","message":"no such try-on library entry: s1"}}"#,
                    status: 404)
            }
            return TestSupport.json(draftResponse(generation: 6))
        }

        let ok = await store.apply(DraftPatch(slots: ["outfit": "app/o.png"], seed: .set("s1")))

        #expect(!ok)
        #expect(!store.needsMaterialsRefresh)
        // The server resolves the seed above its lock and writes nothing, so
        // there is no ambiguity to re-read: the PATCH is the only request.
        #expect(StubURLProtocol.requests.map(\.httpMethod) == ["PATCH"])
        #expect(store.draft?.generation == 4)
        #expect(store.message == "That saved try-on no longer exists.")
    }

    @Test func goneMaterialOnTheSameSeedPatchStillRefreshesMaterials() async {
        StubURLProtocol.install { request in
            request.url?.path == "/v1/pipelines"
                ? TestSupport.json(Fixtures.pipelines)
                : TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()
        StubURLProtocol.install { request in
            if request.httpMethod == "PATCH" {
                return TestSupport.json(
                    #"{"error":{"code":"not_found","message":"no such material: app/o.png"}}"#,
                    status: 404)
            }
            return TestSupport.json(draftResponse(generation: 6))
        }

        let ok = await store.apply(DraftPatch(slots: ["outfit": "app/o.png"], seed: .set("s1")))

        #expect(!ok)
        #expect(store.needsMaterialsRefresh)
        #expect(StubURLProtocol.requests.map(\.httpMethod) == ["PATCH", "GET"])
        #expect(store.draft?.generation == 6)
        #expect(store.message == "no such material: app/o.png")
    }
```

Note: `StubURLProtocol.install` resets `requests` (`StubURLProtocol.swift:17-18`), which is why the second `install` makes the method-list assertions count only the mutation's requests.

- [ ] **Step 2: Run and watch them fail.**

Run: `cd ios/MotionKit && swift test 2>&1 | tail -30`
Expected: `userMessages` fails on the seed line; `deletedSeedDoesNotRefreshMaterialsOrRereadTheDraft` fails (flag set, GET issued, server message shown). `goneMaterialOnTheSameSeedPatchStillRefreshesMaterials` passes (guard).

- [ ] **Step 3: Implement `APIError`.** In `userMessage`'s status switch, directly above `case 404:`:

```swift
            case 404 where code == "seed_not_found":
                return "That saved try-on no longer exists."
```

- [ ] **Step 4: Implement `DraftStore.mutate`.** Replace the 404 match so it excludes the seed code:

```swift
            if materialAssignment,
               case let .server(status: 404, code: code, message: serverMessage) = api,
               code != "seed_not_found" {
```

The body of that `if` is unchanged. A `seed_not_found` falls through to the generic path: not offline, so no re-read; `message = api.userMessage`.

- [ ] **Step 5: Shrink `apply`'s doc comment.** Replace lines `:99-110` so the collision caveat is gone and the rest stays:

```swift
    /// Slots and the try-on seed in one PATCH (Phase 6: cross build, "Use in job").
    /// The 95 s timeout is unconditional, so a seed-only patch that probes nothing pays it too.
    /// A 404 trips `needsMaterialsRefresh` only on a patch that carries slots —
    /// `mutate` sets the flag solely under `materialAssignment`, which this passes as
    /// `!patch.slots.isEmpty` — and means a material this patch named is gone. A deleted seed
    /// answers `seed_not_found` instead and trips nothing: the server resolves it before writing
    /// anything (`scripts/control/drafts.py`, `patch`). The one shipped seed-only patch is
    /// `NewJobView`'s `.clear`, and it cannot 404 on a seed at all: the server resolves a library
    /// entry only for a truthy id.
```

- [ ] **Step 6: Run tests and build.**

Run: `cd ios/MotionKit && swift test 2>&1 | tail -5` → Expected: all tests pass.
Run: `cd /Users/thucpham/Desktop/motion-clone && make ios-build 2>&1 | tail -5` → Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 7: Point the phase-6 spec at this one.** In `docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md`, keep the "Out of scope…" paragraph (`:57-61`) verbatim and add directly after it:

```markdown
*Amended 2026-09-25:* the `not_found` collision and the `app/` owner coupling were both closed on
their own branch — see `2026-09-25-seed-not-found-and-owner-coupling-design.md`. The paragraph
above stays because it records why this branch could not take them.
```

- [ ] **Step 8: Commit.**

```bash
cd /Users/thucpham/Desktop/motion-clone
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md
git commit -m "feat(ios): a deleted try-on seed gets its own copy and no materials reload"
```

(With the `Co-Authored-By` trailer.)

---

### Task 4 (controller): gates, gate record, PR, deploy

Not a subagent task — it needs the VPS and a merge decision from the user.

- [ ] Run, in order: `make batch-test` → `cd ios/MotionKit && swift test` → `make ios-build` → `motions-studio/setup/scrub-secrets.sh --check` → `make ios-contract` (live, pre-deploy, GET only; must be 14/14).
- [ ] Append one row per gate to the spec's Gate record table (result, 2026-09-25, who ran it). For `ios-contract`, say it does not assert on error codes, so it proves no regression, not the new code.
- [ ] Commit the gate record; push branch; open PR (ask the user first — merging `scripts/**` auto-deploys `motion-bot`).
- [ ] Before merge: VPS pre-merge check (`batch/*.state.json`, `.env`'s `GPU_INSTANCE_ID`, `pgrep -af 'drain.py|batch_run.py'` via `doctl compute ssh motion-vps`, kill nothing), then merge, watch `deploy-bot`, live `make ios-contract` post-deploy, record rows.
- [ ] After merge: `docs/superpowers/swiftui-app-progress.md` "Next work" item 4 drops the `app/` owner coupling and the `not_found` collision.
