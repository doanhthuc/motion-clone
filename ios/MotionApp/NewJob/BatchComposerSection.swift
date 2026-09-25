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
                Section {
                    ForEach(sharedRoles, id: \.self) { role in
                        SlotMaterialRow(role: role, required: pipeline.required.contains(role),
                                        kind: pipeline.roles[role] ?? .unknown,
                                        slot: store.draft?.slots[role], materials: materials,
                                        disabled: store.isBusy || composer.isRunning) { onPickRole(role) }
                    }
                } header: {
                    Text("Shared by every video")
                }
                Section {
                    strip {
                        BatchAddTile(title: composer.outfits.isEmpty ? "Add outfits" : "Add more") {
                            pickingOutfits = true
                        }
                        .disabled(composer.isRunning)
                        .accessibilityIdentifier("batch.pickOutfits")
                        ForEach(composer.outfits) { outfit in outfitTile(outfit) }
                    }
                } header: {
                    countHeader("Outfits", count: composer.outfits.count, cap: outfitCap)
                } footer: {
                    Text(composer.outfits.contains { !composer.matches(for: $0.outfitID).isEmpty }
                         ? "Tap an outfit to choose its saved try-on or remove it."
                         : "Each outfit is one try-on, dressed on the character above.")
                }
                if BatchComposer.supportsDrivers(pipeline) {
                    Section {
                        strip {
                            BatchAddTile(title: composer.drivers.isEmpty ? "Add drivers" : "Add more") {
                                pickingDrivers = true
                            }
                            .disabled(composer.isRunning)
                            .accessibilityIdentifier("batch.pickDrivers")
                            ForEach(composer.drivers, id: \.self) { driverID in driverTile(driverID) }
                        }
                    } header: {
                        countHeader("Drivers", count: composer.drivers.count, cap: driverCap)
                    } footer: {
                        Text(composer.cameraAwareTryon && composer.drivers.count > 1
                             ? "Every outfit is rendered once per driver. Camera pipelines make one try-on per driver."
                             : "Every outfit is rendered once per driver. Leave empty to use the shared driver.")
                    }
                }
            }
        }
        // No seed observer here on purpose: `NewJobView` owns it, above the
        // Single|Batch split, because this view only exists in the Batch arm.
    }

    /// A horizontal row of tiles occupying a whole list row, edge to edge.
    private func strip<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        ScrollView(.horizontal) {
            HStack(alignment: .top, spacing: 12) { content() }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
        }
        .scrollIndicators(.hidden)
        .listRowInsets(EdgeInsets())
    }

    private func countHeader(_ title: String, count: Int, cap: Int) -> some View {
        HStack {
            Text(title)
            Spacer()
            Text("\(count) of \(cap)").monospacedDigit()
        }
    }

    /// Tap for the menu: the saved try-on choice and Remove. A menu rather than
    /// controls under each tile keeps the strip one row tall, and it replaces
    /// the small ✕ glyph the rows had, which was under the 44 pt minimum.
    private func outfitTile(_ outfit: CrossOutfit) -> some View {
        let matches = composer.matches(for: outfit.outfitID)
        let material = materials.materials.first { $0.id == outfit.outfitID }
        return Menu {
            Toggle("Use saved try-on", systemImage: "photo.badge.checkmark", isOn: Binding(
                get: { outfit.seedID != nil },
                set: { composer.setSeed($0 ? matches.first?.id : nil, for: outfit.outfitID) }))
                .disabled(matches.isEmpty)
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
                .pickerStyle(.menu)
            }
            if matches.isEmpty {
                Text("No saved try-on for this pair — Phase A will make one.")
            }
            Divider()
            Button("Remove", systemImage: "trash", role: .destructive) {
                composer.toggle(outfitID: outfit.outfitID)
            }
        } label: {
            BatchMediaTile(material: material, fallbackName: outfit.outfitID, materials: materials,
                           badge: outfit.seedID == nil ? nil : "Saved try-on")
        }
        .disabled(composer.isRunning)
        .accessibilityLabel(material?.name ?? outfit.outfitID)
        .accessibilityValue(outfit.seedID == nil ? "" : "Uses a saved try-on")
        .accessibilityIdentifier("batch.outfit.\(outfit.outfitID)")
    }

    /// Simpler than `outfitTile` on purpose: a driver carries no seed — the
    /// server shares one try-on per outfit across its drivers (non-camera
    /// pipelines), so seeding lives on `CrossOutfit`, not here.
    private func driverTile(_ driverID: String) -> some View {
        let material = materials.materials.first { $0.id == driverID }
        return Menu {
            Button("Remove", systemImage: "trash", role: .destructive) {
                composer.toggle(driverID: driverID)
            }
        } label: {
            BatchMediaTile(material: material, fallbackName: driverID, materials: materials)
        }
        .disabled(composer.isRunning)
        .accessibilityLabel(material?.name ?? driverID)
        .accessibilityIdentifier("batch.driver.\(driverID)")
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
                MaterialMultiPicker(
                    title: "Choose outfits",
                    kind: pipeline.roles[BatchComposer.outfitRole] ?? .image,
                    role: .outfit, materials: materials, identifierPrefix: "outfit.pick",
                    disabled: composer.isRunning, note: composer.capReason,
                    isChosen: { id in composer.outfits.contains { $0.outfitID == id } },
                    toggle: { composer.toggle(outfitID: $0) })
            }
            .sheet(isPresented: $pickingDrivers) {
                MaterialMultiPicker(
                    title: "Choose drivers",
                    kind: pipeline.roles[BatchComposer.driverRole] ?? .video,
                    role: .driver, materials: materials, identifierPrefix: "driver.pick",
                    disabled: composer.isRunning, note: composer.capReason,
                    isChosen: { composer.drivers.contains($0) },
                    toggle: { composer.toggle(driverID: $0) })
            }
    }
}
