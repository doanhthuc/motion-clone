import XCTest

/// Zero-spend, live server: a queued job's detail sheet and the cards' ✕
/// (2026-09-26). Only free draft mutations; clears at the end.
final class NewJobEntryDetailTests: XCTestCase {
    /// The ✕ empties a card, and each material in the detail peeks at itself:
    /// the detail's tiles once sat in one List cell, and every long press
    /// peeked at Character.
    @MainActor
    func testCardClearsAndDetailPeeksTheMaterialPressed() throws {
        let app = queueOneJob()

        let character = app.buttons["Character"]
        XCTAssertNotEqual(character.value as? String, "Missing required")
        attach(app, "cards-with-clear")
        character.coordinate(withNormalizedOffset: CGVector(dx: 1, dy: 0))
            .withOffset(CGVector(dx: -22, dy: 22)).tap()
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 15) {
            (character.value as? String) == "Missing required"
        }, "the ✕ must empty the card")

        openJob(1, in: app)
        let driver = app.descendants(matching: .any)["entry.material.driver"]
        XCTAssertTrue(driver.waitForExistence(timeout: 10))
        attach(app, "entry-detail")
        driver.press(forDuration: 1.0)
        XCTAssertTrue(app.buttons["Replace"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["View full screen"].exists)
        attach(app, "entry-peek-driver")
        app.buttons["View full screen"].tap()
        let done = app.navigationBars.buttons["Done"].firstMatch
        XCTAssertTrue(done.waitForExistence(timeout: 5))
        done.tap()
        XCTAssertTrue(app.navigationBars["Job 1"].waitForExistence(timeout: 5))
        app.navigationBars["Job 1"].buttons["Done"].tap()

        Phase4Draft.clear(in: app)
    }

    /// Needs `PATCH /v1/draft/batch/<digest>` on the server.
    @MainActor
    func testProviderChangesInPlace() throws {
        let app = queueOneJob()
        openJob(1, in: app)
        let provider = app.buttons["entry.provider"]
        XCTAssertTrue(provider.waitForExistence(timeout: 10))
        let before = provider.value as? String ?? ""
        provider.tap()
        attach(app, "entry-provider-open")
        let other = app.buttons.allElementsBoundByIndex
            .first { $0.label.contains("Self-host") || ($0.label.contains("Gemini") && !before.contains("Gemini")) }
        let target = try XCTUnwrap(other, "another provider must be offered")
        let wanted = target.label.contains("Self-host") ? "Self-host" : "Gemini"
        target.tap()
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 20) {
            (provider.value as? String)?.contains(wanted) == true
        }, "the provider must change on the queued job")
        XCTAssertTrue(app.navigationBars["Job 1"].exists, "the sheet stays on the job it edited")
        attach(app, "entry-provider-changed")
        app.navigationBars["Job 1"].buttons["Done"].tap()
        Phase4Draft.clear(in: app)
        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0")
    }

    @MainActor
    private func queueOneJob() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.buttons["newjob.more"].waitForExistence(timeout: 15))
        Phase4Draft.composeTryonJob(in: app)
        let add = app.buttons["batch.run"]
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 20) { add.isEnabled })
        add.tap()
        XCTAssertTrue(app.staticTexts["Batch · 1"].waitForExistence(timeout: 60))
        return app
    }

    @MainActor
    private func openJob(_ number: Int, in app: XCUIApplication) {
        let basket = app.buttons["newjob.basket"]
        XCTAssertTrue(basket.waitForExistence(timeout: 5))
        basket.tap()
        let row = app.staticTexts["Job \(number)"]
        XCTAssertTrue(row.waitForExistence(timeout: 5))
        row.tap()
        XCTAssertTrue(app.navigationBars["Job \(number)"].waitForExistence(timeout: 5))
    }

    @MainActor
    private func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
