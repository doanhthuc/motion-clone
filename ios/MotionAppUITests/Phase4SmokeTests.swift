import XCTest

/// Zero-spend: the app is launched with -UITestRecordingSpendGate, so even a
/// stray tap on a spend button is recorded on the phone and never sent. The
/// test still never taps one. The rent panel read is a free GET.
final class Phase4SmokeTests: XCTestCase {
    @MainActor
    func testRunFlowReachesRentPanelWithoutSpending() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.staticTexts["New Job"].waitForExistence(timeout: 15))

        Phase4Draft.composeValidatedTryonJob(in: app)

        let proceed = app.buttons["newjob.continueToRun"]
        XCTAssertTrue(proceed.waitForExistence(timeout: 10))
        proceed.tap()

        let rent = app.buttons["runflow.rentWithoutPreview"]
        XCTAssertTrue(rent.waitForExistence(timeout: 20))
        XCTAssertTrue(app.buttons["runflow.previewTryon"].exists, "try-on draft makes Preview primary")
        rent.tap()

        // Either a priced Confirm or the no-stock state — both are valid live answers.
        let confirm = app.buttons["runflow.confirm"]
        let soldOut = app.descendants(matching: .any)["runflow.soldOut"]
        let deadline = Date().addingTimeInterval(150)
        while !confirm.exists && !soldOut.exists && Date() < deadline {
            _ = confirm.waitForExistence(timeout: 2)
        }
        XCTAssertTrue(confirm.exists || soldOut.exists, "rent panel must render")
        if confirm.exists {
            XCTAssertTrue(confirm.label.contains("$"), "Confirm carries its quote")
        }

        app.navigationBars.buttons.element(boundBy: 0).tap()
        Phase4Draft.clear(in: app)
    }
}

/// Copied from `Phase3SmokeTests`'s private helpers (verbatim bodies) so this
/// file can compose a validated try-on draft without depending on that file.
enum Phase4Draft {
    @MainActor static func composeValidatedTryonJob(in app: XCUIApplication) {
        clear(in: app)
        selectTryonPipeline(in: app)
        chooseMaterial(for: "Character", in: app)
        chooseMaterial(for: "Driver", in: app)
        chooseMaterial(for: "Outfit", in: app)
        revealButton("Validate", in: app).tap()
        XCTAssertTrue(app.staticTexts["Ready"].waitForExistence(timeout: 30))
    }

    @MainActor static func clear(in app: XCUIApplication) {
        revealButton("Clear", in: app).tap()
        XCTAssertTrue(app.staticTexts["0 of 3 required slots assigned"].waitForExistence(timeout: 10)
            || app.staticTexts["0 of 2 required slots assigned"].waitForExistence(timeout: 1))
    }

    @MainActor static func selectTryonPipeline(in app: XCUIApplication) {
        let current = app.buttons["Pipeline"].value as? String ?? ""
        if current.localizedCaseInsensitiveContains("Tryon") { return }

        app.buttons["Pipeline"].tap()
        let option = app.buttons.allElementsBoundByIndex.first {
            $0.label.localizedCaseInsensitiveContains("Tryon")
        }
        XCTAssertNotNil(option, "A try-on pipeline must be available")
        option?.tap()
        XCTAssertTrue(waitUntil(timeout: 10) {
            (app.buttons["Pipeline"].value as? String)?.localizedCaseInsensitiveContains("Tryon") == true
        })
    }

    @MainActor static func chooseMaterial(for role: String, in app: XCUIApplication) {
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

    @MainActor static func revealButton(_ label: String, in app: XCUIApplication) -> XCUIElement {
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

    @MainActor static func waitUntil(timeout: TimeInterval, condition: @escaping () -> Bool) -> Bool {
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
