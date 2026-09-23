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
               let outputs = model.outputs, let flow = model.runFlow {
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
                }
                .tint(Theme.lime)
                .safeAreaInset(edge: .top) { SpendBanner(flow: flow) }
                .onChange(of: flow.podRequested) { _, requested in
                    guard requested else { return }
                    model.selectedTab = .runs
                    Task { await pod.refresh() }
                    flow.acknowledgePodRequest()
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
            }
        }
    }
}

/// Visible on every tab while a spend is outstanding or being re-checked.
struct SpendBanner: View {
    let flow: RunFlow
    var body: some View {
        if let text = flow.pendingNotice ?? flow.inFlightLabel.map({ "Sending: \($0)" }) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small).tint(Theme.lime)
                VStack(alignment: .leading, spacing: 2) {
                    Text(text).font(Theme.sans(13, .semibold)).foregroundStyle(Theme.ink1)
                    if let note = flow.retryNote {
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
