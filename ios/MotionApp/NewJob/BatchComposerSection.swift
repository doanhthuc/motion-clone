import MotionKit
import SwiftUI

/// Batch mode (Phase 6 spec §4): the edited job's slots are shared; each
/// chosen outfit becomes one basket job.
@MainActor
struct BatchComposerSection: View {
    let store: DraftStore
    let composer: BatchComposer
    let materials: MaterialsStore
    let pipeline: Pipeline
    let onPickRole: (String) -> Void
    @State private var pickingOutfits = false

    private var sharedRoles: [String] {
        (pipeline.required + pipeline.optional).filter { $0 != BatchComposer.outfitRole }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if !BatchComposer.supports(pipeline) {
                Text(store.catalog.contains(where: BatchComposer.supports)
                     ? "This pipeline has no character + outfit pair. Pick a try-on pipeline for a batch."
                     : "No pipeline on this server pairs a character with an outfit, so a batch is unavailable.")
                    .font(Theme.sans(13)).foregroundStyle(Theme.amber)
            } else {
                SectionLabel(text: "Shared")
                ForEach(sharedRoles, id: \.self) { role in
                    SlotMaterialRow(role: role, required: pipeline.required.contains(role),
                                    kind: pipeline.roles[role] ?? .unknown,
                                    slot: store.draft?.slots[role], materials: materials,
                                    disabled: store.isBusy || composer.isRunning) { onPickRole(role) }
                }
                SectionLabel(text: "Outfits · \(composer.outfits.count)/\(BatchComposer.maxJobs)")
                ForEach(composer.outfits) { outfit in outfitRow(outfit) }
                Button("Choose outfits…") { pickingOutfits = true }
                    .buttonStyle(SecondaryButtonStyle())
                    .accessibilityIdentifier("batch.pickOutfits")
                    .disabled(composer.isRunning)
                runButton
            }
        }
        // No seed observer here on purpose: `NewJobView` owns it, above the
        // Single|Batch split, because this view only exists in the Batch arm.
        .sheet(isPresented: $pickingOutfits) {
            // `.image` here but `.unknown` for the shared rows above: `accepts`
            // is false for every material under `.unknown`, and an outfit sheet
            // with no rows is a dead end, while an images-only one still builds
            // a batch. The shared rows can afford `.unknown` — `MaterialPicker`
            // renders an explanation for it.
            OutfitMultiPicker(composer: composer, materials: materials,
                              kind: pipeline.roles[BatchComposer.outfitRole] ?? .image)
        }
    }

    private func outfitRow(_ outfit: CrossOutfit) -> some View {
        let matches = composer.matches(for: outfit.outfitID)
        let material = materials.materials.first { $0.id == outfit.outfitID }
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                OutfitThumbnail(material: material, materials: materials)
                Text(material?.name ?? outfit.outfitID)
                    .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.ink1).lineLimit(1)
                Spacer()
                Button(role: .destructive) { composer.toggle(outfitID: outfit.outfitID) } label: {
                    Image(systemName: "xmark.circle")
                }
                .foregroundStyle(Theme.ink3).disabled(composer.isRunning)
            }
            Toggle("Use saved try-on", isOn: Binding(
                get: { outfit.seedID != nil },
                set: { composer.setSeed($0 ? matches.first?.id : nil, for: outfit.outfitID) }))
                .font(Theme.sans(13)).tint(Theme.lime)
                .disabled(matches.isEmpty || composer.isRunning)
                .accessibilityIdentifier("batch.seed.\(outfit.outfitID)")
            if matches.count > 1, outfit.seedID != nil {
                Picker("Saved image", selection: Binding(
                    get: { outfit.seedID ?? "" },
                    set: { composer.setSeed($0, for: outfit.outfitID) })) {
                    ForEach(matches) { entry in
                        Text("\(entry.provider) · \(Date(timeIntervalSince1970: entry.savedAt).formatted(date: .abbreviated, time: .shortened))")
                            .tag(entry.id)
                    }
                }
                .font(Theme.sans(12))
            }
            if matches.isEmpty {
                Text("No saved try-on for this pair — Phase A will make one.")
                    .font(Theme.mono(10)).foregroundStyle(Theme.ink3)
            }
        }
        .padding(12).card()
        .accessibilityIdentifier("batch.outfit.\(outfit.outfitID)")
    }

    @ViewBuilder private var runButton: some View {
        if let progress = composer.progress, composer.isRunning {
            HStack(spacing: 8) {
                ProgressView()
                Text("Adding \(progress.done)/\(progress.total)…").font(Theme.sans(13, .semibold))
            }
            .accessibilityIdentifier("batch.progress")
        }
        if let failure = composer.failure {
            Text(failure).font(Theme.sans(13)).foregroundStyle(Theme.red)
                .accessibilityIdentifier("batch.failure")
        }
        if let added = composer.lastAdded {
            Text("Added \(added) job\(added == 1 ? "" : "s") to the batch.")
                .font(Theme.sans(13)).foregroundStyle(Theme.lime)
        }
        if !composer.missingShared.isEmpty {
            Text("Fill \(composer.missingShared.joined(separator: ", ")) first.")
                .font(Theme.sans(12)).foregroundStyle(Theme.amber)
        }
        // Hidden once a build has landed: success clears `outfits`, so the
        // label would read "Add 0 jobs to batch" on a disabled button. A
        // failure keeps it visible — that is the Continue a stopped run offers,
        // and a run in flight keeps it so the button does not vanish mid-build.
        if !composer.outfits.isEmpty || composer.failure != nil || composer.isRunning {
            Button(composer.failure == nil
                   ? "Add \(composer.outfits.count) job\(composer.outfits.count == 1 ? "" : "s") to batch"
                   : "Continue") {
                Task { await composer.run() }
            }
            .buttonStyle(PrimaryButtonStyle())
            .accessibilityIdentifier("batch.run")
            .disabled(!composer.canRun)
        }
    }
}

