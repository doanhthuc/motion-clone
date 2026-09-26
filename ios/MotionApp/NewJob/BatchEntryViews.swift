import MotionKit
import SwiftUI

/// A queued batch job read at a glance: its materials as pictures and its
/// pipeline by name. Until 2026-09-26 a row was the job's digest over three
/// lines of `role: owner/file` text, and nothing on it opened the job or
/// showed what those files looked like.
@MainActor
struct BatchEntryRow: View {
    let index: Int
    let entry: DraftBatchEntry
    let pipeline: Pipeline?
    let materials: MaterialsStore
    let dropDisabled: Bool
    let onOpen: () -> Void
    let onDrop: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Job \(index)")
                        .font(.headline)
                    Text(BatchEntryText.subtitle(entry, pipeline: pipeline))
                        .font(.subheadline)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(2)
                }
                Spacer(minLength: 0)
                // Named "Drop" so the Phase 3 and 6 smokes still find it by label.
                Button(role: .destructive, action: onDrop) {
                    Image(systemName: "trash")
                        .font(.body)
                        .frame(width: 32, height: 32)
                        .contentShape(.rect)
                }
                .buttonStyle(.borderless)
                .tint(Theme.danger)
                .disabled(dropDisabled)
                .accessibilityLabel("Drop")
            }
            HStack(alignment: .top, spacing: 8) {
                ForEach(BatchEntryText.roles(entry, pipeline: pipeline), id: \.self) { role in
                    BatchEntryMaterialTile(role: role, materialID: entry.slots[role] ?? nil,
                                           kind: pipeline?.roles[role] ?? .unknown,
                                           materials: materials, width: 64,
                                           showsSeed: entry.tryonSeed != nil && role == "outfit")
                }
            }
        }
        .padding(.vertical, 4)
        // The tiles carry no button of their own here, so a tap anywhere but
        // Drop opens the job; a long press on a tile peeks at that material.
        .contentShape(.rect)
        .onTapGesture(perform: onOpen)
        .accessibilityAction(named: "Show details", onOpen)
    }
}

/// One material of a queued job. Long press peeks at it the way the material
/// pickers do — a still image close to screen width, a video already playing —
/// and "View full screen" opens the scrubbable preview.
@MainActor
struct BatchEntryMaterialTile: View {
    let role: String
    let materialID: String?
    let kind: PipelineRoleKind
    let materials: MaterialsStore
    /// Fixed in a batch row; nil fills the grid column in the detail sheet.
    var width: CGFloat?
    var showsSeed = false
    /// The file name under the role, where there is room for it.
    var showsName = false
    /// Off in a batch row, where a tap opens the job instead.
    var tapToPreview = false
    @State private var thumbnail: Data?
    @State private var previewing = false

    private var material: MotionKit.Material? {
        guard let materialID else { return nil }
        return materials.materials.first { $0.id == materialID }
    }

    private var title: String { SlotText(role: role, required: true, kind: kind, slot: nil).title }

    var body: some View {
        content
            .contextMenu {
                if material != nil {
                    Button("View full screen", systemImage: "arrow.up.left.and.arrow.down.right") {
                        previewing = true
                    }
                }
            } preview: {
                if let material {
                    MaterialPeek(material: material, materials: materials)
                }
            }
            .sheet(isPresented: $previewing) {
                if let material {
                    MaterialPreview(material: material, materials: materials, modal: true)
                }
            }
            .task(id: material?.id) {
                guard let material else {
                    thumbnail = nil
                    return
                }
                thumbnail = await materials.thumbnail(for: material)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(title)
            .accessibilityValue(material?.name ?? (materialID == nil ? "Empty" : "Missing"))
    }

    @ViewBuilder private var content: some View {
        if tapToPreview, material != nil {
            Button { previewing = true } label: { tile }.buttonStyle(.plain)
        } else {
            tile
        }
    }

    private var tile: some View {
        VStack(alignment: .leading, spacing: 4) {
            frame
                .overlay { picture }
                .clipShape(.rect(cornerRadius: Theme.Radius.small))
                .overlay(alignment: .bottomLeading) {
                    if material?.kind == .video {
                        Image(systemName: "video.fill")
                            .font(.caption2).foregroundStyle(.white)
                            .shadow(color: .black.opacity(0.6), radius: 3)
                            .padding(6)
                    }
                }
                .overlay(alignment: .topLeading) {
                    if showsSeed {
                        Image(systemName: "photo.badge.checkmark")
                            .font(.caption)
                            .foregroundStyle(Theme.onAccent)
                            .padding(4)
                            .background(Theme.accent, in: .circle)
                            .padding(4)
                    }
                }
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(Theme.label)
                .lineLimit(1).minimumScaleFactor(0.8)
            if showsName {
                Text(material?.name ?? materialID.map(BatchEntryText.fileName) ?? "Empty")
                    .font(.caption)
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(1).truncationMode(.middle)
            }
        }
        .frame(width: width, alignment: .leading)
    }

    @ViewBuilder private var frame: some View {
        if let width {
            Color.clear.frame(width: width, height: width * 4 / 3)
        } else {
            Color.clear.aspectRatio(3 / 4, contentMode: .fit)
        }
    }

    /// A job queued with a material that was later deleted still names it;
    /// the tile says so instead of showing an empty frame.
    @ViewBuilder private var picture: some View {
        if let thumbnail, let image = UIImage(data: thumbnail) {
            Image(uiImage: image).resizable().scaledToFill()
        } else {
            ZStack {
                Theme.surfaceRaised
                VStack(spacing: 4) {
                    Image(systemName: material == nil && materialID != nil
                          ? "exclamationmark.triangle"
                          : (material?.kind == .video || kind == .video ? "film" : "photo"))
                        .foregroundStyle(material == nil && materialID != nil ? Theme.warning : Theme.tertiary)
                    if material == nil, materialID != nil {
                        Text("Missing").font(.caption2).foregroundStyle(Theme.warning)
                    }
                }
            }
        }
    }
}

/// Everything one queued job will run with: pipeline and stages, provider,
/// each material large enough to judge, and the saved try-on it reuses.
@MainActor
struct BatchEntryDetail: View {
    let index: Int
    let entry: DraftBatchEntry
    let pipeline: Pipeline?
    let materials: MaterialsStore
    let library: TryonLibraryStore
    let dropDisabled: Bool
    let onDrop: () async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var confirmingDrop = false

