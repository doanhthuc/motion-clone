# A distinct `seed_not_found` code, and one owner constant

Date: 2026-09-25 · Status: merged 2026-09-25 as PR #69 (`e826935`) and deployed

Two items the Phase 6 follow-ups branch recorded as open and deliberately did not
take, because that branch allowed itself exactly one `scripts/**` change and it had
to be the resume stamp. Both are small. Neither is a money defect. This spec says so
plainly rather than inflating them, because the honest value of each is different
from what the handoff's one-line descriptions imply.

### Gate record

Append-only: add a row when a gate runs, and do not restate its result in prose
elsewhere in this file.

| Gate | Result | Date | Ran by |
|---|---|---|---|
| `make batch-test` | 2150 tests OK (skipped=1) at `7c97e50` | 2026-09-25 | Claude (controller) |
| `cd ios/MotionKit && swift test` | 254 tests in 23 suites passed at `7c97e50` | 2026-09-25 | Claude (controller) |
| `make ios-build` | exit 0 at `7c97e50`; no tracked files changed by `xcodegen` | 2026-09-25 | Claude (controller) |
| `motions-studio/setup/scrub-secrets.sh --check` | exit 0 at `7c97e50` | 2026-09-25 | Claude (controller) |
| `make ios-contract` (live, pre-deploy) | 14/14 against the pre-merge server. The tool does not assert on error codes, so this proves no regression in the wire shapes, not the new `seed_not_found` code | 2026-09-25 | Claude (controller) |
| VPS pre-merge check (drain / Phase A / lease / migration) | clean: no `GPU_INSTANCE_ID`, no `drain.py`/`batch_run.py`, journals untouched since 09-24; the 09-24 handoff's failed `gpu-destroy` cross-checked on RunPod — zero pods | 2026-09-25 | Claude (controller) |
| `deploy-bot` workflow run | success, run 36041022957 (15 s); VPS at `e826935`, `motion-bot` active | 2026-09-25 | Claude (controller) |
| `make ios-contract` (live, post-deploy) | 14/14 against `e826935`; same caveat as the pre-deploy row — error codes are not asserted | 2026-09-25 | Claude (controller) |

`make ios-ui-test` is **not** in this list on purpose. Neither change is reachable
from the zero-spend smoke: it never deletes a library entry that a draft still
references, and it never reads a saved try-on's `material_ids`. Claiming a UI gate
for them would be a claim nothing checks.

## 1. Scope and intent

**Item 1 — the `not_found` collision.** `scripts/control/drafts.py` raises
`DraftError("not_found", …)` for two different causes, so both reach the phone as
`404 not_found` with no way to discriminate:

- a material that is gone — `_resolve`, and the under-lock re-check in `patch`;
- a saved try-on whose library entry was deleted since it was seeded — `patch`'s
  seed resolution.

Because the app cannot tell them apart, `DraftStore.apply` trips
`needsMaterialsRefresh` on a deleted *seed*, which reloads the whole materials list
and runs `closePickerIfSelectionDisappeared()`. And `APIError.userMessage`'s 404
copy — "Not found — it may have been removed from Telegram" — is shown for a saved
try-on, which has nothing to do with Telegram. Phase 6 did not fix this; it
documented it, and `DraftStore.swift`'s doc comment on `apply` now carries a
five-line caveat telling readers to interpret the flag as "something this patch
named is gone" rather than as evidence about materials. **The point of this change
is that the caveat becomes unnecessary.**

Success looks like: a deleted seed produces its own code, its own accurate sentence,
and no materials reload; a deleted material behaves exactly as it does today; and
that doc comment shrinks to describing what the flag actually means.

**Item 2 — the `app/` owner coupling.** `scripts/tgbot/bot.py` builds a saved
try-on's `material_ids` with the literal `f"app/{path.name}"`, while
`scripts/control/materials.py` builds every material id as `f"{owner}/{path.name}"`
and `_material_id` reassembles one with `"/".join(rel.parts)`. All three agree today
only because `APP_OWNER = "app"`. Success looks like: one constant, and a test that
fails if the two paths ever disagree.

