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
    /// Off in the collapsed basket drawer, where a tile is 22 pt wide.
    var showsTitle = true
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
            if showsTitle {
                Text(title)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.label)
                    .lineLimit(1).minimumScaleFactor(0.8)
            }
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

/// One queued job, editable where it sits in the batch (2026-09-26): the
/// pipeline once as a strip of its stages, the provider as a menu, and each
/// material as a card to replace. It was a `List` until then, and its grid of
/// tiles sat in one list cell, so every long press peeked at the first tile
/// (Character) whichever was pressed; and nothing on it could be changed.
///
/// Read by position, not by digest: an edit changes the entry's signature and
/// so its digest, and the sheet must stay on the job it is editing.
@MainActor
struct BatchEntryDetail: View {
    let position: Int
    let store: DraftStore
    let pipelineFor: (DraftBatchEntry) -> Pipeline?
    let materials: MaterialsStore
    let library: TryonLibraryStore
    let locked: Bool
    @Environment(\.dismiss) private var dismiss
    @State private var confirmingDrop = false
    @State private var replacing: String?
    @State private var choosingProvider = false

    private var entry: DraftBatchEntry? {
        guard let batch = store.draft?.batch, batch.indices.contains(position) else { return nil }
        return batch[position]
    }

    var body: some View {
        NavigationStack {
            Group {
                if let entry {
                    content(entry, pipeline: pipelineFor(entry))
                } else {
                    ContentUnavailableView("Job no longer in the batch", systemImage: "tray")
                }
            }
            .background(Theme.bg)
            .navigationTitle("Job \(position + 1)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
                ToolbarItem(placement: .topBarLeading) {
                    if store.isBusy { ProgressView() }
                }
            }
        }
        .onChange(of: entry == nil) { _, gone in if gone { dismiss() } }
    }

    private var disabled: Bool { locked || store.isBusy }

    private func content(_ entry: DraftBatchEntry, pipeline: Pipeline?) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                VStack(alignment: .leading, spacing: 10) {
                    Text(PipelineText.name(entry.pipeline))
                        .font(.title3.bold())
                    if let pipeline, !pipeline.stages.isEmpty {
                        StageStrip(stages: pipeline.stages)
                    }
                }
                if let message = store.message {
                    MessageCard(text: message) { store.dismissMessage() }.heroSurface()
                }
                if let pipeline, !pipeline.providers.isEmpty {
                    providerMenu(entry, pipeline: pipeline)
                }
                materialsSection(entry, pipeline: pipeline)
                if let pipeline, !pipeline.providers.isEmpty {
                    seedRow(entry)
                }
                // No destructive role: it paints the label red over the red
                // fill whatever the style says. The dialog's Drop keeps it.
                Button { confirmingDrop = true } label: {
                    Label("Drop job", systemImage: "trash")
                }
                // Filled: the tinted bordered style read as disabled next to
                // the dark cards (user, 2026-09-26).
                .buttonStyle(DangerButtonStyle())
                .disabled(disabled)
                .confirmationDialog("Drop this batch entry?", isPresented: $confirmingDrop,
                                    titleVisibility: .visible) {
                    Button("Drop", role: .destructive) {
                        Task { await store.dropFromBatch(entry.digest) }
                    }
                    Button("Cancel", role: .cancel) {}
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .sheet(item: Binding(get: { replacing.map(RoleTarget.init) }, set: { replacing = $0?.role })) { target in
            let role = target.role
            let required = pipeline?.required.contains(role) ?? true
            MaterialPicker(role: role, kind: pipeline?.roles[role] ?? .unknown,
                           selectedID: entry.slots[role] ?? nil, materials: materials,
                           onSelect: { id in
                               Task { await store.editBatch(entry.digest,
                                                            BatchEntryEdit.replacing(role, with: id, in: entry)) }
                           },
                           allowsClear: !required)
        }
    }

    // MARK: Provider

