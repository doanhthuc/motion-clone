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
               let gpu = model.gpu, let balance = model.balance, let migrate = model.migrate {
                TabView(selection: $model.selectedTab) {
                    Tab("Runs", systemImage: "waveform.path.ecg", value: AppTab.runs) {
                        NavigationStack { RunsView(runs: runs, pod: pod, flow: flow) }
                    }
                    Tab("Material", systemImage: "square.grid.2x2", value: AppTab.materials) {
                        NavigationStack { MaterialsView(store: materials) }
                    }
                    Tab("New Job", systemImage: "plus.circle.fill", value: AppTab.newJob) {
                        NavigationStack { NewJobView(store: draft, materials: materials, flow: flow) }
                    }
                    Tab("Output", systemImage: "play.rectangle", value: AppTab.outputs) {
                        NavigationStack { OutputsView(store: outputs) }
                    }
                    Tab("Pod", systemImage: "cpu", value: AppTab.pod) {
                        NavigationStack { PodView(pod: pod, gpu: gpu, balance: balance, flow: flow, runs: runs) }
                    }
                }
                .tint(Theme.lime)
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
    let flow: RunFlow
    let migrate: MigrateFlow
    var body: some View {
        if let text = flow.pendingNotice ?? migrate.pendingNotice
            ?? (flow.inFlightLabel ?? migrate.inFlightLabel).map({ "Sending: \($0)" }) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small).tint(Theme.lime)
                VStack(alignment: .leading, spacing: 2) {
                    Text(text).font(Theme.sans(13, .semibold)).foregroundStyle(Theme.ink1)
                    if let note = flow.retryNote ?? migrate.retryNote {
                        Text(note).font(Theme.mono(11)).foregroundStyle(Theme.amber)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .card(border: Theme.limeLine)
            .padding(.horizontal, 16)
        }
    }
}
