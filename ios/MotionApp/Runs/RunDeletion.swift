import MotionKit
import SwiftUI

/// A run the user asked to delete, with what the confirm needs to say.
struct RunDeleteTarget: Identifiable {
    let id: String
    let title: String
    let videos: Int
}

extension View {
    /// The confirm for deleting a run: keep its videos in Outputs, or delete
    /// them too. Nothing is sent before a choice; a refusal (the run is
    /// running or has a pod) comes back as an alert, and the run stays.
    func runDeletion(_ target: Binding<RunDeleteTarget?>, onDeleted: @escaping () -> Void = {}) -> some View {
        modifier(RunDeletion(target: target, onDeleted: onDeleted))
    }
}

private struct RunDeletion: ViewModifier {
    @Binding var target: RunDeleteTarget?
    let onDeleted: () -> Void
    @Environment(AppModel.self) private var model
    @State private var error: APIError?

    func body(content: Content) -> some View {
        content
            .confirmationDialog("Delete \(target?.title ?? "run")?",
                                isPresented: Binding { target != nil } set: { if !$0 { target = nil } },
                                titleVisibility: .visible, presenting: target) { t in
                if t.videos > 0 {
                    Button("Delete run, keep videos", role: .destructive) { delete(t, withVideos: false) }
                        .accessibilityIdentifier("run.delete.keep")
                    Button("Delete run and \(t.videos) video\(t.videos == 1 ? "" : "s")", role: .destructive) {
                        delete(t, withVideos: true)
                    }
                    .accessibilityIdentifier("run.delete.all")
                } else {
                    Button("Delete run", role: .destructive) { delete(t, withVideos: true) }
                        .accessibilityIdentifier("run.delete.all")
                }
                Button("Cancel", role: .cancel) {}
            } message: { t in
                Text(t.videos > 0
                     ? "Its jobs, progress and try-on images go. Its videos can stay in Outputs."
                     : "Its jobs, progress and try-on images go. Materials and saved try-ons stay.")
            }
            .alert("Couldn't delete", isPresented: Binding { error != nil } set: { if !$0 { error = nil } },
                   presenting: error) { _ in
                Button("OK", role: .cancel) {}
            } message: { Text($0.userMessage) }
    }

    private func delete(_ t: RunDeleteTarget, withVideos: Bool) {
        Task {
            guard let runs = model.runs else { return }
            do throws(APIError) {
                let result = try await runs.delete(t.id, withVideos: withVideos)
                if result.videosDeleted > 0 { await model.outputs?.refresh() }
                onDeleted()
            } catch {
                self.error = error
            }
        }
    }
}
