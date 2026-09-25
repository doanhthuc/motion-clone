import MotionKit
import SwiftUI

/// New Job's next step, pinned above the tab bar in both modes. Before
/// 2026-09-25 Single mode's Add, Validate and Continue were the last rows of
/// the list, under the provider rows and the materials, so reaching them
/// always meant scrolling to the end; Batch mode already pinned its Add here.
///
/// One filled button at a time, following the draft: Add while nothing counts
/// as a job yet, then Validate, then Continue. Add stays beside the other two
/// in Single mode, because another job can always be added before a run.
@MainActor
struct NewJobActionBar: View {
    let store: DraftStore
    let composer: BatchComposer
    let draft: Draft
    let isBatch: Bool
    let batchSupported: Bool
    let onContinue: () -> Void

    static func isVisible(draft: Draft, isBatch: Bool, batchSupported: Bool,
                          composer: BatchComposer) -> Bool {
        !isBatch || (batchSupported && BatchRunBar.isVisible(composer)) || draft.jobs > 0
    }

    private var showsComposer: Bool { isBatch && batchSupported && BatchRunBar.isVisible(composer) }

    /// Batch mode adds jobs through the composer, so the draft's own steps only
    /// appear once something is in the basket and no build is being composed.
    private var showsDraftSteps: Bool {
        !isBatch || (!(batchSupported && BatchRunBar.ownsAction(composer)) && draft.jobs > 0)
    }

    private var locked: Bool { store.isBusy || composer.isRunning }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if showsComposer { BatchRunBar(composer: composer) }
            if showsDraftSteps {
                status
                buttons
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
        // A container, so the UI smokes can tell a list row scrolled under the
        // bar from one above it (`Phase4Draft.revealButton`).
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("newjob.actionBar")
        .padding(.horizontal, 12)
        .padding(.bottom, 8)
    }

    @ViewBuilder private var status: some View {
        if store.isValidating {
            HStack(spacing: 10) {
                ProgressView()
                Text("Validating draft…").font(.subheadline)
            }
        } else if store.isReady {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.label)
                // Separate `Text`s: the smokes find "Ready" by its exact string.
                Text("Ready").font(.subheadline.weight(.semibold))
                if let estimate = draft.estimateMin {
                    Text("· about \(estimate) min")
                        .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                }
            }
        } else if store.validationWasStale {
            Label("The draft changed during validation. Validate it again.",
                  systemImage: "arrow.triangle.2.circlepath")
                .font(.subheadline)
                .foregroundStyle(Theme.warning)
        } else if !isBatch, draft.jobs == 0, !draft.missing.isEmpty {
            Text("Pick \(missingNames) to continue.")
                .font(.footnote)
                .foregroundStyle(Theme.secondary)
        }
    }

    private var missingNames: String {
        ListFormatter.localizedString(byJoining: draft.missing.map {
            SlotText(role: $0, required: true, kind: .unknown, slot: nil).title
        })
    }

    @ViewBuilder private var buttons: some View {
        if draft.jobs == 0 {
            addButton.buttonStyle(PrimaryButtonStyle())
        } else {
            HStack(spacing: 10) {
                if !isBatch {
                    addButton.buttonStyle(SecondaryButtonStyle())
                }
                if store.isReady {
                    Button("Continue to run", action: onContinue)
                        .buttonStyle(PrimaryButtonStyle())
                        .accessibilityIdentifier("newjob.continueToRun")
                        .disabled(store.isBusy)
                } else {
                    Button("Validate") { Task { await store.validate() } }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(locked)
                }
            }
        }
    }

    private var addButton: some View {
        Button("Add to batch") { Task { await store.addToBatch() } }
            .disabled(!draft.missing.isEmpty || locked)
    }
}
