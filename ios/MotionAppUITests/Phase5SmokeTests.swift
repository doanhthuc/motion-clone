import XCTest

/// Zero-spend: launched with -UITestRecordingSpendGate. Reads the Pod tab,
/// then opens the migrate flow as far as the typed confirmation — `ask` acts
/// on nothing and its token lapses in 10 minutes. Never taps a GPU row (that
/// rewrites the live .env), Kill, or Migrate.
final class Phase5SmokeTests: XCTestCase {
    @MainActor
    func testPodTabRendersAndMigrateStaysLocked() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["Pod"].tap()

        XCTAssertTrue(app.buttons["pod.checkVast"].waitForExistence(timeout: 60), "balance card renders")
        let rows = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "gpu.row."))
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 90) { rows.count == 5 }, "five GPU rows")

        guard app.descendants(matching: .any)["pod.none"].waitForExistence(timeout: 10) else {
            throw XCTSkip("A pod is live — the migrate half of this smoke runs only with nothing rented.")
        }
        Phase4Draft.revealButton("pod.moveVolume", in: app).tap()

        if app.descendants(matching: .any)["migrate.blocked"].waitForExistence(timeout: 5) {
            throw XCTSkip("The run is busy — migrate is blocked on the phone, as designed.")
        }
        let destination = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "migrate.dest.")).firstMatch
        guard destination.waitForExistence(timeout: 60) else {
            throw XCTSkip("No other datacenter has stock right now.")
        }
        destination.tap()

        let confirm = app.buttons["migrate.confirm"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 120), "ask returned a confirmation")
        XCTAssertFalse(confirm.isEnabled, "Migrate stays locked until the destination is typed")
        XCTAssertTrue(app.descendants(matching: .any)["migrate.warning"].exists)

        app.buttons["Close"].tap()
        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0",
                       "the recording gate saw no spend")
    }
}
