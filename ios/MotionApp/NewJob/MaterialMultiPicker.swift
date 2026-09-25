import MotionKit
import SwiftUI

/// Multi-select of outfit or driver materials as a photo grid: a batch is
/// chosen by what the clothes and the moves look like, and a list of file
/// names like `IMG_6710.mp4` answered neither.
///
/// Tiles are never pre-disabled against `BatchComposer.maxJobs`: the real
/// ceiling moves with the other axis (`maxJobs / drivers`), so the composer's
/// toggle is the single source of truth, and its refusal (`note`) is shown here
/// as well as under the strips.
@MainActor
struct MaterialMultiPicker: View {
    let title: String
    let kind: PipelineRoleKind
    let materials: MaterialsStore
    /// `outfit.pick` / `driver.pick` — the UI smokes find tiles by this prefix.
    let identifierPrefix: String
    let disabled: Bool
    let note: String?
    let isChosen: (String) -> Bool
    let toggle: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    private let columns = [
        GridItem(.flexible(), spacing: 12, alignment: .top),
        GridItem(.flexible(), spacing: 12, alignment: .top),
        GridItem(.flexible(), spacing: 12, alignment: .top),
    ]

    private var eligible: [MotionKit.Material] { materials.materials.filter(kind.accepts) }
    private var chosenCount: Int { eligible.count { isChosen($0.id) } }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    MaterialImportBar(kind: kind, materials: materials) { material in
                        // A fresh import is almost always meant for this batch.
                        if !isChosen(material.id) { toggle(material.id) }
                    }
                    if let note {
                        Label(note, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote).foregroundStyle(Theme.warning)
                    }
                    grid
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .background(Theme.bg)
            .navigationTitle(title)
            .navigationSubtitle(chosenCount == 0 ? "" : "\(chosenCount) selected")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .interactiveDismissDisabled(materials.isUploading || materials.isImportingLink)
        }
        .task { if !materials.loaded { await materials.refresh() } }
    }

    @ViewBuilder private var grid: some View {
        if !materials.loaded {
            ProgressView("Loading materials…")
                .frame(maxWidth: .infinity)
                .padding(.vertical, 48)
        } else if eligible.isEmpty {
            ContentUnavailableView(
                "Nothing here yet",
                systemImage: kind == .video ? "film.stack" : "photo.stack",
                description: Text("Import from your library above."))
                .padding(.vertical, 32)
        } else {
            LazyVGrid(columns: columns, spacing: 16) {
                ForEach(eligible) { material in
                    MaterialChoice(material: material, materials: materials,
                                   selected: isChosen(material.id), compact: true) {
                        toggle(material.id)
                    }
                    .disabled(disabled)
                    .accessibilityIdentifier("\(identifierPrefix).\(material.id)")
                }
            }
        }
    }
}
