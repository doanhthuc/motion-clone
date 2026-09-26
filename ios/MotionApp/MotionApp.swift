import SwiftUI
import MotionKit
import UserNotifications

@main
struct MotionApp: App {
    @UIApplicationDelegateAdaptor private var appDelegate: AppDelegate
    @State private var model = AppModel()
    var body: some Scene {
        WindowGroup {
            RootView().environment(model).preferredColorScheme(.dark).dismissesKeyboardOnOutsideTap()
                .task { appDelegate.openMaterials = { [model] in model.openMaterials() } }
        }
    }
}

/// Receives taps on the share extension's banners (they are posted under the
/// app's identity). Set as the center's delegate at launch, before SwiftUI
/// builds a view, so a tap that cold-launches the app is not missed — it is
/// parked in `pendingMaterials` until `openMaterials` is wired up.
@MainActor
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    private var pendingMaterials = false
    var openMaterials: (() -> Void)? {
        didSet {
            guard pendingMaterials, let openMaterials else { return }
            pendingMaterials = false
            openMaterials()
        }
    }

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            didReceive response: UNNotificationResponse) async {
        let route = response.notification.request.content.userInfo[ShareImportNotice.routeKey] as? String
        guard route == ShareImportNotice.materialsRoute else { return }
        await MainActor.run {
            if let openMaterials { openMaterials() } else { pendingMaterials = true }
        }
    }

    /// Sharing happens from another app, but a share finishing after the user
    /// has switched to Motion should still show its banner.
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter,
                                            willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .list]
    }
}

enum AppTab: Hashable { case runs, materials, newJob, outputs, pod }

/// Which top-level space `SpaceShell` is showing.
enum AppSpace: String { case motion, studio }

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
    private(set) var studio: StudioStore?
    var selectedSpace: AppSpace = AppSpace(rawValue: UserDefaults.standard.string(forKey: "selectedSpace") ?? "") ?? .motion {
        didSet { UserDefaults.standard.set(selectedSpace.rawValue, forKey: "selectedSpace") }
    }
    var isSidebarOpen = false
    /// Motion tabs whose NavigationStack currently shows its root screen
    /// (`motionTabRoot`). The sidebar's edge swipe is only live on those.
    var motionTabsAtRoot: Set<AppTab> = []
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
            studio = nil
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
        studio = StudioStore(client: client)
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

    /// Switches to Studio and opens (or creates, when `projectID` is nil) a project.
    func openStudio(projectID: String?) async {
        guard let studio else { return }
        if let projectID { await studio.open(projectID) } else { _ = await studio.createProject() }
        selectedSpace = .studio
        isSidebarOpen = false
    }

    func resumeMaterialsUpload() {
        guard materialResumeTask == nil, let materials else { return }
        materialResumeTask = Task { [weak self] in
            await materials.resumePendingUpload()
            self?.materialResumeTask = nil
        }
    }

    /// A tap on a share-extension banner: show the list the video landed in.
    func openMaterials() {
        selectedSpace = .motion
        isSidebarOpen = false
        selectedTab = .materials
        refreshMaterials()
    }

    /// The share extension reports through banners only if the app has been
    /// granted them; iOS asks once and remembers the answer.
    /// Skipped under UI tests: the system alert would sit over the screens they tap.
    func requestNotificationPermission() {
        let args = ProcessInfo.processInfo.arguments
        guard !Self.isUITestRecording, !args.contains("-UITestNoNotificationPrompt") else { return }
        Task { _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) }
    }

    /// A video shared to the Motion extension lands on the server while the
    /// app is in the background; re-reading on return puts it in the list.
    func refreshMaterials() {
        guard let materials else { return }
        Task { await materials.refresh() }
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
