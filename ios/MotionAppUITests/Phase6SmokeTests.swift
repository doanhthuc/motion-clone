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
    /// Clear, then prove it worked. `Phase4Draft.clear` checks both halves:
    /// "0 jobs" alone is not enough, because the server counts the edited job
    /// only once it is complete (`scripts/control/drafts.py` `jobs_for`), so a
    /// Clear that silently no-op'd on the skip path would still read "0 jobs"
    /// with Character assigned, and surface one run later as a permanent skip.
    /// The cards reading "Missing required" is the other half.
    @MainActor
    private func clearFromBatch(_ app: XCUIApplication) {
        Phase4Draft.clear(in: app)
    }

    @MainActor
    func testCrossBuildDropAndLibrary() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.staticTexts["New Job"].waitForExistence(timeout: 15))

        // Precondition: an empty draft, not merely an empty basket — see
        // `clearFromBatch` for why "0 jobs" alone would let the smoke overwrite
        // a draft with a slot already assigned.
        guard app.staticTexts["0 jobs"].waitForExistence(timeout: 10),
              Phase4Draft.waitUntil(timeout: 10, condition: { Phase4Draft.isEmptyDraft(app) }) else {
            // Covers a genuinely non-empty draft (never overwritten here), a
            // draft or catalog load failure, and a server whose default pipeline
            // has none of Character, Driver, Outfit as a required card.
            throw XCTSkip("New Job never rendered an empty draft: expected \"0 jobs\" and every "
                + "required card reading \"Missing required\".")
        }

        Phase4Draft.selectTryonPipeline(in: app)
        Phase4Draft.chooseMaterial(for: "Character", in: app)
        Phase4Draft.chooseMaterial(for: "Driver", in: app)

        Phase4Draft.revealButton("batch.pickOutfits", in: app).tap()
        let picks = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "outfit.pick."))
        guard Phase4Draft.waitUntil(timeout: 15, condition: { picks.count >= 2 }) else {
            // Character and Driver were assigned above, so clean up.
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
        app.buttons["newjob.basket"].tap()     // the basket is a drawer since 2026-09-26
        let firstDrop = app.buttons["Drop"].firstMatch
        XCTAssertTrue(firstDrop.waitForExistence(timeout: 5), "a basket Drop button must exist")
        XCTAssertTrue(firstDrop.isHittable, "the basket Drop button must be visible")
        firstDrop.tap()
        XCTAssertTrue(app.sheets["Drop this batch entry?"].waitForExistence(timeout: 5))
        app.sheets["Drop this batch entry?"].buttons["Drop"].tap()
        XCTAssertTrue(Phase4Draft.revealText("Batch · 1", in: app, timeout: 15))

        // Leave the live draft empty, as the Phase 3-5 smokes do. The drop's
        // DELETE and trailing refresh can still hold `store.isBusy` true, which
        // `tapClear` waits out.
        clearFromBatch(app)

        app.tabBars.buttons["Materials"].tap()
        // Phase 6 nested MaterialsView inside MaterialTabView, one level deeper
        // under the NavigationStack, so the import control ("Add material" —
        // since 2026-09-25 `MaterialsView.addButton`, a full-width bar under
        // the Materials | Saved try-ons switch, not a toolbar +) must survive the wrapper. Assert it on the
        // Materials segment only: on Saved try-ons it legitimately disappears,
        // because MaterialsView leaves the hierarchy. This is the first smoke to
        // look at the Material tab, and a regression in a shipped screen is
        // worse than a gap in a new one.
        let addMaterial = app.descendants(matching: .any).matching(
            NSPredicate(format: "label == %@ OR identifier == %@", "Add material", "Add material")
        ).firstMatch
        XCTAssertTrue(addMaterial.waitForExistence(timeout: 10),
                      "the Materials import control survived its MaterialTabView wrapper")

        // The switch sits in the navigation bar in place of a title, so tap
        // the segment by its control and assert the switch by what left the
        // hierarchy: MaterialsView's import control goes with it.
        let savedSegment = app.segmentedControls["material.mode"].buttons["Saved try-ons"]
        savedSegment.tap()
        XCTAssertTrue(savedSegment.isSelected)
        XCTAssertTrue(addMaterial.waitForNonExistence(timeout: 15))

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

        // Same precondition as `testCrossBuildDropAndLibrary`.
        guard app.staticTexts["0 jobs"].waitForExistence(timeout: 10),
              Phase4Draft.waitUntil(timeout: 10, condition: { Phase4Draft.isEmptyDraft(app) }) else {
            // Covers a genuinely non-empty draft (never overwritten here), a
            // draft or catalog load failure, and a server whose default pipeline
            // has none of Character, Driver, Outfit as a required card.
            throw XCTSkip("New Job never rendered an empty draft: expected \"0 jobs\" and every "
                + "required card reading \"Missing required\".")
        }

        Phase4Draft.selectTryonPipeline(in: app)
        Phase4Draft.chooseMaterial(for: "Character", in: app)
        // Driver is filled by the Driver card's multi-select below, the only
        // way a try-on pipeline takes drivers since 2026-09-26.

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