/// The outfit's own thumbnail. Its own view because a `@ViewBuilder` function
/// cannot hold `@State`, and `SlotMaterialRow` fetches one the same way.
@MainActor
private struct OutfitThumbnail: View {
    let material: MotionKit.Material?
    let materials: MaterialsStore
    @State private var thumbnail: Data?

    var body: some View {
        Group {
            if let thumbnail, let image = UIImage(data: thumbnail) {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                ZStack {
                    Theme.surface2
                    Image(systemName: "photo")
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(Theme.ink3)
                }
            }
        }
        .frame(width: 34, height: 34)
        .clipShape(.rect(cornerRadius: 8))
        // Decorative: the row already names the outfit, and hiding it keeps the
        // card's accessibility tree the shape the seed and row identifiers expect.
        .accessibilityHidden(true)
        .task(id: material?.id) {
            guard let material else {
                thumbnail = nil
                return
            }
            thumbnail = await materials.thumbnail(for: material)
        }
    }
}

/// Multi-select of outfit materials, capped by `BatchComposer.maxJobs`.
@MainActor
private struct OutfitMultiPicker: View {
    let composer: BatchComposer
    let materials: MaterialsStore
    let kind: PipelineRoleKind
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List(materials.materials.filter(kind.accepts)) { material in
                let chosen = composer.outfits.contains { $0.outfitID == material.id }
                Button {
                    composer.toggle(outfitID: material.id)
                } label: {
                    HStack {
                        Text(material.name).foregroundStyle(Theme.ink1)
                        Spacer()
                        Image(systemName: chosen ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(chosen ? Theme.lime : Theme.ink3)
                    }
                }
                .disabled(!chosen && composer.outfits.count >= BatchComposer.maxJobs)
                .accessibilityIdentifier("outfit.pick.\(material.id)")
            }
            .navigationTitle("Choose outfits")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
        .task { if !materials.loaded { await materials.refresh() } }
    }
}
