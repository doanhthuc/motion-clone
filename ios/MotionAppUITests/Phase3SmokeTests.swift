import XCTest

final class Phase3SmokeTests: XCTestCase {
    @MainActor
    func testDraftCompositionBatchDropAndValidation() throws {
        let app = XCUIApplication()
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.staticTexts["New Job"].waitForExistence(timeout: 15))

        clearDraft(in: app)
        selectTryonPipeline(in: app)

        chooseMaterial(for: "Outfit", in: app)
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@", "DecodingError")
        ).firstMatch.exists)
        XCTAssertNotEqual(app.buttons["Outfit"].value as? String, "Missing required")

        selectPipeline(named: "Motion Enhance", in: app)
        XCTAssertTrue(Phase4Draft.revealText("Removed incompatible slots: outfit.", in: app))

        selectTryonPipeline(in: app)
        chooseMaterial(for: "Character", in: app)
        chooseMaterial(for: "Driver", in: app)
        chooseMaterial(for: "Outfit", in: app)

        clearMaterial(for: "Character", in: app)
        XCTAssertEqual(app.buttons["Character"].value as? String, "Missing required")
        XCTAssertFalse(revealButton("Add to batch", in: app).isEnabled)
        chooseMaterial(for: "Character", in: app)

        let add = revealButton("Add to batch", in: app)
        XCTAssertTrue(add.isEnabled)
        add.tap()

        let firstDrop = revealButton("Drop", in: app)
        firstDrop.tap()
        XCTAssertTrue(app.sheets["Drop this batch entry?"].waitForExistence(timeout: 5))
        app.sheets["Drop this batch entry?"].buttons["Drop"].tap()
        XCTAssertTrue(app.buttons["Drop"].waitForNonExistence(timeout: 10))

        revealButton("Add to batch", in: app).tap()
        _ = revealButton("Drop", in: app)
        revealButton("Validate", in: app).tap()
        XCTAssertTrue(Phase4Draft.revealText("Ready", in: app, timeout: 15))
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS[c] %@ AND label CONTAINS[c] %@", "about", "min")
        ).firstMatch.waitForExistence(timeout: 5))

        clearMaterial(for: "Character", in: app)
        XCTAssertTrue(app.staticTexts["Ready"].waitForNonExistence(timeout: 10))

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
        revealButton("Clear", in: app).tap()
        XCTAssertTrue(Phase4Draft.revealText("0 of 3 required slots assigned", in: app)
            || Phase4Draft.revealText("0 of 2 required slots assigned", in: app, timeout: 1))
    }

    @MainActor
    private func selectTryonPipeline(in app: XCUIApplication) {
        let pipeline = revealButton("Pipeline", in: app)
        let current = pipeline.value as? String ?? ""
        if current.localizedCaseInsensitiveContains("Try-on") { return }

        pipeline.tap()
        let option = app.buttons.allElementsBoundByIndex.first {
            $0.label.localizedCaseInsensitiveContains("Try-on")
        }
        XCTAssertNotNil(option, "A try-on pipeline must be available")
        option?.tap()
        XCTAssertTrue(waitUntil(timeout: 10) {
            (app.buttons["Pipeline"].value as? String)?.localizedCaseInsensitiveContains("Try-on") == true
        })
    }

    @MainActor
    private func selectPipeline(named name: String, in app: XCUIApplication) {
        revealButton("Pipeline", in: app).tap()
        XCTAssertTrue(app.buttons[name].waitForExistence(timeout: 5))
        app.buttons[name].tap()
        XCTAssertTrue(waitUntil(timeout: 10) {
            (app.buttons["Pipeline"].value as? String) == name
        })
    }

    @MainActor
    private func chooseMaterial(for role: String, in app: XCUIApplication) {
        revealButton(role, in: app).tap()
        XCTAssertTrue(app.navigationBars["Choose material"].waitForExistence(timeout: 10))

        let choice = app.buttons.matching(
            NSPredicate(format: "value == %@", "Not selected")
        ).firstMatch
        XCTAssertTrue(choice.waitForExistence(timeout: 10), "A compatible material must exist for \(role)")
        choice.tap()
        XCTAssertTrue(app.navigationBars["Choose material"].waitForNonExistence(timeout: 10))
        XCTAssertTrue(waitUntil(timeout: 10) {
            (app.buttons[role].value as? String) != "Missing required"
        })
    }

    @MainActor
    private func clearMaterial(for role: String, in app: XCUIApplication) {
        revealButton(role, in: app).tap()
        let picker = app.navigationBars["Choose material"]
        XCTAssertTrue(picker.waitForExistence(timeout: 10))
        XCTAssertTrue(picker.buttons["Clear"].waitForExistence(timeout: 5))
        picker.buttons["Clear"].tap()
        XCTAssertTrue(app.navigationBars["Choose material"].waitForNonExistence(timeout: 10))
    }

    @MainActor
    private func revealButton(_ label: String, in app: XCUIApplication) -> XCUIElement {
        let button = app.buttons[label]
        for _ in 0..<8 where !button.isHittable {
            app.swipeDown()
        }
        for _ in 0..<10 where !button.isHittable {
            app.swipeUp()
        }
        XCTAssertTrue(button.waitForExistence(timeout: 5), "Missing button: \(label)")
        XCTAssertTrue(button.isHittable, "Button is not visible: \(label)")
        return button
    }

    @MainActor
    private func waitUntil(timeout: TimeInterval, condition: @escaping () -> Bool) -> Bool {
        let expectation = XCTNSPredicateExpectation(
            predicate: NSPredicate { _, _ in condition() }, object: nil)
        return XCTWaiter.wait(for: [expectation], timeout: timeout) == .completed
    }
}

private extension XCUIElement {
    @MainActor
    func waitForNonExistence(timeout: TimeInterval) -> Bool {
        let predicate = NSPredicate(format: "exists == false")
        let expectation = XCTNSPredicateExpectation(predicate: predicate, object: self)
        return XCTWaiter.wait(for: [expectation], timeout: timeout) == .completed
    }
}
