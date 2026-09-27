import MotionKit
import SwiftUI

/// The run flow's first stop: what is about to run, one compact row per job,
/// and the two ways on pinned above the tab bar. Until 2026-09-27 it was a
/// `List` reading "3 jobs in the draft." with the buttons in a Section, which
/// clipped them to the Section's corners (larger radius on top than below)
/// and drew a separator under the first one (user, 2026-09-27).
///
/// A row opens the same Job sheet as New Job's basket, read-only: an edit
/// here would reset the draft's validation under a screen about to rent.
@MainActor
struct RunComposeView<Notices: View>: View {
    let flow: RunFlow
    let draftStore: DraftStore
    let materials: MaterialsStore
    let library: TryonLibraryStore
    @ViewBuilder let notices: Notices
    @State private var openPosition: OpenedPosition?

    var body: some View {
        let draft = flow.draft
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                notices
                if let draft {
                    header(draft)
                    jobList(draft)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
        }
        // A handful of jobs fits; only a long batch scrolls.
        .scrollBounceBehavior(.basedOnSize)
        .background(Theme.bg)
        .safeAreaInset(edge: .bottom) { actionBar }
        // The sheet reads the job by position from the draft store, so that
        // store must hold the same draft the flow just loaded.
        .task { await draftStore.load() }
        .sheet(item: $openPosition) { opened in
            BatchEntryDetail(position: opened.position, store: draftStore,
                             pipelineFor: pipeline(for:), materials: materials,
                             library: library, locked: true, readOnly: true)
        }
    }

    private func header(_ draft: Draft) -> some View {
        HStack(spacing: 6) {
            Text("\(draft.jobs) job\(draft.jobs == 1 ? "" : "s")")
                .font(.headline.monospacedDigit())
            if let estimate = draft.estimateMin {
                Text("· about \(estimate) min")
                    .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("runflow.summary")
    }

    private func jobList(_ draft: Draft) -> some View {
        VStack(spacing: 0) {
            ForEach(Array(draft.batch.enumerated()), id: \.element.digest) { offset, entry in
                if offset > 0 { Divider().padding(.leading, 40) }
                RunJobRow(index: offset + 1,
                          roles: BatchEntryText.roles(entry, pipeline: pipeline(for: entry)),
                          slots: entry.filledSlots, pipeline: pipeline(for: entry),
                          title: PipelineText.name(entry.pipeline),
                          provider: BatchEntryText.provider(entry, pipeline: pipeline(for: entry)),
                          materials: materials, showsSeed: entry.tryonSeed != nil,
                          onOpen: { openPosition = OpenedPosition(position: offset) })
            }
            // The job being edited runs too when it is complete (`drafts.py`
            // `_jobs`), but it has no basket position for the Job sheet.
            if draft.jobs > draft.batch.count, draft.missing.isEmpty {
                if !draft.batch.isEmpty { Divider().padding(.leading, 40) }
                let pipeline = flow.catalog.first { $0.id == draft.pipeline }
                RunJobRow(index: draft.batch.count + 1,
                          roles: (pipeline.map { $0.required + $0.optional } ?? draft.slots.keys.sorted())
                              .filter { draft.filledSlots[$0] != nil },
                          slots: draft.filledSlots, pipeline: pipeline,
                          title: PipelineText.name(draft.pipeline), provider: providerName(draft, pipeline),
                          materials: materials, showsSeed: draft.tryonSeed != nil, onOpen: nil)
            }
        }
        .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("runflow.jobs")
    }

    private var actionBar: some View {
        RunActionBar {
            if flow.hasLocalTryon {
                Text("Preview spends Gemini/Qwen quota — no pod is rented.")
                    .font(.footnote).foregroundStyle(Theme.secondary)
                Button("Preview try-on") { Task { await flow.startPhaseA() } }
                    .buttonStyle(PrimaryButtonStyle())
                    .accessibilityIdentifier("runflow.previewTryon")
                    .disabled(!flow.canSpend)
            }
            Button("Rent without preview") { Task { await flow.continueToRent() } }
                .buttonStyle(flow.hasLocalTryon ? AnyButtonStyle(SecondaryButtonStyle()) : AnyButtonStyle(PrimaryButtonStyle()))
                .accessibilityIdentifier("runflow.rentWithoutPreview")
                .disabled(flow.isSpending)
            if !(flow.tryon?.previews.isEmpty ?? true) {
                Button("View last previews") { Task { await flow.start(.existing) } }
                    .buttonStyle(SecondaryButtonStyle())
            }
        }
    }

    private func pipeline(for entry: DraftBatchEntry) -> Pipeline? {
        flow.catalog.first { $0.id == entry.pipeline }
    }

    private func providerName(_ draft: Draft, _ pipeline: Pipeline?) -> String {
        guard let label = pipeline?.providers.first(where: { $0.id == draft.provider })?.label else {
            return draft.provider
        }
        return ProviderText(label: label).name
    }
}

/// One job on one line: number, its materials at 40 pt, pipeline · provider.
@MainActor
private struct RunJobRow: View {
    let index: Int
    let roles: [String]
    let slots: [String: String]
    let pipeline: Pipeline?
    let title: String
    let provider: String
    let materials: MaterialsStore
    let showsSeed: Bool
    /// nil for the job being edited, which has no Job sheet.
    let onOpen: (() -> Void)?

    var body: some View {
        HStack(spacing: 10) {
            Text("\(index)")
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .foregroundStyle(Theme.secondary)
                .frame(width: 18)
            HStack(spacing: 4) {
                ForEach(roles, id: \.self) { role in
                    BatchEntryMaterialTile(role: role, materialID: slots[role],
                                           kind: pipeline?.roles[role] ?? .unknown,
                                           materials: materials, width: 40,
                                           showsSeed: showsSeed && role == "outfit",
                                           showsTitle: false)
                }
            }
            // Two lines: on one, a long pipeline name hid the provider.
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.subheadline).lineLimit(2)
                Text(provider).font(.caption).foregroundStyle(Theme.secondary).lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if onOpen != nil {
                Image(systemName: "chevron.right")
                    .font(.footnote.weight(.semibold)).foregroundStyle(Theme.tertiary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        // Tiles keep their long-press peek; a tap anywhere opens the job.
        .contentShape(.rect)
        .onTapGesture { onOpen?() }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Job \(index), \(title), \(provider)")
        .accessibilityAction(named: "Show details") { onOpen?() }
    }
}

private struct OpenedPosition: Identifiable {
    let position: Int
    var id: Int { position }
}

/// The run flow's actions, pinned above the tab bar on every step that has
/// them (compose, rent panel, reuse/re-run). In a `List` Section a button
/// took the Section's corners — a larger radius on its top than its bottom
/// when two shared a Section (user, 2026-09-27).
struct RunActionBar<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) { content }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)
            .glassEffect(.regular, in: .rect(cornerRadius: 24))
            .accessibilityElement(children: .contain)
            .padding(.horizontal, 12)
            .padding(.bottom, 8)
    }
}
