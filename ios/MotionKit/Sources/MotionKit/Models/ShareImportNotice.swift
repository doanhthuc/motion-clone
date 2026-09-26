import Foundation

/// What a TikTok import reports: the share extension's banners ("downloading"
/// as the sheet closes, then the outcome under the same identifier, so
/// Notification Center keeps one entry per share) and the Shortcuts action's
/// outcome banner.
///
/// Pure text so it is testable here; `ImportNotifier` turns it into a banner.
public struct ShareImportNotice: Equatable, Sendable {
    public enum Stage: Equatable, Sendable {
        case downloading
        /// The clip's length, when the server probed one. The file name and
        /// the frame size are left out: neither helps tell clips apart
        /// (`tiktok-<epoch>.mp4`, and nearly every TikTok is 1080×1920).
        case done(durationS: Double?)
        case failed(String)
        /// iOS ended the extension's background time before the answer came
        /// back. The server keeps downloading regardless (links.py runs the
        /// download in the request thread, not tied to our connection).
        case stillRunning
    }

    /// Tapping any of these opens the app on this tab (`AppTab` raw name).
    public static let routeKey = "route"
    public static let materialsRoute = "materials"

    public let title: String
    public let body: String

    public init(_ stage: Stage) {
        switch stage {
        case .downloading:
            title = "Downloading TikTok video…"
            body = "It will be added to Materials."
        case .done(let durationS):
            title = "TikTok video added"
            body = durationS.map { "\(Self.duration($0)) · Ready in Materials." } ?? "Ready in Materials."
        case .failed(let message):
            title = "TikTok download failed"
            body = message
        case .stillRunning:
            title = "Still downloading"
            body = "The video will show up in Materials when it finishes."
        }
    }

    /// "15s" for a clip, "1:05" once it passes a minute.
    static func duration(_ seconds: Double) -> String {
        let s = Int(seconds.rounded())
        return s < 60 ? "\(s)s" : String(format: "%d:%02d", s / 60, s % 60)
    }
}
