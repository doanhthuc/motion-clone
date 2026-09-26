import MotionKit
import SwiftUI

/// New Job as one stage that does not scroll (2026-09-26 spec): the job's
/// inputs as cards sized to the screen, the pipeline and provider in a
/// toolbar chip, the basket in a drawer, and Add / Continue pinned above the
/// tab bar. It replaces a `List` with a Single | Batch switch: in Batch mode
/// the shared slots, two strips, Settings and the basket stacked past the
/// screen, and the mode itself was one more thing to hold in mind. The mode
/// is now what is picked. Outfit and Driver take many on a try-on pipeline,
/// and one of each is one job.
@MainActor
struct NewJobView: View {
    let store: DraftStore
    let materials: MaterialsStore
    let flow: RunFlow
    let composer: BatchComposer
    let library: TryonLibraryStore
    @State private var pick: PickTarget?
    @State private var openEntry: DraftBatchEntry?
    @State private var showRun = false
    @State private var basketExpanded = false
    @State private var clearSource: ClearSource?

    var body: some View {
        Group {
            if let draft = store.draft, let pipeline = store.selectedPipeline {
                stage(draft: draft, pipeline: pipeline)
            } else if let error = store.error {
                initialLoadFailure(error)
            } else if store.isRefreshing {
                LoadingBlock(title: "Loading draft…")
            } else {
                ContentUnavailableView("New Job unavailable", systemImage: "exclamationmark.triangle")
            }
        }
        .navigationTitle("New Job")
        .navigationBarTitleDisplayMode(.inline)
        .task { await store.load() }
        .task { await library.load() }
        .onChange(of: store.needsMaterialsRefresh) { _, needsRefresh in
            guard needsRefresh else { return }
            Task {
                await materials.refresh()
                store.acknowledgeMaterialsRefresh()
            }
        }
        // Kept from before the mode split was removed, for the reason it was
        // written: a seed picked for the old character/outfit pair must never
        // be PATCHed for a new one. `library.loaded` because `matches(for:)` is
        // empty against an unfetched library.
        .onChange(of: composer.seedKey) { _, _ in composer.refreshSeeds() }
        .onChange(of: library.loaded) { _, loaded in
            if loaded { composer.refreshSeeds() }
        }
        // A crossed role on the draft (Saved try-ons' "Use in job", the
        // Telegram bot, a pre-redesign draft) moves into the composer, the
        // only place the Outfit and Driver cards read.
        .task(id: adoptKey) { await composer.adoptDraftSelection() }
    }

    private var adoptKey: [String] {
        [store.draft?.pipeline ?? "", store.draft?.filledSlots[BatchComposer.outfitRole] ?? "",
         store.draft?.filledSlots[BatchComposer.driverRole] ?? ""]
    }

    private var locked: Bool { store.isBusy || composer.isRunning }

    private func state(_ draft: Draft, _ pipeline: Pipeline) -> NewJobState {
        NewJobState(pipeline: pipeline, draft: draft,
                    outfits: composer.outfits.count, drivers: composer.drivers.count)
    }

