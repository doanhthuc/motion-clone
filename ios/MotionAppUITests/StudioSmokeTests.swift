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
        // The selected space persists across launches; leave the app in Motion so
        // the Phase smokes that run after this one start where they expect.
        addTeardownBlock { @MainActor in
            guard app.state == .runningForeground else { return }
            Self.returnToMotion(in: app)
        }
        XCTAssertTrue(app.buttons["sidebar.open"].firstMatch.waitForExistence(timeout: 20))
        Self.sidebarButtonOnScreen(in: app).tap()
        app.buttons["sidebar.studio"].tap()
        Self.sidebarButtonOnScreen(in: app).tap()
        // By identifier: the Studio empty state has its own "New project" button,
        // which sits half off-screen while the sidebar is open.
        app.buttons["sidebar.newProject"].tap()
        XCTAssertTrue(app.textFields["studio.prompt"].waitForExistence(timeout: 10)
                      || app.textViews["studio.prompt"].waitForExistence(timeout: 1))
        XCTAssertFalse(app.buttons["studio.send"].isEnabled, "Send stays disabled with an empty prompt")
        // Tapping outside the prompt dismisses the keyboard (app-wide
        // recognizer, KeyboardDismissal.swift). Typing a prompt is free: only
        // Send spends.
        let prompt = app.textViews["studio.prompt"].exists ? app.textViews["studio.prompt"] : app.textFields["studio.prompt"]
        prompt.tap()
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        prompt.typeText("a red chair")
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.35)).tap()
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: app.keyboards.firstMatch)
        waitForExpectations(timeout: 5)
        app.buttons["studio.settings"].tap()
        XCTAssertTrue(app.buttons["studio.settings.done"].waitForExistence(timeout: 10))
        // The sheet opens at its medium detent; the model list is below the fold
        // and lazily built, so scroll the sheet's form up to reach it.
        // By coordinate: the first collection view in the tree is the sidebar's list.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.85))
            .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.3)))
        XCTAssertTrue(app.buttons["studio.model.nano-banana-2"].waitForExistence(timeout: 10))
        // Done, not swipeDown: a swipe only drops the sheet to its medium detent.
        app.buttons["studio.settings.done"].tap()
        expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: app.buttons["studio.settings.done"])
        waitForExpectations(timeout: 10)
        // Clean up: delete the project from the sidebar. The row exists (off-screen at
        // x < 0) even while the sidebar is closed, so wait until it is hittable.
        Self.sidebarButtonOnScreen(in: app).tap()
        let row = app.buttons.matching(NSPredicate(format: "label CONTAINS '0 images'")).firstMatch
        expectation(for: NSPredicate(format: "isHittable == true"), evaluatedWith: row)
        waitForExpectations(timeout: 10)
        row.press(forDuration: 1.0)
        app.buttons["Delete"].tap()
        // A confirmation dialog can surface its action twice in the tree.
        let confirm = app.buttons["sidebar.confirmDelete"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
    }

    /// Both spaces stay mounted; the hidden one is moved far off-screen
    /// (SpaceShell.spaceVisibility), so its ☰ is still in the tree. Pick the
    /// one inside the window.
    @MainActor
    static func sidebarButtonOnScreen(in app: XCUIApplication) -> XCUIElement {
        let window = app.windows.firstMatch.frame
        let all = app.buttons.matching(identifier: "sidebar.open")
        return (0..<all.count).map { all.element(boundBy: $0) }
            .first { window.contains(CGPoint(x: $0.frame.midX, y: $0.frame.midY)) }
            ?? all.firstMatch
    }

    /// Opens the sidebar (retrying while a dialog or sheet is still dismissing)
    /// and picks Motion. On screen is judged by frame: `isHittable` reports the
    /// off-screen sidebar rows as hittable.
    @MainActor
    static func returnToMotion(in app: XCUIApplication) {
        let motion = app.buttons["sidebar.motion"]
        for _ in 0..<5 {
            sidebarButtonOnScreen(in: app).tap()
            let deadline = Date().addingTimeInterval(2)
            while Date() < deadline, motion.frame.minX < 0 { usleep(100_000) }
            if motion.frame.minX >= 0 { motion.tap(); return }
        }
        XCTFail("could not reopen the sidebar to return to Motion")
    }
}
