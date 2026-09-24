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
        .background(Theme.bg)
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
        // `matches` is empty.
        .onChange(of: composer.sharedSlots) { _, _ in composer.refreshSeeds() }
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
            ErrorBanner(error: error) { await store.load() }
        }
        .padding(.horizontal, 20)
    }

    private func editor(draft: Draft, pipeline: Pipeline) -> some View {
        let isBatch = model.newJobMode == .batch
        // Cross build needs a character + outfit pair, so Batch mode offers only the
        // pipelines that have one. When none qualify there is nothing to choose, so
        // the picker is disabled instead of opening an empty menu behind a label
        // that still names the pipeline the user is on.
        let batchCatalog = store.catalog.filter(BatchComposer.supports)
        return ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header(draft)
                Picker("Mode", selection: Binding(get: { model.newJobMode }, set: { model.newJobMode = $0 })) {
                    Text("Single").tag(NewJobMode.single)
                    Text("Batch").tag(NewJobMode.batch)
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("newjob.mode")
                .disabled(store.isBusy || composer.isRunning)
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
                                         onPickRole: { selectedRole = $0 })
                    clearAction
                } else {
                    slots(draft: draft, pipeline: pipeline)
                    seedBadge(draft)
                    readiness(draft)
                    editorActions(draft)
                }
                batch(draft)
                validation(draft)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 28)
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

    private func header(_ draft: Draft) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text("New Job")
                .font(Theme.sans(33, .bold))
                .foregroundStyle(Theme.ink)
            Spacer()
            if store.isStale { StaleTag(lastSuccess: store.lastSuccess) }
            Text("\(draft.jobs) jobs")
                .font(Theme.mono(11))
                .foregroundStyle(Theme.ink2)
        }
        .padding(.top, 6)
    }

    @ViewBuilder private var banners: some View {
        if let error = store.error {
            ErrorBanner(error: error) { await store.refresh() }
        }
        if let message = store.message, message != store.error?.userMessage {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "info.circle").foregroundStyle(Theme.amber)
                Text(message).font(Theme.sans(13)).foregroundStyle(Theme.ink1)
                Spacer(minLength: 0)
                Button("Dismiss") { store.dismissMessage() }
                    .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
            }
            .padding(12)
            .background(Theme.surface2, in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.line2))
        }
    }

    private func slots(draft: Draft, pipeline: Pipeline) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(text: "Materials")
            ForEach(pipeline.required, id: \.self) { role in
                SlotMaterialRow(
                    role: role,
                    required: true,
                    kind: pipeline.roles[role] ?? .unknown,
                    slot: draft.slots[role],
                    materials: materials,
                    disabled: store.isBusy) {
                        selectedRole = role
                    }
            }
            ForEach(pipeline.optional, id: \.self) { role in
                SlotMaterialRow(
                    role: role,
                    required: false,
                    kind: pipeline.roles[role] ?? .unknown,
                    slot: draft.slots[role],
                    materials: materials,
                    disabled: store.isBusy) {
                        selectedRole = role
                    }
            }
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
        return HStack(spacing: 8) {
            Image(systemName: ready ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                .foregroundStyle(ready ? Theme.lime : Theme.amber)
            Text(ready ? "Ready · \(assigned) of \(draft.required.count) required slots" : "\(assigned) of \(draft.required.count) required slots assigned")
                .font(Theme.sans(14, .semibold))
                .foregroundStyle(ready ? Theme.lime : Theme.ink1)
        }
        .padding(13)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(ready ? Theme.limeDim : Theme.surface2, in: .rect(cornerRadius: 13))
        .overlay(RoundedRectangle(cornerRadius: 13).strokeBorder(ready ? Theme.limeLine : Theme.line2))
        .accessibilityValue(ready ? "Ready" : "Missing required materials")
    }

    private func editorActions(_ draft: Draft) -> some View {
        HStack(spacing: 10) {
            Button("Add to batch") {
                Task { await store.addToBatch() }
            }
            .font(Theme.sans(14, .semibold))
            .foregroundStyle(Theme.limeInk)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 12)
            .background(Theme.lime, in: .rect(cornerRadius: 12))
            .disabled(!draft.missing.isEmpty || store.isBusy || composer.isRunning)

            clearAction
        }
    }

    /// One implementation for both arms, so they cannot drift. Batch mode had
    /// no Clear at all until 2026-09-24: `editorActions` — the only Clear — sat
    /// in the Single arm of `editor`'s if/else alone, so emptying the draft from
    /// Batch meant switching to Single first. That was not theoretical;
    /// `Phase6SmokeTests` had to do exactly it, twice.
    ///
    /// No accessibility identifier: the smokes find this by its `"Clear"` label
    /// (`Phase4Draft.revealButton`), as they did before, and an identifier
    /// nothing queries is noise.
    private var clearAction: some View {
        Button("Clear", role: .destructive) {
            Task { await store.clear() }
        }
        .font(Theme.sans(14, .semibold))
        .foregroundStyle(Theme.red)
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(Theme.redDim, in: .rect(cornerRadius: 12))
        .disabled(store.isBusy || composer.isRunning)
    }

    /// The edited job's own seed. `!library.loaded` counts as "still exists":
    /// an unfetched library cannot say a seed is gone, only that it is unseen.
    @ViewBuilder private func seedBadge(_ draft: Draft) -> some View {
        if let seed = draft.tryonSeed {
            HStack(spacing: 8) {
                Image(systemName: "photo.badge.checkmark").foregroundStyle(Theme.lime)
                Text(library.entries.contains { $0.id == seed } || !library.loaded
                     ? "Uses a saved try-on — Phase A skips the provider for this job"
                     : "The saved try-on no longer exists")
                    .font(Theme.sans(13)).foregroundStyle(Theme.ink1)
                Spacer(minLength: 0)
                Button("Remove") { Task { await store.apply(DraftPatch(seed: .clear)) } }
                    .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.red)
                    .disabled(store.isBusy)
            }
            .padding(12).card(border: Theme.limeLine)
        }
    }

    @ViewBuilder private func batch(_ draft: Draft) -> some View {
        if !draft.batch.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel(text: "Batch · \(draft.batch.count)")
                ForEach(draft.batch, id: \.digest) { entry in
                    HStack(alignment: .top, spacing: 10) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(entry.digest)
                                .font(Theme.mono(11, .medium))
                                .foregroundStyle(Theme.ink1)
                                .lineLimit(1)
                            Text(entry.runID)
                                .font(Theme.mono(10))
                                .foregroundStyle(Theme.ink2)
                                .lineLimit(1)
                            Text("\(entry.pipeline) · \(entry.provider)")
                                .font(Theme.mono(10))
                                .foregroundStyle(Theme.ink2)
                            Text(slotSummary(entry))
                                .font(Theme.mono(9))
                                .foregroundStyle(Theme.ink3)
                                .lineLimit(2)
                            if entry.tryonSeed != nil {
                                Text("Saved try-on").font(Theme.mono(9, .semibold)).foregroundStyle(Theme.lime)
                            }
                        }
                        Spacer(minLength: 0)
                        Button("Drop", role: .destructive) { dropCandidate = entry }
                            .font(Theme.sans(12, .semibold))
                            .foregroundStyle(Theme.red)
                            .disabled(store.isBusy || composer.isRunning)
                    }
                    .padding(12)
                    .card()
                }
            }
        }
    }

    @ViewBuilder private func validation(_ draft: Draft) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(text: "Validation")
            if store.isValidating {
                HStack(spacing: 9) {
                    ProgressView()
                    Text("Validating draft…")
                        .font(Theme.sans(13, .semibold))
                        .foregroundStyle(Theme.ink1)
                }
                .padding(13)
                .frame(maxWidth: .infinity, alignment: .leading)
                .card(border: Theme.limeLine)
            } else if store.isReady {
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.lime)
                    Text("Ready").font(Theme.sans(14, .semibold)).foregroundStyle(Theme.lime)
                    if let estimate = draft.estimateMin {
                        Text("· about \(estimate) min")
                            .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                    }
                }
                .padding(13)
                .frame(maxWidth: .infinity, alignment: .leading)
                .card(border: Theme.limeLine)
            } else if store.validationWasStale {
                Label("The draft changed during validation. Validate it again.", systemImage: "arrow.triangle.2.circlepath")
                    .font(Theme.sans(13, .medium))
                    .foregroundStyle(Theme.amber)
                    .padding(13)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .card(border: Theme.amber.opacity(0.4))
            }

            Button {
                Task { await store.validate() }
            } label: {
                Text("Validate")
                    .font(Theme.sans(14, .semibold))
                    .foregroundStyle(Theme.limeInk)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 13)
                    .background(Theme.lime, in: .rect(cornerRadius: 12))
            }
            .disabled(draft.jobs == 0 || store.isBusy || composer.isRunning)

            if store.isReady {
                NavigationLink {
                    RunFlowView(flow: flow, entry: .newJob)
                } label: {
                    Text("Continue to run →")
                        .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.ink1)
                        .frame(maxWidth: .infinity).padding(.vertical, 13)
                        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.limeLine))
                }
                .accessibilityIdentifier("newjob.continueToRun")
                .disabled(store.isBusy)
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
