import Foundation
import UserNotifications

/// One banner per run, under the run's own identifier so a second slow stage
/// replaces the first instead of stacking. Local only: the app learns of the
/// warning by polling, so this fires while the app is running, not when it is
/// suspended (that would take APNs, which the server does not send).
public struct SlowRunNotifier: Sendable {
    public init() {}

    public func post(_ notice: SlowRunNotice) {
        let content = UNMutableNotificationContent()
        content.title = notice.title
        content.body = notice.body
        content.sound = .default
        UNUserNotificationCenter.current().add(
            UNNotificationRequest(identifier: "slow-run-\(notice.runID)", content: content, trigger: nil))
    }
}
