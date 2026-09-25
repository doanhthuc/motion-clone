import MotionKit
import SwiftUI

/// Material tab: uploaded materials, or saved try-ons (Phase 6 spec §6).
/// The switch sits between the navigation title and the grid. It lives here,
/// not inside each child's `ScrollView`: placed as a child's first row it
/// stopped switching (taps landed, the selection never changed; 2026-09-25).
struct MaterialTabView: View {
    let materials: MaterialsStore
    let library: TryonLibraryStore
    let draft: DraftStore
    let composer: BatchComposer
    @State private var showSaved = false

    var body: some View {
        VStack(spacing: 0) {
            MaterialModePicker(showSaved: $showSaved)
                .padding(.horizontal, 16).padding(.bottom, 8)
            if showSaved {
                SavedTryonsView(library: library, materials: materials, draft: draft,
                                composer: composer)
            } else {
                MaterialsView(store: materials)
            }
        }
        .background(Theme.bg)
        .navigationTitle(showSaved ? "Saved try-ons" : "Materials")
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
