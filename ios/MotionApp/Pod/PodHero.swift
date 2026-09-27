import SwiftUI
import MotionKit

/// The top of the Pod stage: what is billing right now, and what it can
/// still spend. One surface; a migration replaces it while it runs.
struct PodHero: View {
    let pod: PodStore
    let balance: BalanceStore
    let runs: RunsStore
    let onBalance: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let migration = pod.pod?.migration, migration.running {
                MigrationCard(migration: migration)
            } else if let status = pod.pod, let lease = status.lease {
                LeaseCard(gpu: status.gpu, lease: lease)
            } else if pod.pod != nil {
                Label("No pod running", systemImage: "moon.zzz")
                    .font(.headline)
                    .foregroundStyle(Theme.secondary)
                    .accessibilityIdentifier("pod.none")
            } else if let error = pod.error {
                ErrorBanner(error: error) { await pod.refresh() }
            } else {
                LoadingBlock()
            }
            balanceLine
            if let status = pod.pod {
                if pod.showsKill(runStatus: runs.live?.status), let runID = status.runId {
                    KillButton(pod: pod, runID: runID, hasLease: status.lease != nil)
                } else {
                    KillNotice(pod: pod)
                }
            }
            if pod.isStale {
                HStack {
                    StaleTag(lastSuccess: pod.lastSuccess)
                    Spacer()
                    Button("Retry") { Task { await pod.refresh() } }.buttonStyle(.borderless)
                }
            }
        }
        .opacity(pod.isStale ? 0.6 : 1)
        .heroSurface()
        .accessibilityIdentifier("pod.hero")
    }

    /// Runway is the number that decides whether to rent; the rest of the
    /// balance (Vast credit, errors) is one tap away.
    private var balanceLine: some View {
        Button(action: onBalance) {
            HStack(spacing: 6) {
                Image(systemName: "creditcard").foregroundStyle(Theme.secondary)
                Text(balance.runpodLine ?? (balance.error == nil ? "Reading balance…" : "Balance unavailable"))
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(balance.balance?.runpod?.lowRunway == true ? Theme.warning : Theme.label)
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("pod.balance")
    }
}

struct LeaseCard: View {
    let gpu: String
    let lease: PodLease

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { ctx in
            let elapsed = ctx.date.timeIntervalSince1970 - lease.provisionedAt
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    PulseDot()
                    Text("Live on " + (lease.provider == "runpod" ? "RunPod" : lease.provider))
                        .font(.headline)
                }
                // `.env`'s GPU is what the next rental uses; a change made
                // mid-lease doesn't move the leased card, so it isn't named as it.
                if lease.provider == "runpod" {
                    Text("Next rental: \(gpu)").font(.subheadline).foregroundStyle(Theme.secondary)
                }
                Text(Format.clock(elapsed)).font(.largeTitle.weight(.semibold).monospacedDigit()).foregroundStyle(Theme.label)
                if let cost = CostEstimate.usd(elapsed: elapsed, ratePerHour: lease.quotedUsdPerHr) {
                    Text("≈ \(Format.usd(cost)) — a quote, not the invoice")
                        .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                }
            }
        }
    }
}

struct MigrationCard: View {
    let migration: PodMigration

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                PulseDot(color: Theme.warning)
                Text("Moving the volume" + (migration.toDc.map { " to \($0)" } ?? ""))
                    .font(.headline)
            }
            if let phase = migration.phase {
                Text(phase).font(.subheadline).foregroundStyle(Theme.secondary)
            }
            if let started = migration.startedAt {
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    Text(Format.clock(ctx.date.timeIntervalSince1970 - started))
                        .font(.body.monospacedDigit())
                }
            }
            if let fraction = migration.fractionCopied {
                ProgressView(value: fraction).tint(Theme.warning)
            }
            Text("Progress is also posted in Telegram. A migration can't be cancelled.")
                .font(.footnote).foregroundStyle(Theme.secondary)
        }
        .padding(.vertical, 4)
    }
}
