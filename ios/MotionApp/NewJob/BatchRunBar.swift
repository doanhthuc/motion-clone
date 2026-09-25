import MotionKit
import SwiftUI

/// Batch mode's summary and its one primary action, pinned above the tab bar
/// so "what am I about to add" stays on screen while the strips scroll. It was
/// the last row of a long list before 2026-09-25, out of sight until the end.
@MainActor
struct BatchRunBar: View {
    let composer: BatchComposer

    /// Nothing to say while nothing is picked and no build has run.
    static func isVisible(_ composer: BatchComposer) -> Bool {
        !composer.outfits.isEmpty || composer.isRunning || composer.failure != nil || composer.lastAdded != nil
    }

    private var summaryText: String {
        func count(_ n: Int, _ noun: String) -> String { "\(n) \(noun)\(n == 1 ? "" : "s")" }
        return "\(count(composer.outfits.count, "outfit")) × "
            + "\(count(max(composer.drivers.count, 1), "driver")) = "
            + "\(count(composer.jobCount, "video")) · \(count(composer.tryonCount, "try-on"))"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            statusLines
            // Hidden once a build has landed: success clears `outfits`, so the
            // label would read "Add 0 jobs to batch" on a disabled button. A
            // failure keeps it visible — that is the Continue a stopped run offers,
            // and a run in flight keeps it so the button does not vanish mid-build.
            if !composer.outfits.isEmpty || composer.failure != nil || composer.isRunning {
                Text(summaryText)
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .accessibilityIdentifier("batch.summary")
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
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .glassEffect(.regular, in: .rect(cornerRadius: 24))
        .padding(.horizontal, 12)
        .padding(.bottom, 8)
    }

    @ViewBuilder private var statusLines: some View {
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
        if !composer.missingShared.isEmpty {
            Text("Fill \(composer.missingShared.joined(separator: ", ")) first.")
                .font(.footnote).foregroundStyle(Theme.secondary)
        }
    }
}
