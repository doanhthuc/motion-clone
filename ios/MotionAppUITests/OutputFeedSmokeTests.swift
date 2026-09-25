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

        let counter = app.staticTexts.matching(NSPredicate(format: "label MATCHES %@", "\\d+ of \\d+")).firstMatch
        XCTAssertTrue(counter.waitForExistence(timeout: 10))
        XCTAssertFalse(app.tabBars.firstMatch.isHittable, "tab bar should hide over the feed")
        XCTAssertTrue(app.buttons["Save to Photos"].exists)
        XCTAssertTrue(app.buttons["Share"].exists)
        sleep(4)  // let the first clip buffer and play
        shot("1-first")
        try checkAccessibleControls(app)

        let first = counter.label
        let total = Int(first.split(separator: " ").last ?? "") ?? 1
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

    /// What VoiceOver, a hardware keyboard and a thumb slightly off the hairline each get.
    @MainActor private func checkAccessibleControls(_ app: XCUIApplication) throws {
        let page = app.otherElements.matching(NSPredicate(format: "value IN %@", ["Playing", "Paused"])).firstMatch
        XCTAssertTrue(page.waitForExistence(timeout: 5), "the page is one element with a play state")
        XCTAssertEqual(page.value as? String, "Playing")

        // Space pauses and plays, like any other player.
        app.typeKey(" ", modifierFlags: [])
        expectation(for: NSPredicate(format: "value == 'Paused'"), evaluatedWith: page)
        waitForExpectations(timeout: 3)
        app.typeKey(" ", modifierFlags: [])
        expectation(for: NSPredicate(format: "value == 'Playing'"), evaluatedWith: page)
        waitForExpectations(timeout: 3)

        let scrub = app.descendants(matching: .any)["Playback position"]
        XCTAssertTrue(scrub.exists, "the scrub bar is an element VoiceOver can find")
        // The element's frame is its touch area: the 24pt row plus 10pt above and below.
        XCTAssertEqual(scrub.frame.height, 44, accuracy: 1)

        // A drag that starts 8pt above the row, on the video, still scrubs rather than
        // paging or tap-pausing. Pause first so the clock stands still.
        app.typeKey(" ", modifierFlags: [])
        // Two drags in opposite directions, so a stopped clock can't pass both by luck.
        for (target, check) in [(0.1, { (r: Double) in r < 0.35 }), (0.9, { (r: Double) in r > 0.65 })] {
            let from = scrub.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0)).withOffset(CGVector(dx: 0, dy: 2))
            let to = scrub.coordinate(withNormalizedOffset: CGVector(dx: target, dy: 0)).withOffset(CGVector(dx: 0, dy: 2))
            from.press(forDuration: 0.1, thenDragTo: to)
            XCTAssertEqual(page.value as? String, "Paused", "the drag scrubbed; it didn't tap-pause")
            let (at, of) = clock(scrub.value as? String)
            XCTAssertGreaterThan(of, 0)
            XCTAssertTrue(check(Double(at) / Double(of)), "scrub toward \(target) landed at \(scrub.value ?? "")")
        }
        app.typeKey(" ", modifierFlags: [])
    }

    /// "0:04 of 0:09" → (4, 9) in seconds.
    private func clock(_ label: String?) -> (Int, Int) {
        let parts = (label ?? "").components(separatedBy: " of ").map { part -> Int in
            let mmss = part.split(separator: ":").compactMap { Int($0) }
            return mmss.count == 2 ? mmss[0] * 60 + mmss[1] : 0
        }
        return parts.count == 2 ? (parts[0], parts[1]) : (0, 0)
    }

    @MainActor private func shot(_ name: String) {
        guard let dir = ProcessInfo.processInfo.environment["FEED_SHOTS"] else { return }
        let url = URL(fileURLWithPath: dir).appendingPathComponent("\(name).png")
        try? XCUIScreen.main.screenshot().pngRepresentation.write(to: url)
    }
}
