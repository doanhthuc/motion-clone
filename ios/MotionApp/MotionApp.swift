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

enum AppTab: Hashable { case runs, materials, newJob, outputs }

/// Owns the vault and one set of stores per set of credentials. Saving new
/// credentials in Settings calls `reconnect()`, which rebuilds the stores.
@MainActor @Observable
final class AppModel {
    let vault = CredentialVault(storage: KeychainStorage())
    private(set) var client: APIClient?
    private(set) var runs: RunsStore?
    private(set) var pod: PodStore?
    private(set) var materials: MaterialsStore?
    private(set) var draft: DraftStore?
    private(set) var outputs: OutputsStore?
    private(set) var runFlow: RunFlow?
    var selectedTab: AppTab = .runs
    private var materialResumeTask: Task<Void, Never>?
    private var replayTask: Task<Void, Never>?

    init() {
        vault.seedIfEmpty(from: Bundle.main.infoDictionary ?? [:])
        reconnect()
    }

    func reconnect() {
        materialResumeTask?.cancel()
        materialResumeTask = nil
        guard let credentials = vault.load() else {
            client = nil; runs = nil; pod = nil; materials = nil; draft = nil; outputs = nil; runFlow = nil
            return
        }
        let client = APIClient(credentials: credentials)
        self.client = client
        runs = RunsStore(client: client)
        pod = PodStore(client: client)
        materials = MaterialsStore(client: client)
        draft = DraftStore(client: client)
        outputs = OutputsStore(client: client)
        let gate: any SpendSending = ProcessInfo.processInfo.arguments.contains("-UITestRecordingSpendGate")
            ? RecordingSpendGate()
            : SpendGate(client: client)
        runFlow = RunFlow(client: client, gate: gate)
        resumeMaterialsUpload()
    }

    func resumeMaterialsUpload() {
        guard materialResumeTask == nil, let materials else { return }
        materialResumeTask = Task { [weak self] in
            await materials.resumePendingUpload()
            self?.materialResumeTask = nil
        }
    }

    /// Once per launch (RunFlow guards it): resend an interrupted spend with
    /// its original key, or report it as too old to verify.
    func replayPendingSpend() {
        guard replayTask == nil, let runFlow else { return }
        replayTask = Task { await runFlow.replayPendingOnce() }
    }
}
