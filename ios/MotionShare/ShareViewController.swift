import MotionKit
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// The share sheet's "Motion": hands the shared TikTok link to the same
/// `POST /v1/materials/link` the New Job paste field uses. The server stages
/// the file before it answers, so closing this early never loses the video.
final class ShareViewController: UIViewController {
    private let model = ShareImportModel()

    override func viewDidLoad() {
        super.viewDidLoad()
        model.onFinish = { [weak self] in
            self?.extensionContext?.completeRequest(returningItems: nil)
        }
        let host = UIHostingController(rootView: ShareImportView(model: model))
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)

        let items = extensionContext?.inputItems.compactMap { $0 as? NSExtensionItem } ?? []
        Task {
            let candidates = await Self.candidates(from: items)
            await model.start(candidates: candidates)
        }
    }

    /// URL attachments first (TikTok shares one), then each item's own text
    /// (`attributedContentText`, appended ahead of its attachments' plain
    /// text in the loop below) — `SharedLink` picks the first TikTok link
    /// among them, so only "URLs before texts" has to hold, not a finer
    /// order within `texts` (2026-09-25).
    private static func candidates(from items: [NSExtensionItem]) async -> [String] {
        var urls: [String] = []
        var texts: [String] = []
        for item in items {
            if let text = item.attributedContentText?.string { texts.append(text) }
            for provider in item.attachments ?? [] {
                if let url = await loadURLString(provider) {
                    urls.append(url)
                } else if let text = await loadPlainText(provider) {
                    texts.append(text)
                }
            }
        }
        return urls + texts
    }

    /// `NSItemProvider.loadItem` hands back `NSSecureCoding`, which is not
    /// `Sendable` — the cast to `String` happens inside this helper so only
    /// the resulting `Sendable` string ever crosses back. Left implicitly
    /// (not explicitly `nonisolated`) so it stays on the caller's actor
    /// instead of hopping to a concurrent executor, which is what made
    /// sending the non-Sendable `provider` in raise a data-race error.
    private static func loadURLString(_ provider: NSItemProvider) async -> String? {
        guard provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) else { return nil }
        guard let item = try? await provider.loadItem(forTypeIdentifier: UTType.url.identifier) else { return nil }
        return (item as? URL)?.absoluteString
    }

    /// `loadItem` for `public.plain-text` is documented to hand back an
    /// `NSString`, but some senders hand over `Data` (UTF-8) or an
    /// `NSAttributedString` instead — accepting only `String` silently
    /// dropped those (2026-09-25, finding 6).
    private static func loadPlainText(_ provider: NSItemProvider) async -> String? {
        guard provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) else { return nil }
        guard let item = try? await provider.loadItem(forTypeIdentifier: UTType.plainText.identifier) else { return nil }
        if let text = item as? String { return text }
        if let data = item as? Data { return String(data: data, encoding: .utf8) }
        if let attributed = item as? NSAttributedString { return attributed.string }
        return nil
    }
}
