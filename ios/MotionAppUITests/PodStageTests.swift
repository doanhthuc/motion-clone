import XCTest

/// Zero-spend, live server: the Pod tab fits on one screen (2026-09-27 spec
/// §2). Every tile sits inside the window and above the Watching drawer with
/// no scrolling; the GPU sheet opens and, once the server supports
/// `?all=1` (post-deploy), lists datacenters. Never taps a bell (a real sub
/// would post a real Telegram message), Use, Kill or Migrate.
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
        // Pre-deploy, the live server does not yet answer `?all=1` with
        // `datacenters`, so the sheet opens with an empty "Datacenters"
        // section (GpuSheet.swift's "runpodctl lists no datacenter…" copy).
        // Only assert the sheet itself opens here; the datacenter rows are
        // verified once the deploy ships (see the spec's gate record).
        XCTAssertTrue(
            app.descendants(matching: .any)["gpu.sheet"].waitForExistence(timeout: 60)
                || app.staticTexts["Datacenters"].waitForExistence(timeout: 5),
            "the GPU sheet opens"
        )
        let rows = app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH %@", "gpu.dc."))
        _ = Phase4Draft.waitUntil(timeout: 10) { rows.count > 0 }
        if rows.count > 0 {
            attach(app, "gpu-sheet")
        } else {
            attach(app, "gpu-sheet-no-datacenters")
        }
        app.buttons["Done"].tap()

        drawer.tap()
        XCTAssertTrue(app.descendants(matching: .any)["pod.watchScrim"].waitForExistence(timeout: 5))
        attach(app, "watch-open")
        app.descendants(matching: .any)["pod.watchScrim"].tap()

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