    /// The choices open inside the card rather than in a menu: the menu's
    /// popover floated off the row it belonged to (user, 2026-09-26). A pick
    /// folds the card back to the one row.
    private func providerMenu(_ entry: DraftBatchEntry, pipeline: Pipeline) -> some View {
        VStack(spacing: 0) {
            Button {
                withAnimation(.snappy) { choosingProvider.toggle() }
            } label: {
                HStack(spacing: 12) {
                    ProviderMark(id: entry.provider)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("Provider").font(.caption).foregroundStyle(Theme.secondary)
                        Text(BatchEntryText.provider(entry, pipeline: pipeline))
                            .font(.body.weight(.semibold)).foregroundStyle(Theme.label)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.down")
                        .font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
                        .rotationEffect(.degrees(choosingProvider ? 180 : 0))
                }
                .padding(.horizontal, 14).frame(minHeight: 56)
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Provider")
            .accessibilityValue(BatchEntryText.provider(entry, pipeline: pipeline))
            .accessibilityHint(choosingProvider ? "Hide the providers" : "Show the providers")
            .accessibilityIdentifier("entry.provider")
            if choosingProvider {
                ForEach(pipeline.providers) { provider in
                    Divider().padding(.leading, 14)
                    providerRow(provider, selected: provider.id == entry.provider) {
                        withAnimation(.snappy) { choosingProvider = false }
                        guard provider.id != entry.provider else { return }
                        Task { await store.editBatch(entry.digest, BatchEntryEdit.provider(provider.id, in: entry)) }
                    }
                }
            }
        }
        .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
        .clipShape(.rect(cornerRadius: Theme.Radius.medium))
        .disabled(disabled)
        .onChange(of: disabled) { _, now in if now { choosingProvider = false } }
    }

    private func providerRow(_ provider: PipelineProvider, selected: Bool, action: @escaping () -> Void) -> some View {
        let text = ProviderText(label: provider.label)
        return Button(action: action) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: provider.id == "qwen" ? "cpu" : "cloud")
                    .font(.body).foregroundStyle(Theme.secondary).frame(width: 22)
                VStack(alignment: .leading, spacing: 2) {
                    Text(text.name).font(.body).foregroundStyle(Theme.label)
                    if let caveat = text.caveat {
                        Text(caveat).font(.caption).foregroundStyle(Theme.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
                Image(systemName: "checkmark")
                    .font(.body.weight(.semibold)).foregroundStyle(Theme.accent)
                    .opacity(selected ? 1 : 0)
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .frame(minHeight: 44)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(text.name)
        .accessibilityValue(text.caveat ?? "")
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    // MARK: Materials

    /// Every role the pipeline takes, empty optional ones too, so a
    /// background can be added to a queued job.
    private func roles(_ entry: DraftBatchEntry, pipeline: Pipeline?) -> [String] {
        guard let pipeline else { return BatchEntryText.roles(entry, pipeline: nil) }
        return pipeline.required + pipeline.optional.filter { !pipeline.required.contains($0) }
    }

    private func materialsSection(_ entry: DraftBatchEntry, pipeline: Pipeline?) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text("Materials").font(.headline)
                Spacer()
                Text("Tap to replace · hold to peek").font(.caption).foregroundStyle(Theme.secondary)
            }
            ScrollView(.horizontal) {
                HStack(alignment: .top, spacing: 12) {
                    ForEach(roles(entry, pipeline: pipeline), id: \.self) { role in
                        EntryMaterialCard(role: role, materialID: entry.slots[role] ?? nil,
                                          kind: pipeline?.roles[role] ?? .unknown,
                                          required: pipeline?.required.contains(role) ?? true,
                                          seeded: entry.tryonSeed != nil && role == "outfit",
                                          materials: materials, disabled: disabled,
                                          onReplace: { replacing = role },
                                          onRemove: {
                                              Task { await store.editBatch(
                                                  entry.digest, BatchEntryEdit.replacing(role, with: nil, in: entry)) }
                                          })
                    }
                }
                .padding(.vertical, 2)
            }
            .scrollIndicators(.hidden)
            .scrollClipDisabled()
        }
    }

    // MARK: Saved try-on

    private func seedRow(_ entry: DraftBatchEntry) -> some View {
        let pair = ["character", "outfit"].reduce(into: [String: String]()) { result, role in
            if let id = entry.slots[role] ?? nil { result[role] = id }
        }
        let matches = library.matches(slots: pair)
        let gone = entry.tryonSeed.map { seed in library.loaded && !library.entries.contains { $0.id == seed } } ?? false
        let hosted = BatchEntryEdit.canSeed(entry)
        let note = gone ? "The saved try-on no longer exists."
            : !hosted ? "Only a hosted provider can reuse a saved try-on."
            : matches.isEmpty ? "No saved try-on for this character and outfit."
            : "Phase A skips the provider for this job."
        return Toggle(isOn: Binding(
            get: { entry.tryonSeed != nil },
            set: { on in
                Task { await store.editBatch(entry.digest,
                                             BatchEntryPatch(seed: on ? matches.first.map { .set($0.id) } ?? .keep : .clear)) }
            })) {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Use saved try-on").font(.body)
                    Text(note).font(.caption).foregroundStyle(gone ? Theme.warning : Theme.secondary)
                }
            } icon: {
                Image(systemName: gone ? "exclamationmark.triangle.fill" : "photo.badge.checkmark")
                    .foregroundStyle(gone ? Theme.warning : Theme.accent)
            }
        }
        .tint(Theme.accent)
        .disabled(disabled || (entry.tryonSeed == nil && (!hosted || matches.isEmpty)))
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
        .accessibilityIdentifier("entry.seed")
    }
}

private struct RoleTarget: Identifiable {
    let role: String
    var id: String { role }
}

