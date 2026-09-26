import XCTest

/// Zero-spend, live server: New Job must fit on one screen (2026-09-26 spec
/// §1). Every card sits fully inside the window and above the action bar with
/// no scrolling, empty and filled, and the screenshots are attached as the
/// evidence the spec asks for. Only free draft mutations; clears at the end.
final class NewJobStageTests: XCTestCase {
    @MainActor
    func testStageFitsWithoutScrolling() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["New Job"].tap()
        XCTAssertTrue(app.buttons["newjob.more"].waitForExistence(timeout: 15))

        Phase4Draft.clear(in: app)
        selectLongestTryonPipeline(in: app)
        assertCardsFit(in: app)
        attach(app, "stage-empty")

        Phase4Draft.chooseMaterial(for: "Character", in: app)
        Phase4Draft.chooseMaterial(for: "Driver", in: app)
        Phase4Draft.chooseMaterial(for: "Outfit", in: app)
        assertCardsFit(in: app)
        attach(app, "stage-filled")

        Phase4Draft.clear(in: app)
        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0",
                       "the recording gate saw no spend")
    }

    /// No swipe happens before these reads, so a card found here was on screen
    /// as the stage first drew it.
    @MainActor
    private func assertCardsFit(in app: XCUIApplication) {
        let window = app.windows.firstMatch.frame
        let bar = app.otherElements["newjob.actionBar"]
        XCTAssertTrue(bar.waitForExistence(timeout: 10))
        // The chip must leave room for the menu: with a long pipeline name it
        // once pushed "More" out of the bar, and Clear with it (2026-09-26).
        XCTAssertTrue(app.buttons["newjob.more"].isHittable, "the More menu must stay in the bar")
        let cards = ["Character", "Driver", "Outfit", "Background"].map { app.buttons[$0] }.filter(\.exists)
        XCTAssertGreaterThanOrEqual(cards.count, 3, "a try-on pipeline shows at least three cards")
        for card in cards {
            XCTAssertTrue(card.isHittable, "\(card.label) is not hittable")
            XCTAssertGreaterThanOrEqual(card.frame.minY, window.minY, "\(card.label) starts above the screen")
            XCTAssertLessThanOrEqual(card.frame.maxY, bar.frame.minY + 1,
                                     "\(card.label) runs under the action bar")
        }
    }

    /// The longest try-on pipeline name the server offers, so the chip is
    /// measured at its widest.
    @MainActor
    private func selectLongestTryonPipeline(in app: XCUIApplication) {
        let chip = app.buttons["Pipeline"]
        XCTAssertTrue(chip.waitForExistence(timeout: 10))
        chip.tap()
        XCTAssertTrue(app.navigationBars["Settings"].waitForExistence(timeout: 5))
        let names = app.buttons.allElementsBoundByIndex.map(\.label)
            .filter { $0.localizedCaseInsensitiveContains("Try-on") }
        guard let longest = names.max(by: { $0.count < $1.count }) else {
            return XCTFail("A try-on pipeline must be available")
        }
        app.buttons[longest].tap()
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 10) {
            (app.buttons["Pipeline"].value as? String) == longest
        })
    }

    @MainActor
    private func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
