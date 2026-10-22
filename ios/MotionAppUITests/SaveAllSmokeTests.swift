import XCTest

/// Live, no GPU: Save all on an Outputs batch asks first and can be cancelled, and Save all on a
/// run with videos downloads every one into the simulator's Photos.
/// Set SAVE_ALL_SHOTS=<dir> to keep screenshots for a visual check.
final class SaveAllSmokeTests: XCTestCase {
    @MainActor
    func testSaveAllFromOutputsAndRun() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestNoNotificationPrompt")
        app.launch()

        app.tabBars.buttons["Outputs"].tap()
        let batchSave = app.buttons["outputs.saveAll"].firstMatch
        XCTAssertTrue(batchSave.waitForExistence(timeout: 20), "a batch with videos offers Save all")
        shot("1-outputs")
        batchSave.tap()
        let ask = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Save'")).matching(
            NSPredicate(format: "identifier != 'outputs.saveAll'")).firstMatch
        XCTAssertTrue(ask.waitForExistence(timeout: 5), "Save all confirms before saving")
        shot("2-outputs-confirm")
        // iOS 26 shows the dialog as a popover with no Cancel row; a tap outside dismisses it.
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.6)).tap()
        let gone = NSPredicate(format: "exists == false")
        expectation(for: gone, evaluatedWith: ask)
        waitForExpectations(timeout: 5)

        // Select hides it: the bottom bar is for deleting.
        app.buttons["selection.toggle"].tap()
        XCTAssertFalse(app.buttons["outputs.saveAll"].exists, "no Save all while selecting")
        app.buttons["selection.toggle"].tap()

        app.tabBars.buttons["Runs"].tap()
        let run = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] ' video'")).firstMatch
        XCTAssertTrue(run.waitForExistence(timeout: 20), "a run with videos")
        run.tap()
        let runSave = app.buttons["run.saveAll"]
        XCTAssertTrue(runSave.waitForExistence(timeout: 20), "a run with videos offers Save all")
        runSave.tap()
        let confirm = app.buttons.matching(NSPredicate(format: "label BEGINSWITH 'Save'")).matching(
            NSPredicate(format: "identifier != 'run.saveAll'")).firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        shot("3-run-confirm")
        confirm.tap()
        // The add-only prompt belongs to SpringBoard, and only shows on the simulator's first save.
        let allow = XCUIApplication(bundleIdentifier: "com.apple.springboard").buttons["Allow"]
        if allow.waitForExistence(timeout: 8) { allow.tap() }

        // The status line shows while saving; shot straight away rather than queried, because
        // one snapshot of this screen takes longer than the 2.5 s the final toast stays up.
        sleep(1)
        shot("4-run-saving")
        // Save all is disabled while saving, so enabled again means the loop finished.
        expectation(for: NSPredicate(format: "isEnabled == true"), evaluatedWith: runSave)
        waitForExpectations(timeout: 240)
        shot("5-run-saved")
    }

    @MainActor private func shot(_ name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        guard let dir = ProcessInfo.processInfo.environment["SAVE_ALL_SHOTS"] else { return }
        let url = URL(fileURLWithPath: dir).appendingPathComponent("\(name).png")
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: url)
    }
}
