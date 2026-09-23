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
    @MainActor
    func testCrossBuildDropAndLibrary() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.staticTexts["New Job"].waitForExistence(timeout: 15))

        // Precondition: an empty draft. The header's count is `draft.jobs`, so
        // "0 jobs" means nothing is basketed. A real draft is never overwritten
        // and, on this path, never cleared.
        guard app.staticTexts["0 jobs"].waitForExistence(timeout: 10) else {
            throw XCTSkip("The draft is not empty — this smoke never overwrites a real draft.")
        }

        app.segmentedControls["newjob.mode"].buttons["Batch"].tap()
        Phase4Draft.selectTryonPipeline(in: app)
        Phase4Draft.chooseMaterial(for: "Character", in: app)
        Phase4Draft.chooseMaterial(for: "Driver", in: app)

        Phase4Draft.revealButton("batch.pickOutfits", in: app).tap()
        let picks = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "outfit.pick."))
        guard Phase4Draft.waitUntil(timeout: 15, condition: { picks.count >= 2 }) else {
            // Character and Driver were assigned above, so clean up. Clear lives
            // in Single mode only — Batch renders neither the readiness line nor
            // `editorActions` (`NewJobView.editor`'s if/else) — so switch back
            // before clearing.
            app.buttons["Done"].tap()
            app.segmentedControls["newjob.mode"].buttons["Single"].tap()
            Phase4Draft.clear(in: app)
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
        XCTAssertTrue(app.staticTexts["Added 2 jobs to the batch."].waitForExistence(timeout: 120))
        // `SectionLabel` renders `Text(text.uppercased())` (`StatusViews.swift`),
        // so the basket header reads "BATCH · 2", not "Batch · 2". XCUITest's
        // subscript match is exact — do not "fix" the case back.
        XCTAssertTrue(app.staticTexts["BATCH · 2"].waitForExistence(timeout: 15))

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
        XCTAssertTrue(app.staticTexts["BATCH · 1"].waitForExistence(timeout: 15))

        // Leave the live draft empty, as the Phase 3–5 smokes do. Clear is in
        // Single mode only, so switch back before clearing.
        app.segmentedControls["newjob.mode"].buttons["Single"].tap()
        Phase4Draft.clear(in: app)

        app.tabBars.buttons["Material"].tap()
        // Task 7 nested MaterialsView inside MaterialTabView, one level deeper
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

        // "Saved try-ons" is both this segment's label and the screen title
        // (`SavedTryonsView`), so the static-text query matches twice; tap the
        // segment by its control and assert the text with `.firstMatch`.
        app.segmentedControls["material.mode"].buttons["Saved try-ons"].tap()
        XCTAssertTrue(app.staticTexts["Saved try-ons"].firstMatch.waitForExistence(timeout: 15))

        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0",
                       "the recording gate saw no spend")
    }
}
