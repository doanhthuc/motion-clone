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
    private(set) var outputs: OutputsStore?

    init() {
        vault.seedIfEmpty(from: Bundle.main.infoDictionary ?? [:])
        reconnect()
    }

    func reconnect() {
        guard let credentials = vault.load() else {
            client = nil; runs = nil; pod = nil; outputs = nil
            return
        }
        let client = APIClient(credentials: credentials)
        self.client = client
        runs = RunsStore(client: client)
        pod = PodStore(client: client)
        outputs = OutputsStore(client: client)
    }
}
