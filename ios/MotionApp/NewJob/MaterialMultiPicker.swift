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
    let role: MaterialRole
    let materials: MaterialsStore
    /// `outfit.pick` / `driver.pick` — the UI smokes find tiles by this prefix.
    let identifierPrefix: String
    let disabled: Bool
    let note: String?
    let isChosen: (String) -> Bool
    let toggle: (String) -> Void
    /// Set by a chained pick: a Next button walks on to the next empty card;
    /// Done still closes the chain.
    var onNext: (() -> Void)?
    /// A chain's progress, "Character ✓ → Outfit", under the title.
    var progress: String?
    @Environment(\.dismiss) private var dismiss
    @State private var deleteCandidate: MotionKit.Material?

    private let columns = [
        GridItem(.flexible(), spacing: 12, alignment: .top),
        GridItem(.flexible(), spacing: 12, alignment: .top),
        GridItem(.flexible(), spacing: 12, alignment: .top),
    ]

    private var eligible: [MotionKit.Material] {
        MaterialRole.eligible(materials.materials.filter(kind.accepts), for: role)
    }
    private var chosenCount: Int { eligible.count { isChosen($0.id) } }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    MaterialImportBar(kind: kind, role: role, materials: materials) { material in
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
            .navigationSubtitle([progress, chosenCount == 0 ? nil : "\(chosenCount) selected"]
                .compactMap { $0 }.joined(separator: " · "))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if let onNext {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Next", action: onNext).disabled(chosenCount == 0)
                    }
                    ToolbarItem(placement: .cancellationAction) { Button("Done") { dismiss() } }
                } else {
                    ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
                }
            }
            .interactiveDismissDisabled(materials.isUploading || materials.isImportingLink)
            .modifier(MaterialDeleteDialog(candidate: $deleteCandidate, store: materials) { deleted in
                if isChosen(deleted.id) { toggle(deleted.id) }
            })
        }
        .task { if !materials.loaded { await materials.refresh() } }
    }

    @ViewBuilder private var grid: some View {
        if !materials.loaded {
            LoadingBlock(title: "Loading materials…")
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
                                   selected: isChosen(material.id), compact: true,
                                   onDelete: { deleteCandidate = material }) {
                        toggle(material.id)
                    }
                    .disabled(disabled)
                    .accessibilityIdentifier("\(identifierPrefix).\(material.id)")
                }
            }
        }
    }
}
