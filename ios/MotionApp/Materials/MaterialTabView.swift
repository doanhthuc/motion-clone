import MotionKit
import SwiftUI

/// Material tab: uploaded materials, or saved try-ons (Phase 6 spec §6).
/// The switch *is* the navigation bar's title: a large "Materials" title over
/// a segmented row cost two rows above the first thumbnail and repeated the
/// tab bar's label (2026-09-25). It lives here, not inside each child's
/// `ScrollView`: placed as a child's first row it stopped switching (taps
/// landed, the selection never changed; 2026-09-25).
struct MaterialTabView: View {
    let materials: MaterialsStore
    let library: TryonLibraryStore
    let draft: DraftStore
    let composer: BatchComposer
    @State private var showSaved = false
    /// Here, not in `MaterialsView`: a batch keeps uploading while the user
    /// switches to Saved try-ons and back.
    @State private var uploads: MaterialUploadQueue

    init(materials: MaterialsStore, library: TryonLibraryStore, draft: DraftStore,
         composer: BatchComposer) {
        self.materials = materials
        self.library = library
        self.draft = draft
        self.composer = composer
        _uploads = State(initialValue: MaterialUploadQueue(store: materials))
    }

    var body: some View {
        Group {
            if showSaved {
                SavedTryonsView(library: library, materials: materials, draft: draft,
                                composer: composer)
            } else {
                MaterialsView(store: materials, uploads: uploads)
            }
        }
        .background(Theme.bg)
        // Still set: it names the back button on pushed screens and the bar
        // for VoiceOver, while `.principal` draws the switch in its place.
        .navigationTitle(showSaved ? "Saved try-ons" : "Materials")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                MaterialModePicker(showSaved: $showSaved).fixedSize()
            }
        }
    }
}

/// Materials | Saved try-ons.
struct MaterialModePicker: View {
    @Binding var showSaved: Bool
    var body: some View {
        Picker("Show", selection: $showSaved) {
            Text("Materials").tag(false)
            Text("Saved try-ons").tag(true)
        }
        .pickerStyle(.segmented)
        .accessibilityIdentifier("material.mode")
    }
}