/// The pipeline's stages as a row of capsules, which is all the name said in
/// other words; the detail used to print both.
private struct StageStrip: View {
    let stages: [String]

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 6) {
                ForEach(Array(stages.enumerated()), id: \.offset) { index, stage in
                    if index > 0 {
                        Image(systemName: "chevron.compact.right")
                            .font(.footnote.weight(.semibold)).foregroundStyle(Theme.tertiary)
                    }
                    Label(PipelineText.stage(stage),
                          systemImage: Self.symbol(stage))
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(Theme.label)
                        .padding(.horizontal, 10).padding(.vertical, 6)
                        .background(Theme.surface, in: .capsule)
                }
            }
        }
        .scrollIndicators(.hidden)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Stages")
        .accessibilityValue(stages.map(PipelineText.stage).joined(separator: ", then "))
    }

    static func symbol(_ stage: String) -> String {
        switch stage {
        case "tryon", "camera-tryon": "tshirt"
        case "motion": "figure.run"
        case "camera-motion": "video"
        case "character-swap": "person.2.crop.square.stack"
        case "enhance": "sparkles"
        default: "circle.dashed"
        }
    }
}

/// One material of the job being edited: tap replaces it, long press peeks at
/// it with Replace, View full screen and, on an optional role, Remove.
@MainActor
private struct EntryMaterialCard: View {
    let role: String
    let materialID: String?
    let kind: PipelineRoleKind
    let required: Bool
    let seeded: Bool
    let materials: MaterialsStore
    let disabled: Bool
    let onReplace: () -> Void
    let onRemove: () -> Void
    @State private var thumbnail: Data?
    @State private var previewing = false

    static let size = CGSize(width: 116, height: 155)

    private var material: MotionKit.Material? {
        materialID.flatMap { id in materials.materials.first { $0.id == id } }
    }

    private var title: String { SlotText(role: role, required: required, kind: kind, slot: nil).title }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            picture
                .frame(width: Self.size.width, height: Self.size.height)
                .clipShape(.rect(cornerRadius: Theme.Radius.medium))
                .overlay(alignment: .bottomTrailing) {
                    if !disabled {
                        Image(systemName: materialID == nil ? "plus" : "pencil")
                            .font(.caption.weight(.bold)).foregroundStyle(Theme.onAccent)
                            .frame(width: 26, height: 26)
                            .background(Theme.accent, in: .circle)
                            .padding(6)
                    }
                }
                .overlay(alignment: .topLeading) {
                    if seeded {
                        Image(systemName: "photo.badge.checkmark")
                            .font(.caption).foregroundStyle(Theme.onAccent)
                            .padding(5).background(Theme.accent, in: .circle).padding(6)
                    }
                }
                .contentShape(.rect(cornerRadius: Theme.Radius.medium))
                .onTapGesture { if !disabled { onReplace() } }
                .contextMenu {
                    if !disabled {
                        Button(materialID == nil ? "Choose" : "Replace", systemImage: "arrow.triangle.2.circlepath",
                               action: onReplace)
                    }
                    if material != nil {
                        Button("View full screen", systemImage: "arrow.up.left.and.arrow.down.right") {
                            previewing = true
                        }
                    }
                    if !required, materialID != nil, !disabled {
                        Button("Remove", systemImage: "trash", role: .destructive, action: onRemove)
                    }
                } preview: {
                    if let material { MaterialPeek(material: material, materials: materials) }
                }
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(Theme.label)
                Text(material?.name ?? materialID.map(BatchEntryText.fileName) ?? (required ? "Required" : "Optional"))
                    .font(.caption).foregroundStyle(Theme.secondary)
                    .lineLimit(1).truncationMode(.middle)
            }
            .frame(width: Self.size.width, alignment: .leading)
        }
        .sheet(isPresented: $previewing) {
            if let material { MaterialPreview(material: material, materials: materials, modal: true) }
        }
        .task(id: material?.id) {
            guard let material else { thumbnail = nil; return }
            thumbnail = await materials.thumbnail(for: material)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(title)
        .accessibilityValue(material?.name ?? (materialID == nil ? "Empty" : "Missing"))
        .accessibilityHint("Replace this material")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { if !disabled { onReplace() } }
        .accessibilityActions {
            if material != nil { Button("View full screen") { previewing = true } }
            if !required, materialID != nil, !disabled { Button("Remove", action: onRemove) }
        }
        .accessibilityIdentifier("entry.material.\(role)")
    }

    @ViewBuilder private var picture: some View {
        if let thumbnail, let image = UIImage(data: thumbnail) {
            Image(uiImage: image).resizable().scaledToFill()
                .overlay(alignment: .bottomLeading) {
                    if material?.kind == .video {
                        Image(systemName: "play.fill").font(.caption2).foregroundStyle(.white)
                            .shadow(color: .black.opacity(0.6), radius: 3).padding(8)
                    }
                }
        } else if materialID == nil {
            RoundedRectangle(cornerRadius: Theme.Radius.medium)
                .strokeBorder(Theme.accent.opacity(0.6), style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
                .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
        } else {
            ZStack {
                Theme.surfaceRaised
                VStack(spacing: 4) {
                    Image(systemName: material == nil ? "exclamationmark.triangle" : (kind == .video ? "film" : "photo"))
                        .foregroundStyle(material == nil ? Theme.warning : Theme.tertiary)
                    if material == nil { Text("Missing").font(.caption2).foregroundStyle(Theme.warning) }
                }
            }
        }
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
