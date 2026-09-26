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

    // Dismiss first: `openStudio` flips `selectedSpace` to `.studio`, and `SpaceShell`
    // then tears down the Motion subtree that owns this sheet's presentation, so
    // dismissing after that races with the teardown. `model` is captured into a local
    // so the Task never reaches back into sheet-owned state once the sheet is gone.
    // Attach still runs after `openStudio` returns: it may call `createProject()`,
    // which clears `attachments`, while `open()` clears them only on a project switch —
    // attaching after either has settled is the only ordering that survives both.
    private func go(_ projectID: String?) {
        dismiss()
        let model = model
        Task {
            await model.openStudio(projectID: projectID)
            model.studio?.attach(ref)
        }
    }
}