Out of scope: the stamp write's fail-open race (recorded in the follow-ups spec §10,
closable, deliberately not closed); `MigrateSheet`'s missing `dropBlocked` reason;
`RunDetailView`'s redundant inner `if`; the Swift post-confirm fixture's missing
cross-language pin; and any live spend. **Nothing here rents a pod or calls a
try-on provider.**

## 2. What the code does today (read 2026-09-25, not recalled)

Every fact below was read in full this session. Two of them contradict what the
handoff's one-line descriptions imply, and one constrains the fix.

- **`patch` resolves the seed before it takes the lock, and before anything is
  written.** `drafts.py:385-389`: `tryon_seed` is read, type-checked, and
  `resolve_image`d; `:389` raises `DraftError("not_found", f"no such try-on library
  entry: {tryon_seed}")` when it returns `None`. That is above the
  `with control.LOCK:` block entirely. The under-lock section ends with its own
  comment at `:446` — *"Every check passed: apply. Nothing above wrote anything."*
  **So a `seed_not_found` refusal leaves the draft definitively unchanged**, which is
  what makes skipping the app's ambiguity re-read correct rather than optimistic.
- **Exactly one raise site changes.** `DraftError("not_found", …)` appears in
  `drafts.py` at five places: `_resolve`'s malformed-id guard (`:359`) and its
  vanished-file case (`:363`), the seed (`:389`), `patch`'s under-lock re-check of
  every resolved path (`:445`), and `drop_from_batch`'s missing digest (`:481`). Only
  `:389` is about a library entry. Enumerated by grep over the file, not sampled.
- **The HTTP layer maps codes to statuses through a table, with 400 as the default.**
  `scripts/httpapi/server.py:103-105` catches `DraftError` among the domain errors and
  calls `self._error(_DOMAIN_STATUS.get(exc.code, 400), exc.code, exc.message)`.
  `_DOMAIN_STATUS` (`:38`) carries `"not_found": 404`. **A new code that is not added
  to that table silently becomes a 400**, which would change the status the phone
  sees and is not what this design wants.
- **The app matches on status and deliberately ignores the code.**
  `DraftStore.mutate`'s catch is
  `if materialAssignment, case let .server(status: 404, code: _, message: serverMessage) = api`
  → `needsMaterialsRefresh = true`, `await refreshAfterAmbiguousWrite()`, then
  `self.error = api; message = serverMessage`. The `code: _` is why **a server-only
  change would alter nothing observable**: the app would keep tripping the flag. This
  is the fact the handoff's "the real fix is a distinct server code" glosses over.
- **`materialAssignment` is `!patch.slots.isEmpty`** for `apply(_:)`, and `true`
  unconditionally for `assign(role:materialID:)`. So the flag only trips on a patch
  that names at least one slot — which, as `DraftStore.swift`'s own doc comment
  records, covers every seed the app actually *sets*, because
  `TryonLibraryStore.use` and `BatchComposer.run` both send `tryon_seed` together
  with `slots.outfit`. The one shipped seed-only patch is `NewJobView`'s `.clear`,
  and it cannot 404 on a seed at all: `drafts.py:386-389` only resolves a library
  entry for a truthy id.
- **What the flag actually costs.** `NewJobView.swift:31-37`'s `.onChange` runs
  `materials.refresh()`, `store.acknowledgeMaterialsRefresh()` and
  `closePickerIfSelectionDisappeared()`. So a spurious trip is a full materials
  reload over the network plus a possible picker dismissal — not corruption, and the
  server's own accurate sentence still reaches `message`. Modest. Recorded so the
  fix is not oversold.
- **`material_item` stats the file and raises if it is gone.**
  `materials.py:218-225`: `st = path.stat()` then
  `{"id": f"{owner}/{path.name}", …}`, with a docstring saying it raises
  `FileNotFoundError` deliberately so a caller that just produced the file can
  distinguish "gone" from "never existed". `bot.py:7259` builds ids for a **past**
  run's inputs and never stats — those files may legitimately be gone.
  **So the coupling must not be fixed by calling `material_item`**; that would turn a
  record of history into a call that can throw. The fix is to source the owner prefix
  from the constant and nothing more.
- `materials` is already imported in `bot.py:55`
  (`from control import BOT_LOCK, BOT_LOCK_TIMEOUT_SEC, materials, uploads`), and
  `APP_OWNER = "app"` is at `materials.py:199`. No new import, and because
  `APP_OWNER == "app"` **no stored id changes meaning** — there is no data migration
  in this item, which is what the design conversation assumed there might be.

