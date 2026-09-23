import SwiftUI
import MotionKit

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Group {
            if let runs = model.runs, let pod = model.pod,
               let materials = model.materials, let draft = model.draft,
               let outputs = model.outputs {
                TabView {
                    Tab("Runs", systemImage: "waveform.path.ecg") {
                        NavigationStack { RunsView(runs: runs, pod: pod) }
                    }
                    Tab("Material", systemImage: "square.grid.2x2") {
                        NavigationStack { MaterialsView(store: materials) }
                    }
                    Tab("New Job", systemImage: "plus.circle.fill") {
                        NavigationStack { NewJobView(store: draft, materials: materials) }
                    }
                    Tab("Output", systemImage: "play.rectangle") {
                        NavigationStack { OutputsView(store: outputs) }
                    }
                }
                .tint(Theme.lime)
            } else {
                NavigationStack { SettingsView(firstRun: true) }
            }
        }
        .background(Theme.bg)
        .task { model.resumeMaterialsUpload() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { model.resumeMaterialsUpload() }
        }
    }
}
