import Foundation
import MotionKit

/// Used only when the app is launched with `-UITestRecordingSpendGate`: a
/// stray tap in a UI test is recorded and refused on the phone, so the test
/// build can never reach a spend route on the VPS.
actor RecordingSpendGate: SpendSending {
    private(set) var intents: [SpendIntent] = []
    private let onRecord: @Sendable (Int) -> Void

    init(onRecord: @escaping @Sendable (Int) -> Void = { _ in }) {
        self.onRecord = onRecord
    }

    func perform(_ intent: SpendIntent, label: String, onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        intents.append(intent)
        onRecord(intents.count)
        return .notSent(reason: "UI test build — spend recorded, not sent.")
    }
    func recheck(onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        .notSent(reason: "UI test build — nothing to check.")
    }
    func replayPending() async -> SpendResult? { nil }
    func pending() async -> SpendLedgerEntry? { nil }
}
