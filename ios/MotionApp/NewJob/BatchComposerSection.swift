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
    /// Owned by `NewJobView`, whose `List` carries the sheets
    /// (`BatchPickerSheets`): a sheet hung on lazily-built list content is
    /// torn down when its row scrolls away.
    @Binding var pickingOutfits: Bool
    @Binding var pickingDrivers: Bool

    /// Roles the crossed pickers below fill: the outfit always, the driver too
    /// once at least one is multi-selected (mirrors `BatchComposer.crossedRoles`,
    /// which is private). While `drivers` is empty the driver role stays a
    /// plain shared row, exactly as it behaved before drivers existed.
    private var crossedRoles: Set<String> {
        composer.drivers.isEmpty ? [BatchComposer.outfitRole] : [BatchComposer.outfitRole, BatchComposer.driverRole]
    }

    private var sharedRoles: [String] {
        let crossed = crossedRoles
        return (pipeline.required + pipeline.optional).filter { !crossed.contains($0) }
    }

    /// How many outfits fit at the current driver count — `maxJobs` divided by
    /// the drivers already picked, floored, since `BatchComposer.fits` refuses
    /// once outfits × drivers would exceed `maxJobs`. Shown instead of the flat
    /// outfit count against `maxJobs` (Task 6's known follow-up: that stale
    /// denominator read "n/12" even with 2 drivers picked, where the real
    /// ceiling is 6).
    private var outfitCap: Int { BatchComposer.maxJobs / max(composer.drivers.count, 1) }
    private var driverCap: Int { BatchComposer.maxJobs / max(composer.outfits.count, 1) }

    private var summaryText: String {
        func count(_ n: Int, _ noun: String) -> String { "\(n) \(noun)\(n == 1 ? "" : "s")" }
        return "\(count(composer.outfits.count, "outfit")) × "
            + "\(count(max(composer.drivers.count, 1), "driver")) = "
            + "\(count(composer.jobCount, "video")) · \(count(composer.tryonCount, "try-on"))"
    }

    var body: some View {
        Group {
            if !BatchComposer.supports(pipeline) {
                Section {
                    Label(store.catalog.contains(where: BatchComposer.supports)
                          ? "This pipeline has no character + outfit pair. Pick a try-on pipeline for a batch."
                          : "No pipeline on this server pairs a character with an outfit, so a batch is unavailable.",
                          systemImage: "info.circle")
                        .font(.subheadline).foregroundStyle(Theme.secondary)
                }
            } else {
                Section("Shared") {
                    ForEach(sharedRoles, id: \.self) { role in
                        SlotMaterialRow(role: role, required: pipeline.required.contains(role),
                                        kind: pipeline.roles[role] ?? .unknown,
                                        slot: store.draft?.slots[role], materials: materials,
                                        disabled: store.isBusy || composer.isRunning) { onPickRole(role) }
                    }
                }
                Section("Outfits · \(composer.outfits.count) of \(outfitCap)") {
                    ForEach(composer.outfits) { outfit in outfitRow(outfit) }
                    Button("Choose outfits…") { pickingOutfits = true }
                        .accessibilityIdentifier("batch.pickOutfits")
                        .disabled(composer.isRunning)
                }
                if BatchComposer.supportsDrivers(pipeline) {
                    Section("Drivers · \(composer.drivers.count) of \(driverCap)") {
                        ForEach(composer.drivers, id: \.self) { driverID in driverRow(driverID) }
                        Button("Choose drivers…") { pickingDrivers = true }
                            .accessibilityIdentifier("batch.pickDrivers")
                            .disabled(composer.isRunning)
                    }
                }
                Section {
                    Text(summaryText)
                        .font(.headline.monospacedDigit())
                        .accessibilityIdentifier("batch.summary")
                    if composer.cameraAwareTryon && composer.drivers.count > 1 {
                        Text("Camera pipelines make one try-on per driver.")
                            .font(.footnote).foregroundStyle(Theme.secondary)
                    }
                    if let capReason = composer.capReason {
                        Text(capReason).font(.footnote).foregroundStyle(Theme.warning)
                            .accessibilityIdentifier("batch.capReason")
                    }
                    runButton
                }
            }
        }
        // No seed observer here on purpose: `NewJobView` owns it, above the
        // Single|Batch split, because this view only exists in the Batch arm.
    }

    private func outfitRow(_ outfit: CrossOutfit) -> some View {
        let matches = composer.matches(for: outfit.outfitID)
        let material = materials.materials.first { $0.id == outfit.outfitID }
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                MaterialThumbnail(material: material, materials: materials)
                Text(material?.name ?? outfit.outfitID)
                    .font(.body).lineLimit(1).truncationMode(.middle)
                Spacer()
                Button(role: .destructive) { composer.toggle(outfitID: outfit.outfitID) } label: {
                    Image(systemName: "xmark.circle.fill")
                }
                .buttonStyle(.borderless)
                .foregroundStyle(Theme.tertiary).disabled(composer.isRunning)
                .accessibilityLabel("Remove")
            }
            Toggle("Use saved try-on", isOn: Binding(
                get: { outfit.seedID != nil },
                set: { composer.setSeed($0 ? matches.first?.id : nil, for: outfit.outfitID) }))
                .font(.subheadline)
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
                .font(.footnote)
            }
            if matches.isEmpty {
                Text("No saved try-on for this pair — Phase A will make one.")
                    .font(.footnote).foregroundStyle(Theme.secondary)
            }
        }
        .padding(.vertical, 2)
        .accessibilityIdentifier("batch.outfit.\(outfit.outfitID)")
    }

    /// Simpler than `outfitRow` on purpose: a driver carries no seed — the
    /// server shares one try-on per outfit across its drivers (non-camera
    /// pipelines), so seeding lives on `CrossOutfit`, not here.
    private func driverRow(_ driverID: String) -> some View {
        let material = materials.materials.first { $0.id == driverID }
        return HStack(spacing: 10) {
            MaterialThumbnail(material: material, materials: materials)
            Text(material?.name ?? driverID)
                .font(.body).lineLimit(1).truncationMode(.middle)
            Spacer()
            Button(role: .destructive) { composer.toggle(driverID: driverID) } label: {
                Image(systemName: "xmark.circle.fill")
            }
            .buttonStyle(.borderless)
            .foregroundStyle(Theme.tertiary).disabled(composer.isRunning)
            .accessibilityLabel("Remove")
        }
        .accessibilityIdentifier("batch.driver.\(driverID)")
    }

    @ViewBuilder private var runButton: some View {
        if let progress = composer.progress, composer.isRunning {
            HStack(spacing: 8) {
                ProgressView()
                Text("Adding \(progress.done) of \(progress.total)…").font(.subheadline.monospacedDigit())
            }
            .accessibilityIdentifier("batch.progress")
        }
        if let failure = composer.failure {
            Label(failure, systemImage: "exclamationmark.triangle.fill")
                .font(.subheadline).foregroundStyle(Theme.danger)
                .accessibilityIdentifier("batch.failure")
        }
        if let added = composer.lastAdded {
            Text("Added \(added) job\(added == 1 ? "" : "s") to the batch.")
                .font(.subheadline).foregroundStyle(Theme.secondary)
        }
        if !composer.missingShared.isEmpty {
            Text("Fill \(composer.missingShared.joined(separator: ", ")) first.")
                .font(.footnote).foregroundStyle(Theme.secondary)
        }
        // Hidden once a build has landed: success clears `outfits`, so the
        // label would read "Add 0 jobs to batch" on a disabled button. A
        // failure keeps it visible — that is the Continue a stopped run offers,
        // and a run in flight keeps it so the button does not vanish mid-build.
        if !composer.outfits.isEmpty || composer.failure != nil || composer.isRunning {
            // `jobCount`, not `outfits.count`: with drivers multi-selected each
            // outfit becomes several basket jobs, and the pre-drivers label
            // would undercount by exactly the driver factor.
            Button(composer.failure == nil
                   ? "Add \(composer.jobCount) job\(composer.jobCount == 1 ? "" : "s") to batch"
                   : "Continue") {
                Task { await composer.run() }
            }
            .buttonStyle(PrimaryButtonStyle())
            .accessibilityIdentifier("batch.run")
            .disabled(!composer.canRun)
            .buttonRow()
        }
    }
}

