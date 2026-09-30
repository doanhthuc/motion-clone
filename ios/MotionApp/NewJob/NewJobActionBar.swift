import MotionKit
import SwiftUI

/// New Job's next step, pinned above the tab bar (2026-09-26 spec §3). Two
/// buttons with fixed meanings. **Add N** queues what is being composed.
/// **Continue** runs everything on screen plus the basket: it adds a pending
/// composition first, then validates, then opens the run flow. Before this
/// change Validate was a step of its own between Add and "Continue to run",
/// and Batch mode's summary lived in a separate `BatchRunBar`.
@MainActor
struct NewJobActionBar: View {
    let store: DraftStore
    let composer: BatchComposer
    let materials: MaterialsStore
    let draft: Draft
    let state: NewJobState
    let pipeline: Pipeline
    let onContinue: () -> Void
    @State private var continuing = false
    /// The shortest of the multi-picked drivers: a length past it would be
    /// refused for that driver. nil until measured.
    @State private var shortestDriver: Double?

    private var locked: Bool { store.isBusy || composer.isRunning || continuing }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            lengthRow
            statusLines
            HStack(spacing: 10) {
                if state.addCount > 0 || composer.failure != nil { addButton }
                continueButton
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
        // A container, so the UI smokes can tell a card under the bar from one above it.
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("newjob.actionBar")
        .padding(.horizontal, 12)
        .padding(.bottom, 8)
    }

    private var hasDriver: Bool {
        pipeline.required.contains(BatchComposer.driverRole)
            || pipeline.optional.contains(BatchComposer.driverRole)
    }

    /// The output length, out of the Settings sheet where it was hard to find
    /// (2026-09-30). Only a pipeline with a driver video has a length to cut.
    @ViewBuilder private var lengthRow: some View {
        if hasDriver {
            // Drivers picked on the Driver card live in the composer, not the
            // draft, so their lengths are asked of the server and the picker is
            // bounded by the shortest one.
            let multi = !composer.drivers.isEmpty
            HStack(alignment: .top, spacing: 10) {
                Text("Length").font(.subheadline.weight(.semibold)).padding(.top, 6)
                DurationControl(current: multi ? composer.duration.seconds : draft.durationSec,
                                driverLengthSec: multi ? shortestDriver : draft.slots[BatchComposer.driverRole]?.probe.durationS,
                                allowedSeconds: pipeline.durations,
                                disabled: locked,
                                onSelect: { choice in
                                    if multi { composer.setDuration(choice) } else { await store.selectDuration(choice) }
                                })
            }
            .accessibilityIdentifier("newjob.length")
            .task(id: composer.drivers) { await measureDrivers() }
            .onChange(of: pipeline.id) { _, _ in dropUnsupportedLength() }
        }
    }

    /// A length picked under one pipeline may be refused by the next (camera takes
    /// only 5/10/15/20/30): fall back to Full rather than fail at Continue.
    private func dropUnsupportedLength() {
        if let allowed = pipeline.durations, let chosen = composer.duration.seconds, !allowed.contains(chosen) {
            composer.setDuration(.full)
        }
    }

    private func measureDrivers() async {
        var shortest: Double?
        for id in composer.drivers {
            guard let seconds = await materials.duration(ofMaterialID: id) else { shortestDriver = nil; return }
            shortest = min(shortest ?? seconds, seconds)
        }
        shortestDriver = shortest
        dropUnsupportedLength()
        // A length chosen for a longer driver set is no longer valid: fall back to Full.
        if let shortest, let chosen = composer.duration.seconds, Double(chosen) > shortest {
            composer.setDuration(.full)
        }
    }

    private func count(_ n: Int, _ noun: String) -> String { "\(n) \(noun)\(n == 1 ? "" : "s")" }

