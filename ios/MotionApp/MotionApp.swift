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

enum AppTab: Hashable { case runs, materials, newJob, outputs, pod }

/// How many jobs the draft stands for: `.single` is the one job being edited,
/// `.batch` is a cross build of many.
enum NewJobMode: Hashable { case single, batch }

/// Opens the migrate sheet (RootView presents it over every tab).
struct MigrateRequest: Identifiable, Equatable {
    let id = UUID()
    let destination: String?
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
    private(set) var draft: DraftStore?
    private(set) var outputs: OutputsStore?
    private(set) var runFlow: RunFlow?
    private(set) var gpu: GpuStore?
    private(set) var balance: BalanceStore?
    private(set) var migrate: MigrateFlow?
    private(set) var tryonLibrary: TryonLibraryStore?
    private(set) var batchComposer: BatchComposer?
    var migrateSheet: MigrateRequest?
    /// UI-test builds only: how many spend taps the recording gate swallowed.
    private(set) var recordedSpends = 0
    private var spendGate: (any SpendSending)?
    static let isUITestRecording = ProcessInfo.processInfo.arguments.contains("-UITestRecordingSpendGate")
    var selectedTab: AppTab = .runs
    /// Views write this directly: adopting a saved try-on sets `.single` and
    /// moves to the New Job tab.
    var newJobMode: NewJobMode = .single
    private var materialResumeTask: Task<Void, Never>?
    private var replayTask: Task<Void, Never>?

    init() {
        vault.seedIfEmpty(from: Bundle.main.infoDictionary ?? [:])
        reconnect()
    }

    /// Refuses to rebuild while a spend or migrate is outstanding — replacing
    /// the store would strand its result on an unobserved instance.
    @discardableResult
    func reconnect() -> Bool {
        if runFlow?.isSpending == true || runFlow?.pendingNotice != nil { return false }
        if migrate?.isSending == true || migrate?.pendingNotice != nil { return false }
        materialResumeTask?.cancel()
        materialResumeTask = nil
        guard let credentials = vault.load() else {
            client = nil; runs = nil; pod = nil; materials = nil; draft = nil; outputs = nil; runFlow = nil
            gpu = nil; balance = nil; migrate = nil; tryonLibrary = nil; batchComposer = nil; spendGate = nil
            return true
        }
        let client = APIClient(credentials: credentials)
        self.client = client
        runs = RunsStore(client: client)
        let pod = PodStore(client: client)
        self.pod = pod
        materials = MaterialsStore(client: client)
        let draft = DraftStore(client: client)
        self.draft = draft
        let library = TryonLibraryStore(client: client, draft: draft)
        tryonLibrary = library
        batchComposer = BatchComposer(draft: draft, library: library)
        outputs = OutputsStore(client: client)
        gpu = GpuStore(client: client, pod: pod)
        balance = BalanceStore(client: client)
        let gate: any SpendSending = Self.isUITestRecording
            ? RecordingSpendGate { [weak self] count in
                Task { @MainActor in self?.recordedSpends = count }
            }
            : SpendGate(client: client)
        spendGate = gate
        // A local `let`, like `pod` and `draft` above: `self.runFlow` is a
        // `RunFlow?`, and `MigrateFlow` takes the dependency non-optional so an
        // absent one cannot silently no-op its drop guard.
        let runFlow = RunFlow(client: client, gate: gate)
        self.runFlow = runFlow
        migrate = MigrateFlow(client: client, gate: gate, pod: pod, runFlow: runFlow)
        replayTask = nil
        replayPendingSpend()
        resumeMaterialsUpload()
        return true
    }

    func resumeMaterialsUpload() {
        guard materialResumeTask == nil, let materials else { return }
        materialResumeTask = Task { [weak self] in
            await materials.resumePendingUpload()
            self?.materialResumeTask = nil
        }
    }

    /// Once per launch (each flow guards it): resend an interrupted spend with
    /// its original key. The one ledger entry belongs to whichever flow sent it.
    func replayPendingSpend() {
        guard replayTask == nil, let runFlow, let migrate, let spendGate else { return }
        replayTask = Task {
            if await spendGate.pending()?.intent.kind == .migrate {
                await migrate.replayPendingOnce()
            } else {
                await runFlow.replayPendingOnce()
            }
        }
    }
}
