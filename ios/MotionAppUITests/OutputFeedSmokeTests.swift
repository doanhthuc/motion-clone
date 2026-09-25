import XCTest

/// Live, read-only: opens a finished video in the outputs feed and swipes to the next file.
/// Set TEST_RUNNER_FEED_SHOTS=<dir> to keep screenshots of each page for a visual check.
final class OutputFeedSmokeTests: XCTestCase {
    @MainActor
    func testFeedPagesBetweenOutputs() throws {
        let app = XCUIApplication()
        app.launch()
        app.tabBars.buttons["Output"].tap()

        let video = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", ".mp4")).firstMatch
        XCTAssertTrue(video.waitForExistence(timeout: 20), "no finished video in Outputs")
        video.tap()

        let counter = app.staticTexts.matching(NSPredicate(format: "label MATCHES %@", ".* · \\d+/\\d+")).firstMatch
        XCTAssertTrue(counter.waitForExistence(timeout: 10))
        XCTAssertFalse(app.tabBars.firstMatch.isHittable, "tab bar should hide over the feed")
        XCTAssertTrue(app.buttons["Save to Photos"].exists)
        sleep(4)  // let the first clip buffer and play
        shot("1-first")

        let first = counter.label
        let total = Int(first.split(separator: "/").last ?? "") ?? 1
        guard total > 1 else { return }
        app.swipeUp()
        let moved = NSPredicate(format: "label != %@", first)
        expectation(for: moved, evaluatedWith: counter)
        waitForExpectations(timeout: 5)
        sleep(4)
        shot("2-next")

        app.tap()  // pause
        sleep(1)
        shot("3-paused")
    }

    @MainActor private func shot(_ name: String) {
        guard let dir = ProcessInfo.processInfo.environment["FEED_SHOTS"] else { return }
        let url = URL(fileURLWithPath: dir).appendingPathComponent("\(name).png")
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: url)
    }
}
