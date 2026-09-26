import MotionKit
import SwiftUI

@MainActor
struct MaterialPicker: View {
    let role: String
    let kind: PipelineRoleKind
    let selectedID: String?
    let materials: MaterialsStore
    let onSelect: (String?) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var deleteCandidate: MotionKit.Material?

    /// This role's own materials, then unsorted ones. A material filed under
    /// another role is moved from the Materials tab, not picked across roles.
    private var eligible: [MotionKit.Material] {
        MaterialRole.eligible(materials.materials.filter(kind.accepts), for: MaterialRole(rawValue: role))
    }

    private let columns = [
        GridItem(.flexible(), spacing: 12, alignment: .top),
        GridItem(.flexible(), spacing: 12, alignment: .top),
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header
                    if kind != .unknown {
                        MaterialImportBar(kind: kind, role: MaterialRole(rawValue: role),
                                          materials: materials) { material in
                            onSelect(material.id)
                            dismiss()
                        }
                    }
                    if let message = materials.errorMessage, materials.loaded {
                        refreshFailureBanner(message)
                    }
                    content
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 24)
            }
            .background(Theme.bg)
            .navigationTitle("Choose material")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if selectedID != nil {
                        Button("Clear", role: .destructive) {
                            onSelect(nil)
                            dismiss()
                        }
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            // An upload or download started here finishes by selecting its
            // material; a swipe-down mid-way would select it behind a closed sheet.
            .interactiveDismissDisabled(materials.isUploading || materials.isImportingLink)
            .modifier(MaterialDeleteDialog(candidate: $deleteCandidate, store: materials) { deleted in
                // The slot would otherwise point at a file that is gone.
                if deleted.id == selectedID { onSelect(nil) }
            })
        }
        .task {
            if !materials.loaded {
                await materials.refresh()
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(displayName(role))
                .font(.title2.bold())
            Text(kindDescription)
                .font(.subheadline)
                .foregroundStyle(Theme.secondary)
        }
        .padding(.top, 8)
    }

    @ViewBuilder private var content: some View {
        if kind == .unknown {
            ContentUnavailableView {
                Label("Unsupported material type", systemImage: "questionmark.square.dashed")
            } description: {
                Text("This role uses a material type this version of Motion does not recognize. Update the app before assigning one.")
            }
            .foregroundStyle(Theme.secondary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 48)
        } else if !materials.loaded, let message = materials.errorMessage {
            initialLoadFailure(message)
        } else if !materials.loaded {
            LoadingBlock(title: "Loading materials…")
                .padding(.vertical, 48)
        } else if eligible.isEmpty {
            ContentUnavailableView {
                Label("No matching materials", systemImage: "photo.on.rectangle.angled")
            } description: {
                Text(kind == .video
                     ? "Import a video from your library or paste a TikTok link above."
                     : "Import \(kindDescriptionForEmptyState) from your library above.")
            }
            .foregroundStyle(Theme.secondary)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 48)
        } else {
            LazyVGrid(columns: columns, spacing: 20) {
                ForEach(eligible) { material in
                    MaterialChoice(material: material, materials: materials, selected: material.id == selectedID,
                                   onDelete: { deleteCandidate = material }) {
                        onSelect(material.id)
                        dismiss()
                    }
                }
            }
        }
    }

    private func initialLoadFailure(_ message: String) -> some View {
        ContentUnavailableView {
            Label("Materials unavailable", systemImage: "exclamationmark.triangle")
        } description: {
            Text(message)
        } actions: {
            retryButton
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
    }

    private func refreshFailureBanner(_ message: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.warning)
            Text(message).font(.subheadline)
            Spacer(minLength: 0)
            retryButton
        }
        .heroSurface()
    }

    private var retryButton: some View {
        Button("Retry") {
            Task { await materials.refresh() }
        }
        .font(.subheadline.weight(.semibold))
    }

    private var kindDescription: String {
        switch kind {
        case .image: "Choose an image for this role."
        case .video: "Choose a video for this role."
        case .unknown: "This role has an unknown material type."
        }
    }

    private var kindDescriptionForEmptyState: String {
        switch kind {
        case .image: "an image"
        case .video: "a video"
        case .unknown: "a compatible material"
        }
    }

    private func displayName(_ value: String) -> String {
        value.replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
    }
}