## 3. Item 1 — `seed_not_found`

### Approaches rejected

- **Keep `not_found` and have the app discriminate on the message text.** Rejected:
  matching human-readable copy across a language boundary is precisely the fragility
  the follow-ups branch removed in `APIError.userMessage`, and it would break the
  first time someone improves the server's sentence.
- **Keep `not_found` and stop tripping `needsMaterialsRefresh` whenever the patch
  carried a `tryon_seed`.** Rejected, and this one is a real regression rather than a
  trade-off: the cross build sends the seed *together with* `slots.outfit`, so a
  genuinely stale material in that same patch would stop being reported. The flag
  exists for that case.
- **Add the code but let it fall through to 400.** Rejected. It is the cheapest
  option — `_DOMAIN_STATUS.get(code, 400)` gives it for free and the app's
  status-404 match would stop firing with no app change at all, so no app deploy. But
  400 says the request was malformed, and the request was fine; the referenced entity
  vanished. Worse, 400 is the *default* for an unmapped code, so `seed_not_found` at
  400 would be indistinguishable from someone forgetting the table entry. Recorded
  here because it is a legitimate cheap option and the reasoning for declining it
  should outlive this decision.

### Chosen

**Server.** `drafts.py:389` becomes `DraftError("seed_not_found", f"no such try-on
library entry: {tryon_seed}")`. `_DOMAIN_STATUS` in `scripts/httpapi/server.py:38`
gains `"seed_not_found": 404`, so the status is unchanged and only the code
discriminates. Nothing else in `drafts.py` moves — in particular the three material
sites and `drop_from_batch` keep `not_found`.

`runs.py`'s `OUTCOME_STATUS` does **not** gain an entry. That table is for the bot's
`Outcome` codes, a separate vocabulary from `DraftError`; adding it there would be
harmless but wrong, and the kind of copy-paste that makes two tables look like one
rule.

**App.** Two changes, both needed:

- `DraftStore.mutate`'s 404 branch stops matching `code: _` and excludes
  `seed_not_found`, so a deleted seed neither trips `needsMaterialsRefresh` nor runs
  `refreshAfterAmbiguousWrite()`. Skipping the re-read is justified by §2's first
  fact, not assumed: the seed is resolved above the lock and nothing above it writes.
- `APIError.userMessage` gains a `case 404 where code == "seed_not_found"` returning
  copy that names a saved try-on, ahead of the existing bare `case 404`. Proposed:
  `"That saved try-on no longer exists."` The bare-404 sentence stays for materials.

`DraftStore.apply`'s doc comment then loses the caveat paragraph that exists only to
explain the collision, and says what the flag means now: a material this patch named
is gone. The comment's other content — that `materialAssignment` is
`!patch.slots.isEmpty`, and why the seed-only `.clear` patch cannot 404 on a seed —
stays, because both are still true and neither is obvious.

**Both deploy orders are safe**, and the reasoning is worth stating because it is not
symmetric:

- *Old app, new server:* the app matches on status 404 and ignores the code, so it
  keeps tripping the flag exactly as today. Bug persists for that build; nothing
  regresses.
- *New app, old server:* the server still sends `not_found`, so the app's
  `code != "seed_not_found"` exclusion does not fire and it trips the flag exactly as
  today. Same behaviour, no crash, no unknown-code path — `APIError`'s 404 case falls
  through to the material sentence, which is what an old server means.

## 4. Item 2 — one owner constant

`bot.py:7259` becomes `f"{materials.APP_OWNER}/{path.name}"`. That is the whole
change. It must **not** become a `material_item(...)` call (§2's sixth fact: that
stats, and these files may be gone).

The test has to pin the *coupling*, not the value, and the obvious shape is a trap:
patching `APP_OWNER` to something else and asserting both sides moved would pass
vacuously, because both sides read the same constant. The test that actually pins it
compares two independent code paths against each other — build a run whose inputs are
real files in a temp staging tree, take `tryon_save_info`'s `material_ids`, and assert
each equals the `id` that `materials.material_item(materials.APP_OWNER, <same path>)`
produces. Two implementations, one expected value; if either drifts, the test fails.
The files must exist for `material_item`, which is fine in a unit test and is exactly
why the production call site cannot use it.

