import SwiftUI
import MotionKit

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            if let runs = model.runs, let pod = model.pod, let outputs = model.outputs {
                TabView {
                    Tab("Runs", systemImage: "waveform.path.ecg") {
                        NavigationStack { RunsView(runs: runs, pod: pod) }
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
    }
}

struct OutputsView: View {
    let store: OutputsStore
    var body: some View { Text("Outputs") }
}
