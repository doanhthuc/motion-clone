import MotionKit
import SwiftUI

/// The runs the user asked to delete — one, or a selection — with what the
/// confirm needs to say.
struct RunDeleteTarget: Identifiable {
    let ids: [String]
    let title: String
    let videos: Int
    var id: String { ids.joined(separator: ",") }

    init(id: String, title: String, videos: Int) {
        self.init(ids: [id], title: title, videos: videos)
    }

    init(ids: [String], title: String, videos: Int) {
        self.ids = ids
        self.title = title
        self.videos = videos
    }
}

extension View {
    /// The confirm for deleting a run: keep its videos in Outputs, or delete
    /// them too. Nothing is sent before a choice; a refusal (the run is
    /// running or has a pod) comes back as an alert, and the run stays.
    /// `onDeleted` gets the ids the server refused, empty when all went.
    func runDeletion(_ target: Binding<RunDeleteTarget?>,
                     onDeleted: @escaping (_ failed: Set<String>) -> Void = { _ in }) -> some View {
        modifier(RunDeletion(target: target, onDeleted: onDeleted))
    }
}

private struct RunDeletion: ViewModifier {
    @Binding var target: RunDeleteTarget?
    let onDeleted: (Set<String>) -> Void
    @Environment(AppModel.self) private var model
    @State private var failure: String?

    func body(content: Content) -> some View {
        content
            .confirmationDialog("Delete \(target?.title ?? "run")?",
                                isPresented: Binding { target != nil } set: { if !$0 { target = nil } },
                                titleVisibility: .visible, presenting: target) { t in
                if t.videos > 0 {
                    Button("Delete \(runs(t)), keep videos", role: .destructive) { delete(t, withVideos: false) }
                        .accessibilityIdentifier("run.delete.keep")
                    Button("Delete \(runs(t)) and \(t.videos) video\(t.videos == 1 ? "" : "s")", role: .destructive) {
                        delete(t, withVideos: true)
                    }
                    .accessibilityIdentifier("run.delete.all")
                } else {
                    Button("Delete \(runs(t))", role: .destructive) { delete(t, withVideos: true) }
                        .accessibilityIdentifier("run.delete.all")
                }
                Button("Cancel", role: .cancel) {}
            } message: { t in
                let its = t.ids.count == 1 ? "Its" : "Their"
                Text(t.videos > 0
                     ? "\(its) jobs, progress and try-on images go. \(its) videos can stay in Outputs."
                     : "\(its) jobs, progress and try-on images go. Materials and saved try-ons stay.")
            }
            .alert("Couldn't delete", isPresented: Binding { failure != nil } set: { if !$0 { failure = nil } },
                   presenting: failure) { _ in
                Button("OK", role: .cancel) {}
            } message: { Text($0) }
    }

    private func runs(_ t: RunDeleteTarget) -> String {
        t.ids.count == 1 ? "run" : "\(t.ids.count) runs"
    }

    private func delete(_ t: RunDeleteTarget, withVideos: Bool) {
        Task {
            guard let runs = model.runs else { return }
            let result = await runs.delete(t.ids, withVideos: withVideos)
            if result.videosDeleted > 0 { await model.outputs?.refresh() }
            if let first = result.failed.first {
                // A single run keeps its old alert; a selection says how many stayed.
                failure = t.ids.count == 1 ? first.error.userMessage
                    : "\(result.failed.count) of \(t.ids.count) runs weren't deleted: \(first.error.userMessage)"
            }
            onDeleted(Set(result.failed.map(\.id)))
        }
    }
}