/// The outfit and driver multi-pickers, attached above the `List`.
struct BatchPickerSheets: ViewModifier {
    @Binding var pickingOutfits: Bool
    @Binding var pickingDrivers: Bool
    let composer: BatchComposer
    let materials: MaterialsStore
    let pipeline: Pipeline

    func body(content: Content) -> some View {
        content
            .sheet(isPresented: $pickingOutfits) {
                // `.image` here but `.unknown` for the shared rows above: `accepts`
                // is false for every material under `.unknown`, and an outfit sheet
                // with no rows is a dead end, while an images-only one still builds
                // a batch. The shared rows can afford `.unknown` — `MaterialPicker`
                // renders an explanation for it.
                OutfitMultiPicker(composer: composer, materials: materials,
                                  kind: pipeline.roles[BatchComposer.outfitRole] ?? .image)
            }
            .sheet(isPresented: $pickingDrivers) {
                DriverMultiPicker(composer: composer, materials: materials,
                                  kind: pipeline.roles[BatchComposer.driverRole] ?? .video)
            }
    }
}

/// Shared by an outfit row and a driver row. Its own view because a
/// `@ViewBuilder` function cannot hold `@State`, and `SlotMaterialRow` fetches
/// one the same way.
@MainActor
private struct MaterialThumbnail: View {
    let material: MotionKit.Material?
    let materials: MaterialsStore
    @State private var thumbnail: Data?

