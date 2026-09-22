import SwiftUI
import MotionKit

@main
struct MotionApp: App {
    @State private var model = AppModel()
    var body: some Scene {
        WindowGroup {
            RootView().environment(model).preferredColorScheme(.dark)
        }
    }
}

/// Owns the vault and one set of stores per set of credentials. Saving new
/// credentials in Settings calls `reconnect()`, which rebuilds the stores.
@MainActor @Observable
final class AppModel {
    let vault = CredentialVault(storage: KeychainStorage())
    private(set) var client: APIClient?
    private(set) var runs: RunsStore?
    private(set) var pod: PodStore?
    private(set) var materials: MaterialsStore?
    private(set) var outputs: OutputsStore?
    private var materialResumeTask: Task<Void, Never>?

    init() {
        vault.seedIfEmpty(from: Bundle.main.infoDictionary ?? [:])
        reconnect()
    }

    func reconnect() {
        materialResumeTask?.cancel()
        materialResumeTask = nil
        guard let credentials = vault.load() else {
            client = nil; runs = nil; pod = nil; materials = nil; outputs = nil
            return
        }
        let client = APIClient(credentials: credentials)
        self.client = client
        runs = RunsStore(client: client)
        pod = PodStore(client: client)
        materials = MaterialsStore(client: client)
        outputs = OutputsStore(client: client)
        resumeMaterialsUpload()
    }

    func resumeMaterialsUpload() {
        guard materialResumeTask == nil, let materials else { return }
        materialResumeTask = Task { [weak self] in
            await materials.resumePendingUpload()
            self?.materialResumeTask = nil
        }
    }
}
