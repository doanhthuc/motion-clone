import MotionKit
import SwiftUI

@MainActor
struct NewJobView: View {
    let store: DraftStore
    let materials: MaterialsStore
    let flow: RunFlow
    let composer: BatchComposer
    let library: TryonLibraryStore
    @Environment(AppModel.self) private var model
    @State private var selectedRole: String?
    @State private var dropCandidate: DraftBatchEntry?
    @State private var showRun = false
    @State private var pickingOutfits = false
    @State private var pickingDrivers = false
    @State private var clearSource: ClearSource?

    var body: some View {
        Group {
            if let draft = store.draft, let pipeline = store.selectedPipeline {
                editor(draft: draft, pipeline: pipeline)
            } else if let error = store.error {
                initialLoadFailure(error)
            } else if store.isRefreshing {
                LoadingBlock(title: "Loading draft…")
            } else {
                ContentUnavailableView("New Job unavailable", systemImage: "exclamationmark.triangle")
            }
        }
        .navigationTitle("New Job")
        .task { await store.load() }
        .task { await library.load() }
        .refreshable { await store.refresh() }
        .onChange(of: store.needsMaterialsRefresh) { _, needsRefresh in
            guard needsRefresh else { return }
            Task {
                await materials.refresh()
                store.acknowledgeMaterialsRefresh()
                closePickerIfSelectionDisappeared()
            }
        }
        // Both seed observers sit above the Single|Batch split so they outlive the
        // Batch arm: Single mode edits the same draft slots through
        // `slots(draft:pipeline:)`, and an arm removed while they changed never
        // re-picks. The seed chosen for the old character/outfit pair would then be
        // PATCHed for the new one, and Phase A skips the provider and seeds the job
        // from an image made from different materials. `library.loaded` is observed
        // too because `matches(for:)` is empty against an unfetched library, so an
        // outfit chosen before `load()` returns would keep no seed at all; no manual
        // pick can be lost by that, since the seed toggle is disabled while
        // `matches` is empty. `seedKey`, not `sharedSlots`: toggling the first
        // driver on or the last off moves `driver` in or out of `sharedSlots`,
        // and re-picking then would overwrite a hand-chosen seed for nothing.
        .onChange(of: composer.seedKey) { _, _ in composer.refreshSeeds() }
        .onChange(of: library.loaded) { _, loaded in
            if loaded { composer.refreshSeeds() }
        }
    }

    /// Where Clear was asked from. Each source carries its own dialog, because
    /// on iOS 26 a confirmation dialog is a popover pointing at the view it is
    /// attached to: hung on the whole `List`, as both dialogs here were until
    /// 2026-09-25, its arrow pointed at the middle of the screen, at nothing.
    private enum ClearSource { case menu, batchHeader }

    private func confirmsClear(_ source: ClearSource) -> Binding<Bool> {
        Binding(get: { clearSource == source }, set: { if !$0 { clearSource = nil } })
    }

    private func clearDialog(_ source: ClearSource) -> some ViewModifier {
        ClearDraftDialog(isPresented: confirmsClear(source), message: clearMessage) {
            Task { await store.clear() }
        }
    }

    /// Clear empties the basket *and* unassigns every slot, in both modes
    /// (`POST /v1/draft/clear`), so the dialog names both before it happens.
    private var clearMessage: String {
        let queued = store.draft?.batch.count ?? 0
        return queued == 0
            ? "Every picked material is removed."
            : "\(queued) job\(queued == 1 ? "" : "s") in the batch and every picked material are removed."
    }

    private func initialLoadFailure(_ error: APIError) -> some View {
        VStack(spacing: 16) {
            ContentUnavailableView(
                "New Job unavailable",
                systemImage: "exclamationmark.triangle",
                description: Text("The draft and pipeline catalog could not be loaded."))
            ErrorBanner(error: error) { await store.load() }.heroSurface()
        }
        .padding(.horizontal, 16)
    }

