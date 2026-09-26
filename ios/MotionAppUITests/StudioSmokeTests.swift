import XCTest

/// Zero-spend, live server: opens the sidebar, switches to Image Studio,
/// creates a project, opens the settings sheet, and never taps Send.
/// Deletes the project it made at the end.
final class StudioSmokeTests: XCTestCase {
    @MainActor
    func testSidebarAndComposerWithoutSpending() throws {
        let app = XCUIApplication()
        app.launchArguments += ["-UITestRecordingSpendGate"]
        app.launch()
        let open = app.buttons["sidebar.open"].firstMatch
        XCTAssertTrue(open.waitForExistence(timeout: 20))
        open.tap()
        app.buttons["sidebar.studio"].tap()
        open.tap()
        app.buttons["New project"].firstMatch.tap()
        XCTAssertTrue(app.textFields["studio.prompt"].waitForExistence(timeout: 10)
                      || app.textViews["studio.prompt"].waitForExistence(timeout: 1))
        XCTAssertFalse(app.buttons["studio.send"].isEnabled, "Send stays disabled with an empty prompt")
        app.buttons["studio.settings"].tap()
        XCTAssertTrue(app.buttons["studio.model.nano-banana-2"].waitForExistence(timeout: 10))
        app.swipeDown()
        // Clean up: delete the project from the sidebar.
        app.buttons["sidebar.open"].firstMatch.tap()
        let row = app.buttons.matching(NSPredicate(format: "label CONTAINS '0 images'")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.press(forDuration: 1.0)
        app.buttons["Delete"].tap()
    }
}
