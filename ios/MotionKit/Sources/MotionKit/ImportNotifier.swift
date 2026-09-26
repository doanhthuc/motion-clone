import Foundation
import UserNotifications

/// One TikTok import's banners, all under one identifier so each stage
/// replaces the last instead of stacking up in Notification Center. Posted by
/// the share extension and by the Shortcuts action (SaveTikTokIntent); both
/// run under the app's identity, so a tap reaches the app's delegate.
public struct ImportNotifier: Sendable {
    public init() {}

    private let id = "share-import-\(UUID().uuidString)"

    /// Authorization belongs to the containing app, which asks on first launch.
    public static func canNotify() async -> Bool {
        let status = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        #if os(iOS)
        if status == .ephemeral { return true }
        #endif
        return status == .authorized || status == .provisional
    }

    public func post(_ stage: ShareImportNotice.Stage) {
        UNUserNotificationCenter.current().add(request(stage, thumbnail: nil))
    }

    /// Awaited where order or lifetime matters: the outcome must not end the
    /// expiring activity (and let the process be suspended) before it lands.
    public func postAndWait(_ stage: ShareImportNotice.Stage, thumbnail: Data? = nil) async {
        try? await UNUserNotificationCenter.current().add(request(stage, thumbnail: thumbnail))
    }

    private func request(_ stage: ShareImportNotice.Stage, thumbnail: Data?) -> UNNotificationRequest {
        let notice = ShareImportNotice(stage)
        let content = UNMutableNotificationContent()
        content.title = notice.title
        content.body = notice.body
        content.userInfo = [ShareImportNotice.routeKey: ShareImportNotice.materialsRoute]
        // The downloading banner is silent; only the outcome makes a sound.
        if stage != .downloading { content.sound = .default }
        if let thumbnail, let attachment = Self.attachment(thumbnail) {
            content.attachments = [attachment]
        }
        return UNNotificationRequest(identifier: id, content: content, trigger: nil)
    }

    /// The server's poster frame (a JPEG) as the banner's thumbnail. The
    /// center moves the file into its own store, so a temp path is enough.
    private static func attachment(_ jpeg: Data) -> UNNotificationAttachment? {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("share-thumb-\(UUID().uuidString).jpg")
        do {
            try jpeg.write(to: url)
            return try UNNotificationAttachment(identifier: "thumb", url: url)
        } catch {
            return nil
        }
    }
}