    private func editor(draft: Draft, pipeline: Pipeline) -> some View {
        let isBatch = model.newJobMode == .batch
        // Cross build needs a character + outfit pair, so Batch mode offers only the
        // pipelines that have one. When none qualify there is nothing to choose, so
        // the picker is disabled instead of opening an empty menu behind a label
        // that still names the pipeline the user is on.
        let batchCatalog = store.catalog.filter(BatchComposer.supports)
        let batchSupported = BatchComposer.supports(pipeline)
        // Materials first, settings after: the inputs are what the user came
        // to pick, and before 2026-09-25 the provider rows (~550 pt) pushed
        // them under the tab bar. The next step is pinned below the list
        // (`NewJobActionBar`) rather than being its last rows.
        return List {
            Section {
                Picker("Mode", selection: Binding(get: { model.newJobMode }, set: { model.newJobMode = $0 })) {
                    Text("Single").tag(NewJobMode.single)
                    Text("Batch").tag(NewJobMode.batch)
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("newjob.mode")
                .disabled(store.isBusy || composer.isRunning)
                .buttonRow()
            }
            banners
            if isBatch {
                BatchComposerSection(store: store, composer: composer,
                                     materials: materials, pipeline: pipeline,
                                     onPickRole: { selectedRole = $0 },
                                     pickingOutfits: $pickingOutfits,
                                     pickingDrivers: $pickingDrivers)
            } else {
                slots(draft: draft, pipeline: pipeline)
                seedBadge(draft)
            }
            PipelinePicker(
                pipeline: pipeline,
                pipelines: isBatch ? batchCatalog : store.catalog,
                selectedProvider: draft.provider,
                disabled: store.isBusy || composer.isRunning || (isBatch && batchCatalog.isEmpty),
                onPipelineSelected: { id in await store.selectPipeline(id) },
                onProviderSelected: { id in await store.selectProvider(id) })
            batch(draft)
        }
        .navigationSubtitle("\(draft.jobs) job\(draft.jobs == 1 ? "" : "s")")
        .toolbar {
            if store.isStale {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await store.refresh() } } label: {
                        Image(systemName: "clock.arrow.circlepath")
                    }
                    .tint(Theme.warning)
                    .accessibilityLabel("Stale — refresh")
                }
            }
            ToolbarItem(placement: .topBarTrailing) { moreMenu }
        }
        .safeAreaInset(edge: .bottom) {
            if NewJobActionBar.isVisible(draft: draft, isBatch: isBatch, batchSupported: batchSupported,
                                         composer: composer) {
                NewJobActionBar(store: store, composer: composer, draft: draft, isBatch: isBatch,
                                batchSupported: batchSupported, onContinue: { showRun = true })
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.snappy, value: NewJobActionBar.isVisible(draft: draft, isBatch: isBatch,
                                                            batchSupported: batchSupported,
                                                            composer: composer))
        .modifier(BatchPickerSheets(pickingOutfits: $pickingOutfits, pickingDrivers: $pickingDrivers,
                                    composer: composer, materials: materials, pipeline: pipeline))
        .navigationDestination(isPresented: $showRun) {
            RunFlowView(flow: flow, entry: .newJob)
        }
        .sheet(
            isPresented: Binding(
                get: { selectedRole != nil },
                set: { if !$0 { selectedRole = nil } })
        ) {
            if let role = selectedRole {
                MaterialPicker(
                    role: role,
                    kind: pipeline.roles[role] ?? .unknown,
                    selectedID: draft.slots[role]?.materialID,
                    materials: materials,
                    onSelect: { materialID in
                        Task { await store.assign(role: role, materialID: materialID) }
                    })
                    .interactiveDismissDisabled(store.isBusy)
            }
        }
    }

    @ViewBuilder private var banners: some View {
        if store.error != nil || (store.message != nil && store.message != store.error?.userMessage) {
            Section {
                if let error = store.error {
                    ErrorBanner(error: error) { await store.refresh() }
                }
                if let message = store.message, message != store.error?.userMessage {
                    MessageCard(text: message) { store.dismissMessage() }
                }
            }
        }
    }

    private func slots(draft: Draft, pipeline: Pipeline) -> some View {
        Section {
            SlotTileGrid(count: (pipeline.required + pipeline.optional).count) {
                ForEach(pipeline.required + pipeline.optional, id: \.self) { role in
                    SlotMaterialRow(
                        role: role,
                        required: pipeline.required.contains(role),
                        kind: pipeline.roles[role] ?? .unknown,
                        slot: draft.slots[role],
                        materials: materials,
                        disabled: store.isBusy,
                        tile: true) {
                            selectedRole = role
                        }
                }
            }
        } header: {
            Text("Materials")
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(slotWarnings(draft, pipeline: pipeline), id: \.self) { warning in
                    Label(warning, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(Theme.warning)
                }
                readiness(draft)
            }
        }
    }

    /// A tile has room for a warning glyph, not its sentence; the sentences
    /// sit under the grid, each named by its slot.
    private func slotWarnings(_ draft: Draft, pipeline: Pipeline) -> [String] {
        (pipeline.required + pipeline.optional).compactMap { role in
            guard let warning = draft.slots[role]?.warning, !warning.isEmpty else { return nil }
            return "\(SlotText(role: role, required: true, kind: .unknown, slot: nil).title): \(warning)"
        }
    }

    /// Single mode only, deliberately. This reports the *edited job*'s required
    /// and missing roles, and the Batch arm does not render the edited job — it
    /// renders shared slots plus an outfit multi-select. A "2 of 3 required
    /// slots assigned" line under a cross-build form would describe a job the
    /// user is not looking at. Batch mode's own readiness is `BatchComposer`'s
    /// `canRun`, which the run button's disabled state already shows.
    private func readiness(_ draft: Draft) -> some View {
        let assigned = draft.required.count - draft.missing.count
        let ready = draft.missing.isEmpty
        return Text(ready ? "Ready · \(assigned) of \(draft.required.count) required slots" : "\(assigned) of \(draft.required.count) required slots assigned")
            .accessibilityValue(ready ? "Ready" : "Missing required materials")
    }

    /// Clear sits behind "More", the way Photos and Notes keep destructive
    /// actions, and behind a confirmation. Until 2026-09-25 it was a bare red
    /// capsule in the leading corner, where a Back button is expected, and it
    /// emptied the draft on one tap. The batch header offers the same action
    /// next to the jobs it removes.
    private var moreMenu: some View {
        Menu {
            Button("Clear draft", systemImage: "trash", role: .destructive) { clearSource = .menu }
                .disabled(store.isBusy || composer.isRunning)
        } label: {
            Image(systemName: "ellipsis")
        }
        .accessibilityLabel("More")
        .accessibilityIdentifier("newjob.more")
        .modifier(clearDialog(.menu))
    }

    /// The edited job's own seed. `!library.loaded` counts as "still exists":
    /// an unfetched library cannot say a seed is gone, only that it is unseen.
    @ViewBuilder private func seedBadge(_ draft: Draft) -> some View {
        if let seed = draft.tryonSeed {
            let exists = library.entries.contains { $0.id == seed } || !library.loaded
            Section {
                HStack(spacing: 10) {
                    Image(systemName: exists ? "photo.badge.checkmark" : "exclamationmark.triangle.fill")
                        .foregroundStyle(exists ? Theme.secondary : Theme.warning)
                    Text(exists
                         ? "Uses a saved try-on — Phase A skips the provider for this job"
                         : "The saved try-on no longer exists")
                        .font(.subheadline)
                    Spacer(minLength: 0)
                    Button("Remove") { Task { await store.apply(DraftPatch(seed: .clear)) } }
                        .font(.subheadline.weight(.semibold))
                        .buttonStyle(.borderless)
                        .tint(Theme.danger)
                        .disabled(store.isBusy)
                }
            }
        }
    }

    @ViewBuilder private func batch(_ draft: Draft) -> some View {
        if !draft.batch.isEmpty {
            Section {
                ForEach(draft.batch, id: \.digest) { entry in
                    HStack(alignment: .firstTextBaseline, spacing: 12) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(entry.digest)
                                .font(.body)
                                .lineLimit(1).truncationMode(.middle)
                            Text("\(entry.pipeline) · \(entry.provider)")
                                .font(.subheadline)
                                .foregroundStyle(Theme.secondary)
                            Text(slotSummary(entry))
                                .font(.footnote)
                                .foregroundStyle(Theme.secondary)
                                .lineLimit(2)
                            if entry.tryonSeed != nil {
                                Label("Saved try-on", systemImage: "photo.badge.checkmark")
                                    .font(.footnote).foregroundStyle(Theme.secondary)
                            }
                        }
                        Spacer(minLength: 0)
                        Button("Drop", role: .destructive) { dropCandidate = entry }
                            .font(.subheadline.weight(.semibold))
                            .buttonStyle(.borderless)
                            .tint(Theme.danger)
                            .disabled(store.isBusy || composer.isRunning)
                            // On the button, so the popover points at this row's Drop.
                            .confirmationDialog(
                                "Drop this batch entry?",
                                isPresented: Binding(
                                    get: { dropCandidate?.digest == entry.digest },
                                    set: { if !$0 { dropCandidate = nil } }),
                                titleVisibility: .visible
                            ) {
                                Button("Drop", role: .destructive) {
                                    dropCandidate = nil
                                    Task { await store.dropFromBatch(entry.digest) }
                                }
                                Button("Cancel", role: .cancel) { dropCandidate = nil }
                            } message: {
                                Text(entry.digest)
                            }
                    }
                }
            } header: {
                HStack {
                    // Its own `Text`: the smokes read "Batch · 2" by exact string.
                    Text("Batch · \(draft.batch.count)")
                    Spacer()
                    Button("Clear all") { clearSource = .batchHeader }
                        .font(.subheadline)
                        .buttonStyle(.borderless)
                        .tint(Theme.danger)
                        .disabled(store.isBusy || composer.isRunning)
                        .accessibilityIdentifier("newjob.clearAll")
                        .modifier(clearDialog(.batchHeader))
                }
            }
        }
    }

    private func slotSummary(_ entry: DraftBatchEntry) -> String {
        entry.slots.keys.sorted().map { role in
            let materialID: String? = entry.slots[role] ?? nil
            return "\(role): \(materialID ?? "empty")"
        }.joined(separator: " · ")
    }

    private func closePickerIfSelectionDisappeared() {
        guard let selectedRole,
              let materialID = store.draft?.slots[selectedRole]?.materialID,
              !materials.materials.contains(where: { $0.id == materialID }) else { return }
        self.selectedRole = nil
    }
}