    private func stage(draft: Draft, pipeline: Pipeline) -> some View {
        let state = state(draft, pipeline)
        return GeometryReader { proxy in
            SlotCardGrid(count: state.cards.count) { size in
                ForEach(state.cards, id: \.self) { card in
                    slotCard(card, state: state, draft: draft, pipeline: pipeline, size: size)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .overlay(alignment: .bottom) {
                if !draft.batch.isEmpty {
                    BasketDrawer(batch: draft.batch, pipeline: self.pipeline(for:), materials: materials,
                                 locked: locked, expanded: $basketExpanded,
                                 onOpen: { openEntry = $0 },
                                 onDrop: { await store.dropFromBatch($0.digest) },
                                 clearAll: AnyView(clearAllButton))
                        .frame(maxHeight: basketExpanded ? proxy.size.height * 0.7 : nil, alignment: .bottom)
                        .padding(.horizontal, 12)
                }
            }
            .overlay(alignment: .top) { banners.padding(.horizontal, 12) }
        }
        .toolbar {
            ToolbarItem(placement: .principal) {
                SettingsChip(pipeline: pipeline, pipelines: store.catalog, selectedProvider: draft.provider,
                             disabled: locked,
                             onPipelineSelected: { id in
                                 await store.selectPipeline(id)
                                 if let selected = store.selectedPipeline, !BatchComposer.supports(selected) {
                                     composer.reset()
                                 }
                             },
                             onProviderSelected: { id in await store.selectProvider(id) })
            }
            ToolbarItem(placement: .topBarLeading) {
                Text("\(draft.jobs) job\(draft.jobs == 1 ? "" : "s")")
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle(draft.jobs > 0 ? Theme.accent : .secondary)
                    .fixedSize()
                    .contentTransition(.numericText())
                    .animation(.snappy, value: draft.jobs)
            }
            // A count, not a control: without this iOS 26 wraps it in glass.
            .sharedBackgroundVisibility(.hidden)
            ToolbarItem(placement: .topBarTrailing) { moreMenu }
        }
        .safeAreaInset(edge: .bottom) {
            NewJobActionBar(store: store, composer: composer, draft: draft, state: state,
                            onContinue: { showRun = true })
        }
        .sheet(item: $pick) { target in
            PickerChainSheet(start: target, pipeline: pipeline, store: store, composer: composer,
                             materials: materials, onClose: { pick = nil })
        }
        .sheet(item: $openEntry) { entry in
            let index = (store.draft?.batch.firstIndex { $0.digest == entry.digest } ?? 0) + 1
            BatchEntryDetail(index: index, entry: entry, pipeline: self.pipeline(for: entry),
                             materials: materials, library: library, dropDisabled: locked,
                             onDrop: { await store.dropFromBatch(entry.digest) })
        }
        .navigationDestination(isPresented: $showRun) { RunFlowView(flow: flow, entry: .newJob) }
    }

    private func slotCard(_ card: NewJobState.Card, state: NewJobState, draft: Draft,
                          pipeline: Pipeline, size: CGSize) -> some View {
        let role = state.role(of: card)
        let items: [SlotCardItem]? = switch card {
        case .single: nil
        case .outfits: composer.outfits.map { SlotCardItem(id: $0.outfitID, seeded: $0.seedID != nil) }
        case .drivers: composer.drivers.map { SlotCardItem(id: $0, seeded: false) }
        }
        return SlotCard(
            role: role, required: state.required.contains(role), kind: pipeline.roles[role] ?? .unknown,
            // A Driver card with nothing multi-picked still shows the shared
            // driver slot, which is what one job with no driver list runs on.
            slot: draft.slots[role], items: (card == .drivers && composer.drivers.isEmpty) ? nil : items,
            materials: materials, disabled: locked,
            identifier: card == .outfits ? "batch.pickOutfits" : card == .drivers ? "batch.pickDrivers" : nil,
            size: size,
            onTap: { pick = PickTarget(card: card, chained: state.isFresh) },
            menu: { item in AnyView(cardMenu(card, role: role, item: item)) })
    }

    @ViewBuilder private func cardMenu(_ card: NewJobState.Card, role: String, item: SlotCardItem?) -> some View {
        switch card {
        case .single:
            Button("Clear", systemImage: "xmark.circle", role: .destructive) {
                Task { await store.assign(role: role, materialID: nil) }
            }
        case .outfits:
            if let item {
                let matches = composer.matches(for: item.id)
                let seed = composer.outfits.first { $0.outfitID == item.id }?.seedID
                Toggle("Use saved try-on", systemImage: "photo.badge.checkmark", isOn: Binding(
                    get: { seed != nil },
                    set: { composer.setSeed($0 ? matches.first?.id : nil, for: item.id) }))
                    .disabled(matches.isEmpty)
                    .accessibilityIdentifier("batch.seed.\(item.id)")
                if matches.count > 1, seed != nil {
                    Picker("Saved image", selection: Binding(
                        get: { seed ?? "" }, set: { composer.setSeed($0, for: item.id) })) {
                        ForEach(matches) { entry in
                            Text("\(entry.provider) · \(Date(timeIntervalSince1970: entry.savedAt).formatted(date: .abbreviated, time: .shortened))")
                                .tag(entry.id)
                        }
                    }
                    .pickerStyle(.menu)
                }
                if matches.isEmpty { Text("No saved try-on for this pair — Phase A will make one.") }
                Divider()
                Button("Remove", systemImage: "trash", role: .destructive) { composer.toggle(outfitID: item.id) }
            }
        case .drivers:
            if let item {
                Button("Remove", systemImage: "trash", role: .destructive) { composer.toggle(driverID: item.id) }
            } else {
                Button("Clear", systemImage: "xmark.circle", role: .destructive) {
                    Task { await store.assign(role: role, materialID: nil) }
                }
            }
        }
    }

    @ViewBuilder private var banners: some View {
        VStack(spacing: 8) {
            if let error = store.error {
                ErrorBanner(error: error) { await store.refresh() }
            }
            if let message = store.message, message != store.error?.userMessage {
                MessageCard(text: message) { store.dismissMessage() }
            }
        }
        .animation(.snappy, value: store.message)
    }

    private func initialLoadFailure(_ error: APIError) -> some View {
        VStack(spacing: 16) {
            ContentUnavailableView("New Job unavailable", systemImage: "exclamationmark.triangle",
                                   description: Text("The draft and pipeline catalog could not be loaded."))
            ErrorBanner(error: error) { await store.load() }.heroSurface()
        }
        .padding(.horizontal, 16)
    }

    // MARK: Clear

    /// Where Clear was asked from. Each source carries its own dialog: on
    /// iOS 26 a confirmation dialog is a popover pointing at its anchor.
    private enum ClearSource { case menu, basket }

    private func confirmsClear(_ source: ClearSource) -> Binding<Bool> {
        Binding(get: { clearSource == source }, set: { if !$0 { clearSource = nil } })
    }

    private func clearDialog(_ source: ClearSource) -> some ViewModifier {
        ClearDraftDialog(isPresented: confirmsClear(source), message: clearMessage) {
            // The selection lives outside the draft since 2026-09-26, so the
            // server's clear alone would leave the Outfit and Driver cards full.
            composer.reset()
            Task { await store.clear() }
        }
    }

    private var clearMessage: String {
        let queued = store.draft?.batch.count ?? 0
        return queued == 0
            ? "Every picked material is removed."
            : "\(queued) job\(queued == 1 ? "" : "s") in the batch and every picked material are removed."
    }

    private var moreMenu: some View {
        // Refresh lives here rather than as its own bar button: a bar item that
        // came and went with `isStale` crowded the chip, and iOS 26 then dropped
        // the whole trailing group, this menu with it (Phase 3 smoke,
        // 2026-09-26). The stage has no pull to refresh, so this is also the
        // manual reload. The icon turns to a warning while the draft is stale.
        Menu {
            Button("Refresh", systemImage: "arrow.clockwise") { Task { await store.refresh() } }
            Button("Clear draft", systemImage: "trash", role: .destructive) { clearSource = .menu }
                .disabled(locked)
        } label: {
            Image(systemName: store.isStale ? "exclamationmark.arrow.circlepath" : "ellipsis")
                .foregroundStyle(store.isStale ? Theme.warning : Theme.label)
        }
        .accessibilityLabel("More")
        .accessibilityValue(store.isStale ? "Draft may be out of date" : "")
        .accessibilityIdentifier("newjob.more")
        .modifier(clearDialog(.menu))
    }

    private var clearAllButton: some View {
        Button("Clear all") { clearSource = .basket }
            .font(.subheadline)
            .buttonStyle(.borderless)
            .tint(Theme.danger)
            .disabled(locked)
            .accessibilityIdentifier("newjob.clearAll")
            .modifier(clearDialog(.basket))
    }

    private func pipeline(for entry: DraftBatchEntry) -> Pipeline? {
        store.catalog.first { $0.id == entry.pipeline }
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
