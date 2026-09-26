import Testing
@testable import MotionKit

@Suite struct FormattingTests {
    @Test func clock() {
        #expect(Format.clock(0) == "00:00")
        #expect(Format.clock(754) == "12:34")
        #expect(Format.clock(4367) == "1:12:47")
        #expect(Format.clock(-5) == "00:00")
    }
    @Test func usd() {
        #expect(Format.usd(0.4234) == "$0.42")
        #expect(Format.usd(12.4) == "$12.40")
    }
    @Test func ago() {
        #expect(Format.ago(12) == "12s ago")
        #expect(Format.ago(125) == "2m ago")
        #expect(Format.ago(3 * 3600 + 5) == "3h ago")
        #expect(Format.ago(50 * 3600) == "2d ago")
    }
    @Test func stageNames() {
        #expect(Format.stageName("tryon") == "Try-on")
        #expect(Format.stageName("motion") == "Motion")
        #expect(Format.stageName("some_new_stage") == "Some new stage")
    }
    @Test func costIsElapsedTimesQuote() {
        #expect(CostEstimate.usd(elapsed: 1800, ratePerHour: 0.99) == 0.495)
        #expect(CostEstimate.usd(elapsed: 1800, ratePerHour: nil) == nil)
    }
    @Test func runwayRoundsToWholeMinutes() {
        #expect(Format.runway(hours: 12.46) == "12h 28m")
        #expect(Format.runway(hours: 0.5) == "30m")
        #expect(Format.runway(hours: 0) == "0m")
        #expect(Format.runway(hours: -1) == "0m")
    }
}
