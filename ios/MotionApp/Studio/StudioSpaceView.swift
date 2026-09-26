import MotionKit
import SwiftUI

struct StudioSpaceView: View {
    @Environment(AppModel.self) private var model
    let studio: StudioStore
    @State private var viewing: Viewing?
    /// Which reference is tapped open for the enlarged preview + ⓧ. Owned
    /// here, not by the composer: the backdrop dim below has to cover the
    /// grid, and a dim drawn from inside the pinned composer can't reliably
    /// reach past its own bounds.
    @State private var peeking: StudioRef?

    struct Viewing: Identifiable {
        let generation: StudioGeneration
        let imageID: String
        var id: String { imageID }
    }

    private var isActive: Bool { model.selectedSpace == .studio }

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
                    .scrollDismissesKeyboard(.immediately)
                    .overlay {
                        // Attached before `safeAreaInset` below, so it fills the
                        // ScrollView's own bounds (the grid) while the pinned
                        // composer, added after, still renders on top of it
                        // undimmed.
                        if peeking != nil {
                            Color.black.opacity(0.5)
                                .ignoresSafeArea()
                                .onTapGesture { withAnimation(.snappy) { peeking = nil } }
                                .transition(.opacity)
                        }
                    }
                    .safeAreaInset(edge: .bottom) { StudioComposer(studio: studio, peeking: $peeking) }
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
            // `SpaceShell` keeps this view mounted while Motion is showing, so
            // the alert and the catalog load are gated on Studio being the
            // visible space: a failed Studio call must not pop over Motion.
            .alert("Studio", isPresented: Binding(get: { isActive && studio.message != nil },
                                                  set: { if !$0 { studio.message = nil } })) {
                Button("OK", role: .cancel) {}
            } message: { Text(studio.message ?? "") }
            .sheet(item: $viewing) { v in StudioImageViewer(studio: studio, generation: v.generation, imageID: v.imageID) }
            .task(id: isActive) { if isActive { await studio.loadCatalog() } }
        }
    }
}