    var body: some View {
        Group {
            if let thumbnail, let image = UIImage(data: thumbnail) {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                ZStack {
                    Theme.surfaceRaised
                    Image(systemName: "photo")
                        .font(.footnote)
                        .foregroundStyle(Theme.tertiary)
                }
            }
        }
        .frame(width: 36, height: 36)
        .clipShape(.rect(cornerRadius: Theme.Radius.small))
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
                        Text(material.name).foregroundStyle(Theme.label)
                            .lineLimit(1).truncationMode(.middle)
                        Spacer()
                        Image(systemName: chosen ? "checkmark.circle.fill" : "circle")
                            .font(.title3)
                            .foregroundStyle(chosen ? Theme.accent : Theme.tertiary)
                    }
                }
                // Not pre-disabled by a count against `maxJobs`: with drivers
                // picked the real ceiling is `maxJobs / drivers`, which moves
                // as drivers are toggled elsewhere, and disabling rows here
                // against the wrong number either greys out a tap that would
                // succeed or leaves tappable one that `toggle` must refuse
                // anyway. `toggle(outfitID:)` is the single source of truth —
                // a refusal sets `capReason`, rendered under the pickers.
                .disabled(composer.isRunning)
                .accessibilityIdentifier("outfit.pick.\(material.id)")
            }
            .navigationTitle("Choose outfits")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
        .task { if !materials.loaded { await materials.refresh() } }
    }
}

/// Multi-select of driver materials, mirroring `OutfitMultiPicker`. Rows are
/// never pre-disabled by a count against `maxJobs`, for the same reason: the
/// real ceiling moves with the outfit count, and `toggle(driverID:)` is the
/// single source of truth for a refusal (`capReason`, rendered under the
/// pickers).
@MainActor
private struct DriverMultiPicker: View {
    let composer: BatchComposer
    let materials: MaterialsStore
    let kind: PipelineRoleKind
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List(materials.materials.filter(kind.accepts)) { material in
                let chosen = composer.drivers.contains(material.id)
                Button {
                    composer.toggle(driverID: material.id)
                } label: {
                    HStack {
                        Text(material.name).foregroundStyle(Theme.label)
                            .lineLimit(1).truncationMode(.middle)
                        Spacer()
                        Image(systemName: chosen ? "checkmark.circle.fill" : "circle")
                            .font(.title3)
                            .foregroundStyle(chosen ? Theme.accent : Theme.tertiary)
                    }
                }
                .disabled(composer.isRunning)
                .accessibilityIdentifier("driver.pick.\(material.id)")
            }
            .navigationTitle("Choose drivers")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
        }
        .task { if !materials.loaded { await materials.refresh() } }
    }
}
