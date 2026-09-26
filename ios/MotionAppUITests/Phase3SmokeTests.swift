import XCTest

final class Phase3SmokeTests: XCTestCase {
    @MainActor
    func testDraftCompositionBatchDropAndValidation() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestNoNotificationPrompt")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.staticTexts["New Job"].waitForExistence(timeout: 15))

        clearDraft(in: app)
        selectTryonPipeline(in: app)
        Phase4Draft.chooseMaterial(for: "Outfit", in: app)
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@", "DecodingError")).firstMatch.exists)
        XCTAssertNotEqual(app.buttons["Outfit"].value as? String, "Missing required")

        // A pipeline without an outfit drops the selection instead of hiding it.
        selectPipeline(named: "Motion Enhance", in: app)
        XCTAssertTrue(app.buttons["Outfit"].waitForNonExistence(timeout: 5))
        selectTryonPipeline(in: app)
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 10) {
            (app.buttons["Outfit"].value as? String) == "Missing required"
        })

        Phase4Draft.chooseMaterial(for: "Character", peekFirst: true, in: app, attach: { self.add($0) })
        Phase4Draft.chooseMaterial(for: "Driver", in: app)
        Phase4Draft.chooseMaterial(for: "Outfit", in: app)

        clearMaterial(for: "Character", in: app)
        XCTAssertFalse(app.buttons["newjob.continueToRun"].isEnabled)
        Phase4Draft.chooseMaterial(for: "Character", in: app)

        let add = app.buttons["batch.run"]
        XCTAssertTrue(add.waitForExistence(timeout: 10))
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 20) { add.isEnabled })
        add.tap()
        XCTAssertTrue(app.staticTexts["Batch · 1"].waitForExistence(timeout: 60))

        app.buttons["newjob.basket"].tap()
        let firstDrop = app.buttons["Drop"].firstMatch
        XCTAssertTrue(firstDrop.waitForExistence(timeout: 5))
        firstDrop.tap()
        XCTAssertTrue(app.sheets["Drop this batch entry?"].waitForExistence(timeout: 5))
        app.sheets["Drop this batch entry?"].buttons["Drop"].tap()
        XCTAssertTrue(app.staticTexts["Batch · 1"].waitForNonExistence(timeout: 10))

        // Continue adds the pending outfit itself, validates, and opens the run flow.
        Phase4Draft.chooseMaterial(for: "Outfit", in: app)
        let proceed = app.buttons["newjob.continueToRun"]
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 20) { proceed.isEnabled })
        proceed.tap()
        XCTAssertTrue(app.buttons["runflow.rentWithoutPreview"].waitForExistence(timeout: 90))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(app.staticTexts["Batch · 1"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Ready"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@ AND label CONTAINS[c] %@", "about", "min")
        ).firstMatch.waitForExistence(timeout: 5))

        // The Pod tab (Phase 5) is a tab-bar item, not a New Job action.
        for forbidden in ["Phase A", "Run", "Rent", "Pod"] {
            let named = NSPredicate(format: "label == %@ OR identifier == %@", forbidden, forbidden)
            let onScreen = app.buttons.matching(named).count
                - app.tabBars.buttons.matching(named).count
            XCTAssertEqual(onScreen, 0, "The New Job screen must not expose \(forbidden)")
        }

        clearDraft(in: app)
    }

    @MainActor
    private func clearDraft(in app: XCUIApplication) {
        Phase4Draft.clear(in: app)
    }

    @MainActor
    private func selectTryonPipeline(in app: XCUIApplication) {
        Phase4Draft.selectTryonPipeline(in: app)
    }

    @MainActor
    private func selectPipeline(named name: String, in app: XCUIApplication) {
        let chip = app.buttons["Pipeline"]
        XCTAssertTrue(chip.waitForExistence(timeout: 10))
        chip.tap()
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons[name].waitForExistence(timeout: 5))
        app.buttons[name].tap()
        XCTAssertTrue(waitUntil(timeout: 10) {
            (app.buttons["Pipeline"].value as? String) == name
        })
    }

    /// Clear lives in the card's long-press menu since 2026-09-26.
    @MainActor
    private func clearMaterial(for role: String, in app: XCUIApplication) {
        let card = app.buttons[role]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.press(forDuration: 1.0)
        let clear = app.buttons["Clear"]
        XCTAssertTrue(clear.waitForExistence(timeout: 5))
        clear.tap()
        XCTAssertTrue(waitUntil(timeout: 10) {
            (app.buttons[role].value as? String) == "Missing required"
        })
    }

    @MainActor
    private func waitUntil(timeout: TimeInterval, condition: @escaping () -> Bool) -> Bool {
        Phase4Draft.waitUntil(timeout: timeout, condition: condition)
    }
}