    @ViewBuilder private var statusLines: some View {
        if state.isBatch, !composer.outfits.isEmpty {
            Text("\(count(composer.outfits.count, "outfit")) × \(count(max(composer.drivers.count, 1), "driver")) = "
                 + "\(count(composer.jobCount, "video")) · \(count(composer.tryonCount, "try-on"))")
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .accessibilityIdentifier("batch.summary")
        }
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
            // Plain `Text`: the smokes find this line by its exact string.
            Text("Added \(added) job\(added == 1 ? "" : "s") to the batch.")
                .font(.subheadline).foregroundStyle(Theme.secondary)
        }
        if let capReason = composer.capReason {
            Text(capReason).font(.footnote).foregroundStyle(Theme.warning)
                .accessibilityIdentifier("batch.capReason")
        }
        if store.isReady, !continuing {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.label)
                // Separate `Text`s: the smokes find "Ready" by its exact string.
                Text("Ready").font(.subheadline.weight(.semibold))
                if let estimate = draft.estimateMin {
                    Text("· about \(estimate) min").font(.subheadline.monospacedDigit())
                        .foregroundStyle(Theme.secondary)
                }
            }
        } else if store.validationWasStale {
            Label("The draft changed during validation. Tap Continue again.",
                  systemImage: "arrow.triangle.2.circlepath")
                .font(.subheadline).foregroundStyle(Theme.warning)
        } else if !state.missing.isEmpty, !state.canContinue || !composer.outfits.isEmpty {
            Text("Pick \(missingNames) to continue.")
                .font(.footnote).foregroundStyle(Theme.secondary)
        }
    }

    private var missingNames: String {
        ListFormatter.localizedString(byJoining: state.missing.map {
            SlotText(role: $0, required: true, kind: .unknown, slot: nil).title
        })
    }

    /// "Add to batch" keeps its old label on single pipelines, which the
    /// Phase 3 smoke finds by name. On a try-on pipeline it names the job
    /// count, or "Resume" after a stopped build — not "Continue", which is
    /// the primary button beside it. The identifier `batch.run` stays on both.
    private var addButton: some View {
        let title = !state.isBatch ? "Add to batch"
            : composer.failure != nil ? "Resume"
            : "Add \(count(state.addCount, "job"))"
        return Button(title) { Task { await add() } }
            .buttonStyle(SecondaryButtonStyle())
            .disabled(locked || (state.isBatch ? !composer.canRun : state.addCount == 0))
            .accessibilityIdentifier(state.isBatch ? "batch.run" : "newjob.add")
    }

    private var continueButton: some View {
        Button { Task { await runContinue() } } label: {
            HStack(spacing: 8) {
                if continuing || store.isValidating { ProgressView().tint(Theme.onAccent) }
                Text("Continue")
            }
        }
        .buttonStyle(PrimaryButtonStyle())
        // Not blocked by `composer.failure`: a stopped build keeps its outfits,
        // so Continue resumes it first (and stops again if it fails), and a
        // failure after every job was added — the final slot clear — must not
        // leave both buttons dead (review, 2026-09-26).
        .disabled(locked || !state.canContinue)
        .accessibilityIdentifier("newjob.continueToRun")
    }

    @discardableResult
    private func add() async -> Bool {
        if state.isBatch {
            // `run()` returns early without a failure when it cannot run, which
            // must not read as success: Continue would then run the basket
            // without the outfits on screen.
            guard composer.canRun else { return false }
            await composer.run()
            return composer.failure == nil
        }
        return await store.addToBatch()
    }

    /// Add what is pending, validate, open the run flow — stopping at the
    /// first step that fails, whose message is already on screen (the
    /// composer's failure line, or the store's banner). A stopped build is
    /// resumed by the add, and a half-added batch never goes to validation.
    private func runContinue() async {
        continuing = true
        defer { continuing = false }
        if state.isBatch, !composer.outfits.isEmpty {
            guard await add() else { return }
        }
        if !store.isReady { await store.validate() }
        if store.isReady { onContinue() }
    }
}
