import MotionKit
import os
import SwiftUI

@MainActor @Observable
final class ShareImportModel {
    enum Phase: Equatable {
        case working
        case done(name: String)
        case failed(String)
        /// The 524 "still running" case: the server kept the download going
        /// past Cloudflare's tunnel timeout. Not an error — the video will
        /// land in Materials on its own — so the card shows it as
        /// informational rather than a failure (finding 4, 2026-09-25).
        case notice(String)
    }

    private(set) var phase: Phase = .working
    var onFinish: () -> Void = {}

    // 2026-09-25: Close (tap during .working) and the success path (after the
    // 1.5 s auto-dismiss) both reach `finish()` — a user closing the card
    // right as the download completes could otherwise fire `onFinish` twice,
    // and `extensionContext.completeRequest` is documented to accept only one
    // call. `didFinish` makes every exit path idempotent.
    private var didFinish = false

    /// Close is offered while working and after a failure; success dismisses itself.
    var isDone: Bool {
        if case .done = phase { return true }
        return false
    }

    /// The one-shot exit: safe to call from Close and from the success path
    /// even if both fire, since only the first call runs `onFinish`.
    func finish() {
        guard !didFinish else { return }
        didFinish = true
        onFinish()
    }

    func start(candidates: [String]) async {
        guard let link = SharedLink.tiktok(in: candidates) else {
            phase = .failed("There is no TikTok link in what was shared.")
            return
        }
        let vault = CredentialVault(storage: KeychainStorage())
        vault.seedIfEmpty(from: Bundle.main.infoDictionary ?? [:])
        guard let credentials = vault.load() else {
            phase = .failed("Open Motion and fill in Settings first.")
            return
        }
        let store = MaterialsStore(client: APIClient(credentials: credentials))
        // Shortcuts-style when banners are allowed: close the sheet at once and
        // report through notifications. Without permission the card is the
        // only feedback left, so it stays up as before.
        //
        // No Dynamic Island here: Activity.request from this extension fails
        // with `unsupportedTarget` even with NSSupportsLiveActivities on every
        // target (measured on device, 2026-09-26) — only the app may start one.
        // The island path is the Shortcuts action (SaveTikTokIntent).
        if await ImportNotifier.canNotify() {
            await downloadInBackground(link, store: store)
            finish()
            return
        }
        switch await Self.importOutcome(link, store: store) {
        case .done(let material, _):
            phase = .done(name: material.name)
            try? await Task.sleep(for: .seconds(1.5))
            finish()
        case .stillRunning:
            phase = .notice(store.errorMessage ?? "The download is still running.")
        case .failed(let message):
            phase = .failed(message)
        }
    }

    private enum Outcome {
        case done(MotionKit.Material, thumbnail: Data?)
        case failed(String)
        case stillRunning
    }

    private static func importOutcome(_ link: String, store: MaterialsStore) async -> Outcome {
        if let material = await store.importLink(link) {
            return .done(material, thumbnail: await store.thumbnail(for: material))
        }
        if store.linkImportStillRunning { return .stillRunning }
        return .failed(store.errorMessage ?? "The download failed.")
    }

    /// Posts "downloading", then keeps the extension alive past
    /// `completeRequest` with an expiring activity until the server answers,
    /// and replaces the banner with the outcome. If iOS takes the time back
    /// first, the banner says the download is still running — true, since
    /// the server finishes it whether or not we are still listening.
    private func downloadInBackground(_ link: String, store: MaterialsStore) async {
        let notifier = ImportNotifier()
        // Awaited so a fast outcome cannot reach the center ahead of it.
        await notifier.postAndWait(.downloading)
        let started = ContinuousClock.now
        let finished = DispatchSemaphore(value: 0)
        let settled = OSAllocatedUnfairLock(initialState: false)
        Task {
            let outcome = await Self.importOutcome(link, store: store)
            settled.withLock { $0 = true }
            Self.log.info("share import answered after \(started.duration(to: .now), privacy: .public)")
            switch outcome {
            case .done(_, let thumbnail):
                let probe = store.lastLinkProbe
                await notifier.postAndWait(.done(durationS: probe?.durationS), thumbnail: thumbnail)
            case .failed(let message):
                await notifier.postAndWait(.failed(message))
            case .stillRunning:
                await notifier.postAndWait(.stillRunning)
            }
            finished.signal()
        }
        ProcessInfo.processInfo.performExpiringActivity(withReason: "Downloading a shared TikTok video") { expired in
            if expired {
                if !settled.withLock({ $0 }) { notifier.post(.stillRunning) }
                // Unblocks the waiting call below so the activity can end.
                finished.signal()
                return
            }
            finished.wait()
        }
    }

    private static let log = Logger(subsystem: "xyz.doanhthuc.motion.share", category: "import")
}

struct ShareImportView: View {
    let model: ShareImportModel

    var body: some View {
        VStack(spacing: 16) {
            switch model.phase {
            case .working:
                ProgressView().controlSize(.large)
                Text("Downloading to Materials…").font(.headline)
                Text("You can close this — the download continues.")
                    .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
            case .done(let name):
                Image(systemName: "checkmark.circle.fill").font(.system(size: 44)).foregroundStyle(.green)
                Text("Added to Materials").font(.headline)
                Text(name).font(.subheadline).foregroundStyle(.secondary).lineLimit(1)
            case .failed(let message):
                Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 44)).foregroundStyle(.orange)
                Text(message).font(.subheadline).multilineTextAlignment(.center)
            case .notice(let message):
                Image(systemName: "clock.fill").font(.system(size: 44)).foregroundStyle(.secondary)
                Text(message).font(.subheadline).multilineTextAlignment(.center)
            }
            if !model.isDone {
                Button("Close", action: model.finish).buttonStyle(.bordered)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
    }
}
