import MotionKit
import SwiftUI

/// Pick a project (or make one), attach `ref` to its composer, switch to Studio.
struct EditInStudioSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let ref: StudioRef

    var body: some View {
        NavigationStack {
            List {
                Button { go(nil) } label: { Label("New project", systemImage: "plus") }
                if let studio = model.studio {
                    ForEach(studio.projects) { p in
                        Button(StudioFormat.title(p.title, createdAt: p.createdAt)) { go(p.id) }
                    }
                }
            }
            .navigationTitle("Edit in Studio").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Cancel") { dismiss() } } }
            .task { await model.studio?.loadProjects() }
        }
    }

    // `openStudio` may call `createProject()`, which clears `attachments`; `open()`
    // clears them only when the project changes. Attaching after it returns is the
    // only ordering that survives both paths.
    private func go(_ projectID: String?) {
        Task {
            await model.openStudio(projectID: projectID)
            model.studio?.attach(ref)
            dismiss()
        }
    }
}
