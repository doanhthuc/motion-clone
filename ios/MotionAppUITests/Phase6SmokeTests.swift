import XCTest

/// Zero-spend, live server: only reads and free draft mutations. Builds a
/// two-outfit cross build, drops one basket entry, opens Saved try-ons, and
/// clears the draft. Never taps Preview try-on, Rent, Confirm, Kill, Migrate or
/// a GPU row, and skips rather than proceeding when its preconditions are
/// missing — a non-empty draft, or fewer than two image materials to use as
/// outfits. Launched with -UITestRecordingSpendGate, so even a stray tap on a
/// spend button is recorded on the phone and never sent; the recorded counter
/// is asserted zero at the end.
final class Phase6SmokeTests: XCTestCase {
    /// Batch mode has had its own Clear since 2026-09-24, but
    /// `Phase4Draft.clear(in:)` still cannot be reused from here: its
    /// post-condition is the readiness line ("0 of 3 required slots assigned"),
    /// which is Single-only and stays Single-only. Both post-conditions below
    /// read elements the Batch arm renders — a shared slot row
    /// (`BatchComposerSection.sharedRoles`) and the header's job count
    /// (`NewJobView.header`) — but only the job count is unconditional. The
    /// slot row is drawn only in the `else` of `if !BatchComposer.supports(pipeline)`,
    /// so under a catalog that pairs no character with an outfit the row is
    /// absent for a configuration reason and Clear still worked; `supports` is
    /// `required ∪ optional` containing both roles, and the default pipeline
    /// comes from `TG_PIPELINE`.
    @MainActor
    private func clearFromBatch(_ app: XCUIApplication) {
        let clear = Phase4Draft.revealButton("Clear", in: app)
        // `revealButton` only waits for `isHittable`. Clear is
        // `.disabled(store.isBusy || composer.isRunning)`, and a tap on a
        // disabled SwiftUI control is a silent no-op — so wait for `isEnabled`
        // separately, or the draft stays full and the assertions below are the
        // only thing that notices.
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 10) { clear.isEnabled },
                      "Clear must re-enable before it is tapped")
        clear.tap()
        // The load-bearing half. Do not delete it as redundant just because
        // "0 jobs" below already passes: `_jobs` counts the basket plus the
        // edited job only once that job is *complete*
        // (`scripts/control/drafts.py:294-307`, `:337`), so on the
        // fewer-than-two-outfits skip path the header read "0 jobs" *before* the
        // tap too — nothing basketed, and Outfit never assigned. A Clear that
        // silently no-op'd would leave Character and Driver assigned and still
        // read "0 jobs" here, and the damage would surface one run later as a
        // permanent silent skip (that path's `XCTSkip`), not as a failure.
        // "Missing required" is `SlotRow.stateText` for an empty required role —
        // the exact inverse of `Phase4Draft.chooseMaterial`'s post-condition.
        // Not `readiness`'s "Missing required materials", which is a different
        // element's `accessibilityValue` and Single-only besides.
        _ = Phase4Draft.revealButton("Character", in: app)
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 10) {
            (app.buttons["Character"].value as? String) == "Missing required"
        }, "Batch mode's Clear must unassign the shared slots, not just empty the basket"
            + " — if the row is absent instead, the server's default pipeline is not"
            + " batch-supported (check TG_PIPELINE)")
        XCTAssertTrue(app.staticTexts["0 jobs"].waitForExistence(timeout: 10),
                      "Batch mode's Clear emptied the draft")
    }

    @MainActor
    func testCrossBuildDropAndLibrary() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.staticTexts["New Job"].waitForExistence(timeout: 15))

        // Precondition: an empty draft, not merely an empty basket. `draft.jobs`
        // is the server's `len(jobs)` (`scripts/control/drafts.py:337`), and `_jobs`
        // (`:294-307`) counts the basket plus the edited job only once it is
        // *complete* — so "0 jobs" alone would let the smoke proceed on a draft
        // with a slot already assigned, then overwrite and clear it. Requiring the
        // readiness line too (`NewJobView.readiness`, rendered in Single mode)
        // proves no required slot is filled. Together: nothing basketed, no
        // required slot assigned. A real draft is never overwritten and, on this
        // path, never cleared. Either pipeline's empty count is accepted (tryon = 3
        // required, motion-enhance = 2), as `Phase4Draft.clear(in:)` does.
        guard app.staticTexts["0 jobs"].waitForExistence(timeout: 10),
              Phase4Draft.revealText("0 of 3 required slots assigned", in: app)
                  || Phase4Draft.revealText("0 of 2 required slots assigned", in: app, timeout: 1) else {
            // This one reason covers three different causes, so name the
            // literals: (a) a genuinely non-empty draft, (b) a draft or catalog
            // load failure — `initialLoadFailure` (`NewJobView.swift:71-80`)
            // replaces the editor, so neither line ever renders, and a network
            // blip would otherwise send whoever reads this hunting for a
            // phantom draft — and (c) a pipeline whose required-role count is
            // neither 3 nor 2.
            throw XCTSkip("New Job never rendered an empty draft: expected \"0 jobs\" plus "
                + "\"0 of 3 required slots assigned\" or \"0 of 2 required slots assigned\". "
                + "Either the draft is not empty (this smoke never overwrites a real one), "
                + "or the draft/catalog load failed, or the pipeline requires a different "
                + "number of roles.")
        }

        // The guard above may have scrolled down to the readiness footer; the
        // mode control is New Job's first row, so scroll back until it is built.
        let mode = app.segmentedControls["newjob.mode"]
        for _ in 0..<6 where !mode.exists { app.swipeDown() }
        mode.buttons["Batch"].tap()
        Phase4Draft.selectTryonPipeline(in: app)
        Phase4Draft.chooseMaterial(for: "Character", in: app)
        Phase4Draft.chooseMaterial(for: "Driver", in: app)

        Phase4Draft.revealButton("batch.pickOutfits", in: app).tap()
        let picks = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "outfit.pick."))
        guard Phase4Draft.waitUntil(timeout: 15, condition: { picks.count >= 2 }) else {
            // Character and Driver were assigned above, so clean up. Batch mode
            // has its own Clear, so this no longer switches to Single first.
            app.buttons["Done"].tap()
            clearFromBatch(app)
            throw XCTSkip("Fewer than two image materials to use as outfits.")
        }
        picks.element(boundBy: 0).tap()
        picks.element(boundBy: 1).tap()
        app.buttons["Done"].tap()

        let run = Phase4Draft.revealButton("batch.run", in: app)
        // `canRun` needs the shared-slot PATCHes to have settled (`!isBusy`,
        // `!isRefreshing`), so wait for the button to enable rather than reading
        // it the instant the outfit sheet closes.
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 20) { run.isEnabled },
                      "cross build is ready once the shared slots are filled and two outfits are picked")
        run.tap()
        // Two sequential PATCH + add-to-batch round-trips, then the
        // outfit-clearing PATCH; a slow draft probe can make each one linger.
        XCTAssertTrue(Phase4Draft.revealText("Added 2 jobs to the batch.", in: app, timeout: 120))
        // The basket is a `List` section whose header is sentence case
        // (`RootView` sets `.textCase(nil)`), so it reads "Batch · 2". XCUITest's
        // subscript match is exact — the case matters.
        XCTAssertTrue(Phase4Draft.revealText("Batch · 2", in: app, timeout: 15))

        // Two basket entries means two "Drop" buttons, so scope to the first;
        // `app.buttons["Drop"]` would be an ambiguous query a tap cannot
        // resolve. The confirmation dialog is matched by its title, never
        // app-wide, because the run-flow card carries its own "Drop" too.
        let firstDrop = app.buttons["Drop"].firstMatch
        for _ in 0..<8 where !firstDrop.isHittable { app.swipeDown() }
        for _ in 0..<10 where !firstDrop.isHittable { app.swipeUp() }
        XCTAssertTrue(firstDrop.waitForExistence(timeout: 5), "a basket Drop button must exist")
        XCTAssertTrue(firstDrop.isHittable, "the basket Drop button must be visible")
        firstDrop.tap()
        XCTAssertTrue(app.sheets["Drop this batch entry?"].waitForExistence(timeout: 5))
        app.sheets["Drop this batch entry?"].buttons["Drop"].tap()
        XCTAssertTrue(Phase4Draft.revealText("Batch · 1", in: app, timeout: 15))

        // Leave the live draft empty, as the Phase 3-5 smokes do. Batch mode
        // clears directly now; the drop's DELETE and trailing refresh can still
        // hold `store.isBusy` true, which `clearFromBatch` waits out.
        clearFromBatch(app)

        app.tabBars.buttons["Materials"].tap()
        // Phase 6 nested MaterialsView inside MaterialTabView, one level deeper
        // under the NavigationStack. SwiftUI still propagates its `.toolbar`
        // item to the enclosing stack, so the import control ("Add material",
        // `MaterialsView.addMenu`) must survive the wrapper. Assert it on the
        // Materials segment only: on Saved try-ons it legitimately disappears,
        // because MaterialsView leaves the hierarchy. This is the first smoke to
        // look at the Material tab, and a regression in a shipped screen is
        // worse than a gap in a new one.
        let addMaterial = app.descendants(matching: .any).matching(
            NSPredicate(format: "label == %@ OR identifier == %@", "Add material", "Add material")
        ).firstMatch
        XCTAssertTrue(addMaterial.waitForExistence(timeout: 10),
                      "the Materials toolbar import control survived its MaterialTabView wrapper")

        // "Saved try-ons" is both this segment's label and, since the native
        // design pass, the navigation title (`MaterialTabView`), so tap the
        // segment by its control and assert the switch by the navigation bar.
        app.segmentedControls["material.mode"].buttons["Saved try-ons"].tap()
        XCTAssertTrue(app.navigationBars["Saved try-ons"].waitForExistence(timeout: 15))

        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0",
                       "the recording gate saw no spend")
    }

    /// Task 7 (2026-09-25 spec §4): drivers are now multi-selected too, so a
    /// cross build is outfits × drivers. Builds 2 outfits × 2 drivers from free
    /// draft mutations only — never Preview try-on, Rent, Confirm, Kill,
    /// Migrate or a GPU row — and asserts zero recorded spends at the end, same
    /// gate as `testCrossBuildDropAndLibrary`. Skips rather than proceeding
    /// when its preconditions are missing: a non-empty draft, fewer than two
    /// image materials to use as outfits, or fewer than two video materials to
    /// use as drivers.
    @MainActor
    func testMultiDriverCrossBuild() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.staticTexts["New Job"].waitForExistence(timeout: 15))

        // Same precondition as `testCrossBuildDropAndLibrary` — see its comment
        // for why both "0 jobs" and the readiness line are required together.
        guard app.staticTexts["0 jobs"].waitForExistence(timeout: 10),
              Phase4Draft.revealText("0 of 3 required slots assigned", in: app)
                  || Phase4Draft.revealText("0 of 2 required slots assigned", in: app, timeout: 1) else {
            throw XCTSkip("New Job never rendered an empty draft: expected \"0 jobs\" plus "
                + "\"0 of 3 required slots assigned\" or \"0 of 2 required slots assigned\".")
        }

        // The guard above may have scrolled down to the readiness footer; the
        // mode control is New Job's first row, so scroll back until it is built.
        let mode = app.segmentedControls["newjob.mode"]
        for _ in 0..<6 where !mode.exists { app.swipeDown() }
        mode.buttons["Batch"].tap()
        Phase4Draft.selectTryonPipeline(in: app)
        Phase4Draft.chooseMaterial(for: "Character", in: app)
        // Driver is deliberately left unfilled as a shared slot: picking at
        // least one driver below moves it into `BatchComposer.crossedRoles`,
        // which drops it out of `missingShared` (`BatchComposerSection.swift`,
        // `BatchComposer.swift:139-143`) — the multi-select is how a
        // multi-driver build fills that role now, not the shared row.

        Phase4Draft.revealButton("batch.pickDrivers", in: app).tap()
        let driverPicks = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "driver.pick."))
        guard Phase4Draft.waitUntil(timeout: 15, condition: { driverPicks.count >= 2 }) else {
            app.buttons["Done"].tap()
            clearFromBatch(app)
            throw XCTSkip("Fewer than two video materials to use as drivers.")
        }
        driverPicks.element(boundBy: 0).tap()
        driverPicks.element(boundBy: 1).tap()
        app.buttons["Done"].tap()

        Phase4Draft.revealButton("batch.pickOutfits", in: app).tap()
        let outfitPicks = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "outfit.pick."))
        guard Phase4Draft.waitUntil(timeout: 15, condition: { outfitPicks.count >= 2 }) else {
            app.buttons["Done"].tap()
            clearFromBatch(app)
            throw XCTSkip("Fewer than two image materials to use as outfits.")
        }
        outfitPicks.element(boundBy: 0).tap()
        outfitPicks.element(boundBy: 1).tap()
        app.buttons["Done"].tap()

        let summary = app.staticTexts["batch.summary"]
        _ = Phase4Draft.revealText("batch.summary", in: app, timeout: 2)
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 15) {
            summary.exists && summary.label.contains("2 outfits × 2 drivers = 4 videos")
        }, "expected \"2 outfits × 2 drivers = 4 videos\" in the summary, got: \(summary.label)")

        let run = Phase4Draft.revealButton("batch.run", in: app)
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 20) { run.isEnabled },
                      "cross build is ready once Character is filled and 2 outfits × 2 drivers are picked")
        run.tap()
        // Four sequential PATCH + add-to-batch round-trips, then the
        // outfit-and-driver-clearing PATCH; a slow draft probe can make each
        // one linger.
        XCTAssertTrue(Phase4Draft.revealText("Added 4 jobs to the batch.", in: app, timeout: 180))
        XCTAssertTrue(app.staticTexts["4 jobs"].waitForExistence(timeout: 15))

        clearFromBatch(app)

        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0",
                       "the recording gate saw no spend")
    }
}
