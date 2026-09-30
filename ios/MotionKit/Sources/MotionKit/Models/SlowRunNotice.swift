import Foundation

/// What the phone says when a run's stage is far past its usual time. Pure
/// text so it is testable here; `SlowRunNotifier` turns it into a banner.
///
/// Only the run and whether the GPU looks throttled are known from the list
/// poll, so the text stays at that level; the job page has the stage and the
/// GPU figures.
public struct SlowRunNotice: Equatable, Sendable {
    public let runID: String
    public let title: String
    public let body: String

    public init(runID: String, slowStages: Int) {
        self.runID = runID
        title = "Run \(runID) is running slowly"
        body = slowStages == 1
            ? "A stage is far past its usual time. Open the run to see the GPU."
            : "\(slowStages) stages are far past their usual time. Open the run to see the GPU."
    }

    /// Runs whose slow count went up since the last poll. A run seen for the
    /// first time counts from 0, so opening the app on an already-slow run
    /// notifies once; a count that stays or falls does not notify again.
    public static func newlySlow(before: [String: Int], after: [RunSummary]) -> [SlowRunNotice] {
        after.compactMap { run in
            let now = run.slowStages ?? 0
            guard now > (before[run.id] ?? 0) else { return nil }
            return SlowRunNotice(runID: run.id, slowStages: now)
        }
    }
}
