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

    private var eligible: [MotionKit.Material] {
        materials.materials.filter(kind.accepts)
    }

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    header
                    content
                }
                .padding(.horizontal, 20)
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
        }
        .task {
            if !materials.loaded {
                await materials.refresh()
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(displayName(role))
                .font(Theme.sans(22, .bold))
                .foregroundStyle(Theme.ink)
            Text(kindDescription)
                .font(Theme.sans(13))
                .foregroundStyle(Theme.ink2)
        }
        .padding(.top, 6)
    }

    @ViewBuilder private var content: some View {
        if kind == .unknown {
            ContentUnavailableView {
                Label("Unsupported material type", systemImage: "questionmark.square.dashed")
            } description: {
                Text("This role uses a material type this version of Motion does not recognize. Update the app before assigning one.")
            }
            .foregroundStyle(Theme.ink2)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 48)
        } else if !materials.loaded {
            ProgressView("Loading materials…")
                .frame(maxWidth: .infinity)
                .padding(.vertical, 48)
        } else if eligible.isEmpty {
            ContentUnavailableView {
                Label("No matching materials", systemImage: "photo.on.rectangle.angled")
            } description: {
                Text("Add \(kindDescriptionForEmptyState) in Material, then choose it here.")
            }
            .foregroundStyle(Theme.ink2)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 48)
        } else {
            LazyVGrid(columns: columns, spacing: 12) {
                ForEach(eligible) { material in
                    MaterialChoice(material: material, materials: materials, selected: material.id == selectedID) {
                        onSelect(material.id)
                        dismiss()
                    }
                }
            }
        }
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

@MainActor
private struct MaterialChoice: View {
    let material: MotionKit.Material
    let materials: MaterialsStore
    let selected: Bool
    let onSelect: () -> Void
    @State private var thumbnail: Data?

    var body: some View {
        Button(action: onSelect) {
            MaterialCard(
                material: material,
                warning: materials.warning(for: material.id),
                thumbnail: thumbnail)
                .overlay(alignment: .topTrailing) {
                    if selected {
                        Image(systemName: "checkmark.circle.fill")
                            .font(.system(size: 20, weight: .bold))
                            .foregroundStyle(Theme.lime)
                            .padding(8)
                        }
                }
                .overlay(RoundedRectangle(cornerRadius: 15).strokeBorder(selected ? Theme.limeLine : Theme.line))
        }
        .buttonStyle(.plain)
        .accessibilityLabel(material.name)
        .accessibilityValue(selected ? "Selected" : "Not selected")
        .task(id: material.id) {
            thumbnail = await materials.thumbnail(for: material)
        }
    }
}
