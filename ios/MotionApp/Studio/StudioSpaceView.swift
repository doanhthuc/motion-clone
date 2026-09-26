import MotionKit
import SwiftUI

struct StudioSpaceView: View {
    @Environment(AppModel.self) private var model
    let studio: StudioStore
    @State private var viewing: Viewing?

    struct Viewing: Identifiable {
        let generation: StudioGeneration
        let imageID: String
        var id: String { imageID }
    }

    var body: some View {
        NavigationStack {
            Group {
                if let project = studio.project {
                    ScrollView {
                        if project.generations.isEmpty {
                            ContentUnavailableView("Nothing yet", systemImage: "sparkles",
                                                   description: Text("Describe an image, or tap + to add references."))
                                .padding(.top, 80)
                        }
                        StudioGrid(studio: studio) { gen, id in viewing = Viewing(generation: gen, imageID: id) }
                            .padding(.horizontal, 4)
                    }
                    .defaultScrollAnchor(.top)
                    .safeAreaInset(edge: .bottom) { StudioComposerSlot(studio: studio) }
                    .navigationTitle(StudioFormat.title(project.title, createdAt: project.createdAt))
                } else {
                    ContentUnavailableView {
                        Label("Image Studio", systemImage: "sparkles")
                    } description: {
                        Text("Generate and edit images with Nano Banana and Qwen.")
                    } actions: {
                        Button("New project") { Task { await model.openStudio(projectID: nil) } }
                            .buttonStyle(.borderedProminent)
                    }
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .sidebarButton()
            .background(Theme.bg)
            .alert("Studio", isPresented: Binding(get: { studio.message != nil }, set: { if !$0 { studio.message = nil } })) {
                Button("OK", role: .cancel) {}
            } message: { Text(studio.message ?? "") }
            .sheet(item: $viewing) { v in StudioImageViewer(studio: studio, generation: v.generation, imageID: v.imageID) }
            .task { await studio.loadCatalog() }
        }
    }
}

/// Task 9 replaces this with the real composer.
struct StudioComposerSlot: View {
    let studio: StudioStore
    var body: some View { Color.clear.frame(height: 0) }
}