@MainActor
struct SlotMaterialRow: View {
    let role: String
    let required: Bool
    let kind: PipelineRoleKind
    let slot: DraftSlot?
    let materials: MaterialsStore
    let disabled: Bool
    var tile = false
    let onTap: () -> Void
    @State private var thumbnail: Data?

    private var material: MotionKit.Material? {
        guard let materialID = slot?.materialID else { return nil }
        return materials.materials.first { $0.id == materialID }
    }

    var body: some View {
        Group {
            if tile {
                SlotTile(role: role, required: required, kind: kind, slot: slot,
                         thumbnail: thumbnail, disabled: disabled, onTap: onTap)
            } else {
                SlotRow(role: role, required: required, kind: kind, slot: slot,
                        thumbnail: thumbnail, disabled: disabled, onTap: onTap)
            }
        }
            .task(id: material?.id) {
                guard let material else {
                    thumbnail = nil
                    return
                }
                thumbnail = await materials.thumbnail(for: material)
            }
    }
}

/// Slot tiles in one row, three or four across, filling one `List` row, so a
/// whole job's materials show at a glance. Four fit because the try-on
/// pipelines top out at three required inputs plus an optional background;
/// at three columns that background wrapped onto a second row of its own and
/// pushed Settings back under the action bar.
struct SlotTileGrid<Content: View>: View {
    let count: Int
    @ViewBuilder let content: () -> Content

    var body: some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 12, alignment: .top),
                                 count: min(max(count, 3), 4)),
                  alignment: .leading, spacing: 16) {
            content()
        }
        .padding(.vertical, 12)
    }
}

/// The one Clear confirmation, attached to whichever control asked for it.
private struct ClearDraftDialog: ViewModifier {
    @Binding var isPresented: Bool
    let message: String
    let onClear: () -> Void

    func body(content: Content) -> some View {
        content.confirmationDialog("Clear the draft?", isPresented: $isPresented, titleVisibility: .visible) {
            Button("Clear", role: .destructive, action: onClear)
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(message)
        }
    }
}