## 5. Compatibility, deploy and blast radius

Both items touch `scripts/**`, so merging auto-deploys `motion-bot` and restarts the
phone API with it. One branch, one PR, one VPS pre-merge check for a live drain,
Phase A, pod lease and migration.

`make ios-contract` must stay **14/14**. No route changes, no response field changes;
the only wire difference is the `code` string inside one 404 error body, which the
contract tool does not assert on. Say so in the gate row rather than implying the
contract proves the new code.

Nothing in `AppRuns` or `AppPod` reads `DraftError` codes, so the resume stamp and
its gate are untouched. `test_batch_control_invariants.py` must stay green: no
`start_drain` call site moves.

## 6. Testing and acceptance

**Server (`scripts/tests/`, `make batch-test`).** In `test_batch_control_drafts.py`:
the two existing seed assertions (`:385`, `:388`) change from `not_found` to
`seed_not_found`; the material assertions (`:260-262`, `:304`) and `drop_from_batch`'s
(`:467`) must **not** change, and a test should assert they still say `not_found` so
the narrowing is pinned from both sides. Add: a `seed_not_found` refusal leaves the
draft byte-identical (the §2 fact, asserted rather than trusted); and `_DOMAIN_STATUS`
maps it to 404 — because the default is 400 and a missing table entry would silently
change the status. `test_batch_control_http.py` has **no** `tryon_seed` case at all —
grep returns zero hits, checked 2026-09-25 — so the new code needs one there if the
HTTP layer's mapping is to be covered end to end rather than only at the store. Its
`:762` case is a material, not a seed, and must stay `not_found`.

**App (`ios/MotionKit`, `swift test`).** `DraftStoreTests`: a 404 `seed_not_found` on
a slot-carrying patch does **not** set `needsMaterialsRefresh`, does not re-read the
draft, and puts the seed sentence in `message`; a 404 `not_found` on the same patch
still does all three. The second half is the regression guard — without it, the fix
could be implemented by deleting the branch entirely and every test would pass.
`APIClientTests.userMessages()`: the new 404 case, and that a bare 404 still returns
the material sentence.

**Gates, in order.** `make batch-test` → `swift test` → `make ios-build` →
`scrub-secrets.sh --check` → VPS pre-merge check → merge → deploy → live
`make ios-contract`.

`make ios-build` is required and is not redundant with `swift test`: `swift test`
never compiles `MotionApp`, and this change does not touch `MotionApp` — but the gate
is cheap and it is the only proof the app target still builds against a changed
MotionKit.

## 7. Docs to amend

- `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` — the error
  vocabulary, in place, as each slice has been. One new code, one new table entry, and
  the note that an unmapped code becomes 400.
- `docs/superpowers/specs/2026-09-24-phase-6-follow-ups-design.md` §3 — its "Known
  collision this does not fix" paragraph becomes a pointer to this spec rather than a
  description of an open defect. Do not delete the paragraph: it records why that
  branch could not take this, which is the reason the fix landed separately.
- `docs/superpowers/swiftui-app-progress.md` — the `app/` owner coupling and the
  `not_found` collision come off the open list in "Next work" item 4. Reserved for
  after the merge, as always.
- This file's gate record.

## 8. Recorded limits

- The fix changes what a **deleted seed** reports. It does not make seed resolution
  transactional against library deletion: a seed can still be deleted between the
  app reading the library and sending the patch, and the user still sees a refusal
  rather than a silent wrong image. That is correct behaviour; only its label
  changes.
- An old app build keeps the spurious materials reload until it is rebuilt. There is
  one user and one build, so this is a non-issue in practice and is recorded only
  because "both deploy orders are safe" should not read as "both orders are
  identical".
- Item 2 removes the duplicated literal, not the concept. `owner` is still a string
  prefix convention shared by three code paths; making it a type would be a larger
  change with no current payoff, since `APP_OWNER` is the only non-Telegram owner and
  `materials.py:294` already refuses a cross-owner read.
- Neither change is covered by a live gate. Both are unit-tested on both sides, and
  §6 says which assertion is the regression guard for each.
