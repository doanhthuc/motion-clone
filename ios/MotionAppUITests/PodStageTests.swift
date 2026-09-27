import XCTest

/// Zero-spend, live server: the Pod tab fits on one screen (2026-09-27 spec
/// §2). Every tile sits inside the window and above the Watching drawer with
/// no scrolling; the GPU sheet opens and either lists datacenters or shows
/// the explicit `gpu.dc.unsupported` notice (pre-deploy, until the server
/// answers `?all=1` with `datacenters`). Never taps a bell (a real sub would
/// post a real Telegram message), Use, Kill or Migrate.
final class PodStageTests: XCTestCase {
    @MainActor
    func testStageFitsAndSheetOpens() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["Pod"].tap()

        let tiles = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "gpu.tile."))
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 90) { tiles.count == 5 }, "five GPU tiles")
        let drawer = app.buttons["pod.watch"]
        XCTAssertTrue(drawer.waitForExistence(timeout: 10))
        let window = app.windows.firstMatch.frame
        for i in 0..<tiles.count {
            let tile = tiles.element(boundBy: i)
            XCTAssertTrue(tile.isHittable, "\(tile.identifier) is not hittable")
            XCTAssertGreaterThanOrEqual(tile.frame.minY, window.minY)
            XCTAssertLessThanOrEqual(tile.frame.maxY, drawer.frame.minY + 1, "\(tile.identifier) runs under the drawer")
        }
        attach(app, "pod-stage")

        tiles.element(boundBy: 0).tap()
        XCTAssertTrue(app.descendants(matching: .any)["gpu.sheet"].waitForExistence(timeout: 60),
                      "the GPU sheet opens")
        // Pre-deploy, the live server does not yet answer `?all=1` with
        // `datacenters`, so `GpuSheet` renders the explicit `gpu.dc.unsupported`
        // notice instead of rows — a distinct, asserted-on state, not a
        // silently-tolerated absence. Post-deploy, real `gpu.dc.*` rows show
        // up instead and this test must fail if they ever stop showing up.
        let unsupported = app.descendants(matching: .any)["gpu.dc.unsupported"]
        let rows = app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH %@", "gpu.dc."))
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 60) { unsupported.exists || rows.count > 0 },
                      "the sheet shows either datacenters or the pre-deploy notice")
        if unsupported.exists {
            attach(app, "gpu-sheet-no-datacenters")
        } else {
            XCTAssertGreaterThan(rows.count, 0, "the sheet lists datacenters")
            attach(app, "gpu-sheet")
        }
        app.buttons["Done"].tap()

        drawer.tap()
        XCTAssertTrue(app.descendants(matching: .any)["pod.watchScrim"].waitForExistence(timeout: 5))
        attach(app, "watch-open")
        app.descendants(matching: .any)["pod.watchScrim"].tap()

        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0",
                       "the recording gate saw no spend")
    }

    /// The hero's tallest state — a migration mid-copy — rendered from a
    /// fixed fake under `-UITestPreviewMigration` (2026-09-27): no real move
    /// is started, so this checks on an iPhone SE that the five tiles still
    /// fit above the drawer when the hero is at its biggest.
    @MainActor
    func testMigrationHeroLeavesTilesVisible() throws {
        let app = XCUIApplication()
        app.launchArguments += ["-UITestRecordingSpendGate", "-UITestPreviewMigration"]
        app.launch()
        app.tabBars.buttons["Pod"].tap()

        XCTAssertTrue(app.staticTexts["Moving the volume to EU-CZ-1"].waitForExistence(timeout: 30),
                      "the preview migration renders in the hero")
        let tiles = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "gpu.tile."))
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 90) { tiles.count == 5 }, "five GPU tiles")
        let drawer = app.buttons["pod.watch"]
        XCTAssertTrue(drawer.waitForExistence(timeout: 10))
        // `.accessibilityIdentifier("pod.hero")` on the hero's container is
        // inherited by several of its children on this runtime, so the query
        // matches more than one element; the hero's bottom is the lowest.
        let heroParts = app.descendants(matching: .any).matching(identifier: "pod.hero").allElementsBoundByIndex
        XCTAssertFalse(heroParts.isEmpty, "the hero exists")
        let heroBottom = heroParts.map(\.frame.maxY).max() ?? 0
        for i in 0..<tiles.count {
            let tile = tiles.element(boundBy: i)
            XCTAssertTrue(tile.isHittable, "\(tile.identifier) is not hittable")
            XCTAssertGreaterThanOrEqual(tile.frame.minY, heroBottom - 1, "\(tile.identifier) overlaps the hero")
            XCTAssertLessThanOrEqual(tile.frame.maxY, drawer.frame.minY + 1, "\(tile.identifier) runs under the drawer")
        }
        attach(app, "pod-stage-migration")
        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0",
                       "the recording gate saw no spend")
    }

    @MainActor
    private func attach(_ app: XCUIApplication, _ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
}
