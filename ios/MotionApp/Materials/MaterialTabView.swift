import MotionKit
import SwiftUI

/// Material tab: uploaded materials, or saved try-ons (Phase 6 spec §6).
struct MaterialTabView: View {
    let materials: MaterialsStore
    let library: TryonLibraryStore
    let draft: DraftStore
    let composer: BatchComposer
    @State private var showSaved = false

    var body: some View {
        VStack(spacing: 0) {
            Picker("Show", selection: $showSaved) {
                Text("Materials").tag(false)
                Text("Saved try-ons").tag(true)
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("material.mode")
            .padding(.horizontal, 20).padding(.vertical, 8)
            if showSaved {
                SavedTryonsView(library: library, materials: materials, draft: draft,
                                composer: composer)
            } else {
                MaterialsView(store: materials)
            }
        }
        .background(Theme.bg)
    }
}
