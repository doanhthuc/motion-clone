import AppIntents
import MotionKit

/// "Save TikTok to Motion" in Shortcuts: the same `POST /v1/materials/link`
/// as the share extension, run in the app's own process in the background.
///
/// It exists for the Dynamic Island. The share extension cannot start a Live
/// Activity (`unsupportedTarget`, measured on device 2026-09-26), but a
/// shortcut built on this action — "Receive URLs from Share Sheet" → this —
/// gets Shortcuts' own progress ring in the island while it runs.
///
/// The outcome is a banner, like the extension's, not a result dialog: a
/// dialog is a popup with a Done button that has to be dismissed, which is
/// what the user asked to lose (2026-09-26). Failures are banners too, not
/// thrown errors, for the same reason.
struct SaveTikTokIntent: AppIntent {
    static let title: LocalizedStringResource = "Save TikTok to Motion"
    static let description = IntentDescription(
        "Downloads a TikTok video into Motion's Materials.",
        categoryName: "Materials")
    static let supportedModes: IntentModes = .background

    /// Text, not URL: TikTok shares a URL, other apps share prose around one,
    /// and Shortcuts turns either into text — `SharedLink` finds the link.
    @Parameter(title: "Link", inputOptions: String.IntentInputOptions(keyboardType: .URL))
    var link: String

    static var parameterSummary: some ParameterSummary {
        Summary("Save \(\.$link) to Motion")
    }

    @MainActor
    func perform() async throws -> some IntentResult {
        let notifier = ImportNotifier()
        guard let found = SharedLink.tiktok(in: [link]) else {
            await notifier.postAndWait(.failed("There is no TikTok link in what was shared."))
            return .result()
        }
        let vault = CredentialVault(storage: KeychainStorage())
        vault.seedIfEmpty(from: Bundle.main.infoDictionary ?? [:])
        guard let credentials = vault.load() else {
            await notifier.postAndWait(.failed("Open Motion and fill in Settings first."))
            return .result()
        }
        let store = MaterialsStore(client: APIClient(credentials: credentials))
        if let material = await store.importLink(found) {
            await notifier.postAndWait(.done(durationS: store.lastLinkProbe?.durationS),
                                       thumbnail: await store.thumbnail(for: material))
        } else if store.linkImportStillRunning {
            await notifier.postAndWait(.stillRunning)
        } else {
            await notifier.postAndWait(.failed(store.errorMessage ?? "The download failed."))
        }
        return .result()
    }
}