    private let columns = [GridItem(.flexible(), spacing: 12, alignment: .top),
                           GridItem(.flexible(), spacing: 12, alignment: .top)]

    var body: some View {
        NavigationStack {
            List {
                Section {
                    LabeledContent("Pipeline", value: PipelineText.name(entry.pipeline))
                    if let pipeline, !pipeline.stages.isEmpty {
                        LabeledContent("Stages", value: PipelineText.stages(pipeline))
                    }
                    LabeledContent("Provider", value: BatchEntryText.provider(entry, pipeline: pipeline))
                }
                Section {
                    LazyVGrid(columns: columns, alignment: .leading, spacing: 16) {
                        ForEach(BatchEntryText.roles(entry, pipeline: pipeline), id: \.self) { role in
                            materialCell(role)
                        }
                    }
                    .padding(.vertical, 8)
                } header: {
                    Text("Materials")
                } footer: {
                    Text("Tap to view full screen. Long press for a quick look.")
                }
                if let seed = entry.tryonSeed {
                    Section {
                        let exists = library.entries.contains { $0.id == seed } || !library.loaded
                        Label(exists
                              ? "Uses a saved try-on — Phase A skips the provider for this job"
                              : "The saved try-on no longer exists",
                              systemImage: exists ? "photo.badge.checkmark" : "exclamationmark.triangle.fill")
                            .font(.subheadline)
                            .foregroundStyle(exists ? Theme.label : Theme.warning)
                    }
                }
                Section {
                    Button("Drop job", systemImage: "trash", role: .destructive) { confirmingDrop = true }
                        .disabled(dropDisabled)
                        .confirmationDialog("Drop this batch entry?", isPresented: $confirmingDrop,
                                            titleVisibility: .visible) {
                            Button("Drop", role: .destructive) {
                                Task {
                                    await onDrop()
                                    dismiss()
                                }
                            }
                            Button("Cancel", role: .cancel) {}
                        }
                }
            }
            .navigationTitle("Job \(index)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func materialCell(_ role: String) -> some View {
        BatchEntryMaterialTile(role: role, materialID: entry.slots[role] ?? nil,
                               kind: pipeline?.roles[role] ?? .unknown,
                               materials: materials,
                               showsSeed: entry.tryonSeed != nil && role == "outfit",
                               showsName: true, tapToPreview: true)
    }
}

/// The words a batch entry shows, shared by its row and its detail sheet.
enum BatchEntryText {
    /// The pipeline's own order (required, then optional) when it is known, so
    /// Character comes before Driver the way the slots above read; the
    /// server's keys otherwise, sorted.
    static func roles(_ entry: DraftBatchEntry, pipeline: Pipeline?) -> [String] {
        guard let pipeline else { return entry.slots.keys.sorted() }
        let ordered = (pipeline.required + pipeline.optional).filter { entry.slots[$0] != nil }
        return ordered + entry.slots.keys.filter { !ordered.contains($0) }.sorted()
    }

    static func provider(_ entry: DraftBatchEntry, pipeline: Pipeline?) -> String {
        guard let label = pipeline?.providers.first(where: { $0.id == entry.provider })?.label else {
            return entry.provider
        }
        return ProviderText(label: label).name
    }

    static func subtitle(_ entry: DraftBatchEntry, pipeline: Pipeline?) -> String {
        "\(PipelineText.name(entry.pipeline)) · \(provider(entry, pipeline: pipeline))"
    }

    /// `app/IMG_7145.png` → `IMG_7145.png`.
    static func fileName(_ materialID: String) -> String {
        materialID.split(separator: "/").last.map(String.init) ?? materialID
    }
}
