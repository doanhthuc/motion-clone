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

    var body: some View {
        Group {
            if let draft = store.draft, let pipeline = store.selectedPipeline {
                editor(draft: draft, pipeline: pipeline)
            } else if let error = store.error {
                initialLoadFailure(error)
            } else if store.isRefreshing {
                ProgressView("Loading draft…")
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
        .confirmationDialog(
            "Drop this batch entry?",
            isPresented: Binding(
                get: { dropCandidate != nil },
                set: { if !$0 { dropCandidate = nil } }),
            titleVisibility: .visible
        ) {
            Button("Drop", role: .destructive) {
                guard let candidate = dropCandidate else { return }
                dropCandidate = nil
                Task { await store.dropFromBatch(candidate.digest) }
            }
            Button("Cancel", role: .cancel) { dropCandidate = nil }
        } message: {
            Text(dropCandidate?.digest ?? "")
        }
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
            PipelinePicker(
                pipeline: pipeline,
                pipelines: isBatch ? batchCatalog : store.catalog,
                selectedProvider: draft.provider,
                disabled: store.isBusy || composer.isRunning || (isBatch && batchCatalog.isEmpty),
                onPipelineSelected: { id in await store.selectPipeline(id) },
                onProviderSelected: { id in await store.selectProvider(id) })
            if isBatch {
                BatchComposerSection(store: store, composer: composer,
                                     materials: materials, pipeline: pipeline,
                                     onPickRole: { selectedRole = $0 },
                                     pickingOutfits: $pickingOutfits,
                                     pickingDrivers: $pickingDrivers)
            } else {
                slots(draft: draft, pipeline: pipeline)
                seedBadge(draft)
                editorActions(draft)
            }
            batch(draft)
            validation(draft)
        }
        .navigationSubtitle("\(draft.jobs) jobs")
        .toolbar {
            ToolbarItem(placement: .topBarLeading) { clearAction }
            if store.isStale {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { Task { await store.refresh() } } label: {
                        Image(systemName: "clock.arrow.circlepath")
                    }
                    .tint(Theme.warning)
                    .accessibilityLabel("Stale — refresh")
                }
            }
        }
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
            ForEach(pipeline.required + pipeline.optional, id: \.self) { role in
                SlotMaterialRow(
                    role: role,
                    required: pipeline.required.contains(role),
                    kind: pipeline.roles[role] ?? .unknown,
                    slot: draft.slots[role],
                    materials: materials,
                    disabled: store.isBusy) {
                        selectedRole = role
                    }
            }
        } header: {
            Text("Materials")
        } footer: {
            readiness(draft)
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

    /// The draft's next step gets the one filled button: add while the basket is
    /// empty, then validate, then continue (see `validation`).
    private func editorActions(_ draft: Draft) -> some View {
        Section {
            Button("Add to batch") {
                Task { await store.addToBatch() }
            }
            .buttonStyle(draft.jobs == 0 ? AnyButtonStyle(PrimaryButtonStyle()) : AnyButtonStyle(SecondaryButtonStyle()))
            .disabled(!draft.missing.isEmpty || store.isBusy || composer.isRunning)
            .buttonRow()
        }
    }

    /// One implementation for both arms, so they cannot drift. Batch mode had
    /// no Clear at all until 2026-09-24: `editorActions` — the only Clear — sat
    /// in the Single arm of `editor`'s if/else alone, so emptying the draft from
    /// Batch meant switching to Single first. That was not theoretical;
    /// `Phase6SmokeTests` had to do exactly it, twice. It now lives in the
    /// navigation bar, away from the add button it used to sit beside at equal weight.
    ///
    /// No accessibility identifier: the smokes find this by its `"Clear"` label
    /// (`Phase4Draft.revealButton`), as they did before, and an identifier
    /// nothing queries is noise.
    private var clearAction: some View {
        Button("Clear", role: .destructive) {
            Task { await store.clear() }
        }
        .tint(Theme.danger)
        .disabled(store.isBusy || composer.isRunning)
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
            Section("Batch · \(draft.batch.count)") {
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
                    }
                }
            }
        }
    }

    @ViewBuilder private func validation(_ draft: Draft) -> some View {
        Section {
            if store.isValidating {
                HStack(spacing: 10) {
                    ProgressView()
                    Text("Validating draft…").font(.subheadline)
                }
            } else if store.isReady {
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.label)
                    Text("Ready").font(.headline)
                    if let estimate = draft.estimateMin {
                        Text("· about \(estimate) min")
                            .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                    }
                }
            } else if store.validationWasStale {
                Label("The draft changed during validation. Validate it again.", systemImage: "arrow.triangle.2.circlepath")
                    .font(.subheadline)
                    .foregroundStyle(Theme.warning)
            }

            Button("Validate") {
                Task { await store.validate() }
            }
            .buttonStyle(draft.jobs > 0 && !store.isReady
                         ? AnyButtonStyle(PrimaryButtonStyle()) : AnyButtonStyle(SecondaryButtonStyle()))
            .disabled(draft.jobs == 0 || store.isBusy || composer.isRunning)
            .buttonRow()

            if store.isReady {
                Button("Continue to run") { showRun = true }
                    .buttonStyle(PrimaryButtonStyle())
                    .accessibilityIdentifier("newjob.continueToRun")
                    .disabled(store.isBusy)
                    .buttonRow()
            }
        } header: {
            Text("Validation")
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
    let onTap: () -> Void
    @State private var thumbnail: Data?

    private var material: MotionKit.Material? {
        guard let materialID = slot?.materialID else { return nil }
        return materials.materials.first { $0.id == materialID }
    }

    var body: some View {
        SlotRow(
            role: role,
            required: required,
            kind: kind,
            slot: slot,
            thumbnail: thumbnail,
            disabled: disabled,
            onTap: onTap)
            .task(id: material?.id) {
                guard let material else {
                    thumbnail = nil
                    return
                }
                thumbnail = await materials.thumbnail(for: material)
            }
    }
}
