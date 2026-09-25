import SwiftUI
import MotionKit

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        @Bindable var model = model
        Group {
            if let runs = model.runs, let pod = model.pod,
               let materials = model.materials, let draft = model.draft,
               let outputs = model.outputs, let flow = model.runFlow,
               let gpu = model.gpu, let balance = model.balance, let migrate = model.migrate,
               let library = model.tryonLibrary, let composer = model.batchComposer {
                TabView(selection: $model.selectedTab) {
                    Tab("Runs", systemImage: "waveform.path.ecg", value: AppTab.runs) {
                        NavigationStack { RunsView(runs: runs, pod: pod, flow: flow) }
                    }
                    Tab("Materials", systemImage: "photo.on.rectangle", value: AppTab.materials) {
                        NavigationStack {
                            MaterialTabView(materials: materials, library: library, draft: draft,
                                            composer: composer)
                        }
                    }
                    Tab("New Job", systemImage: "plus.circle", value: AppTab.newJob) {
                        NavigationStack {
                            NewJobView(store: draft, materials: materials, flow: flow,
                                       composer: composer, library: library)
                        }
                    }
                    Tab("Outputs", systemImage: "play.rectangle", value: AppTab.outputs) {
                        NavigationStack { OutputsView(store: outputs) }
                    }
                    Tab("Pod", systemImage: "cpu", value: AppTab.pod) {
                        NavigationStack { PodView(pod: pod, gpu: gpu, balance: balance, flow: flow, runs: runs) }
                    }
                }
                .tint(Theme.accent)
                // Sentence-case section headers everywhere: the uppercase
                // tracked labels were the loudest "template" tell in the audit.
                .textCase(nil)
                .safeAreaInset(edge: .top) {
                    VStack(spacing: 8) {
                        KillBanner(pod: pod)
                        SpendBanner(flow: flow, migrate: migrate)
                    }
                }
                .onChange(of: flow.podRequested) { _, requested in
                    guard requested else { return }
                    model.selectedTab = .pod
                    Task { await pod.refresh() }
                    flow.acknowledgePodRequest()
                }
                .sheet(item: $model.migrateSheet) { request in
                    MigrateSheet(request: request, flow: migrate, gpu: gpu, pod: pod,
                                 runStatus: runs.live?.status, spendBlocked: !flow.canSpend)
                }
                .overlay(alignment: .bottomLeading) {
                    if AppModel.isUITestRecording {
                        Color.clear.frame(width: 1, height: 1)
                            .accessibilityElement()
                            .accessibilityLabel("\(model.recordedSpends)")
                            .accessibilityIdentifier("uitest.recordedSpends")
                    }
                }
            } else {
                NavigationStack { SettingsView(firstRun: true) }
            }
        }
        .background(Theme.bg)
        .task { model.resumeMaterialsUpload() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                model.resumeMaterialsUpload()
                model.replayPendingSpend()
                if let pod = model.pod { Task { await pod.refresh() } }
            }
        }
    }
}

/// Visible on every tab while a spend or migrate is outstanding or re-checked.
struct SpendBanner: View {
    @Environment(AppModel.self) private var model
    let flow: RunFlow
    let migrate: MigrateFlow
    var body: some View {
        if let text = flow.pendingNotice ?? migrate.pendingNotice
            ?? (flow.inFlightLabel ?? migrate.inFlightLabel).map({ "Sending: \($0)" }) {
            HStack(spacing: 10) {
                ProgressView()
                VStack(alignment: .leading, spacing: 2) {
                    Text(text).font(.subheadline.weight(.semibold))
                    if let note = flow.retryNote ?? migrate.retryNote {
                        Text(note).font(.footnote).foregroundStyle(Theme.warning)
                    }
                }
                Spacer(minLength: 0)
            }
            .heroSurface()
            .padding(.horizontal, 16)
        } else if migrate.needsRecheck {
            // Every Phase 4 spend is refused (.notSent) until this is answered,
            // and nothing else outside the migrate sheet says why.
            HStack(spacing: 10) {
                Label("Migrate unanswered — the volume move may or may not have started.",
                      systemImage: "exclamationmark.triangle.fill")
                    .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.warning)
                Spacer(minLength: 0)
                Button("Check again") {
                    model.migrateSheet = MigrateRequest(destination: nil)
                    model.selectedTab = .pod
                }
                .font(.subheadline.weight(.semibold))
                .accessibilityIdentifier("banner.migrateCheckAgain")
            }
            .heroSurface()
            .padding(.horizontal, 16)
        }
    }
}
