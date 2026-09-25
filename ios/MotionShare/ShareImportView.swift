import MotionKit
import SwiftUI

@MainActor @Observable
final class ShareImportModel {
    enum Phase: Equatable {
        case working
        case done(name: String)
        case failed(String)
    }

    private(set) var phase: Phase = .working
    var onFinish: () -> Void = {}

    /// Close is offered while working and after a failure; success dismisses itself.
    var isDone: Bool {
        if case .done = phase { return true }
        return false
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
        if let material = await store.importLink(link) {
            phase = .done(name: material.name)
            try? await Task.sleep(for: .seconds(1.5))
            onFinish()
        } else {
            phase = .failed(store.errorMessage ?? "The download failed.")
        }
    }
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
            }
            if !model.isDone {
                Button("Close", action: model.onFinish).buttonStyle(.bordered)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(.systemBackground))
    }
}
