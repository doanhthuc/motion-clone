import SwiftUI
import MotionKit

struct PodView: View {
    let pod: PodStore
    let gpu: GpuStore
    let balance: BalanceStore
    let flow: RunFlow
    let runs: RunsStore
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Pod").font(Theme.sans(33, .bold)).foregroundStyle(Theme.ink)
                if let error = pod.error, pod.pod == nil {
                    ErrorBanner(error: error) { await pod.refresh() }
                }
                leaseSection
                if let migration = pod.pod?.migration, migration.running {
                    MigrationCard(migration: migration)
                }
                SectionLabel(text: "Balance")
                BalanceCard(store: balance)
                SectionLabel(text: "GPU")
                GpuPickerView(store: gpu, spending: flow.isSpending, hasLease: pod.pod?.lease != nil,
                              onMigrate: { model.migrateSheet = MigrateRequest(destination: $0) })
                Button("Move volume…") { model.migrateSheet = MigrateRequest(destination: nil) }
                    .buttonStyle(SecondaryButtonStyle())
                    .accessibilityIdentifier("pod.moveVolume")
            }
            .padding(.horizontal, 20).padding(.bottom, 30)
        }
        .background(Theme.bg)
        .refreshable {
            async let a: Void = pod.refresh()
            async let b: Void = balance.load()
            async let c: Void = gpu.load(force: true)
            async let d: Void = runs.refresh()
            _ = await (a, b, c, d)
        }
        .task {
            async let a: Void = pod.refresh()
            async let b: Void = balance.load()
            async let c: Void = runs.refresh()
            if gpu.stock == nil { await gpu.load() }
            _ = await (a, b, c)
        }
        // Scoped to a visible Pod tab in an active scene; cancelled otherwise.
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await pod.pollMigration()
        }
    }

    @ViewBuilder private var leaseSection: some View {
        if let status = pod.pod {
            if pod.isStale {
                HStack {
                    StaleTag(lastSuccess: pod.lastSuccess)
                    Spacer()
                    Button("Retry") { Task { await pod.refresh() } }
                        .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                }
            }
            Group {
                if let lease = status.lease {
                    LeaseCard(gpu: status.gpu, lease: lease)
                } else {
                    Text("No pod running").font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink2)
                        .padding(16).frame(maxWidth: .infinity, alignment: .leading).card()
                        .accessibilityIdentifier("pod.none")
                }
            }
            .opacity(pod.isStale ? 0.6 : 1)
            // The server decides kill's own visibility/state; staleness of the
            // read never hides or disables it.
            if pod.showsKill(runStatus: runs.live?.status), let runID = status.runId {
                KillButton(pod: pod, runID: runID, hasLease: status.lease != nil)
            } else {
                KillNotice(pod: pod)
            }
        } else if pod.error == nil {
            ProgressView().frame(maxWidth: .infinity).padding(.vertical, 20)
        }
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
                    // The .env GPU names a RunPod card; a Vast lease has its own.
                    Text(lease.provider == "runpod" ? "RunPod · \(gpu)" : lease.provider)
                        .font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink)
                }
                Text(Format.clock(elapsed)).font(Theme.mono(36, .semibold)).foregroundStyle(Theme.ink)
                if let cost = CostEstimate.usd(elapsed: elapsed, ratePerHour: lease.quotedUsdPerHr) {
                    Text("≈ \(Format.usd(cost)) — a quote, not the invoice")
                        .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                }
            }
        }
        .padding(18).frame(maxWidth: .infinity, alignment: .leading)
        .card(radius: 20, border: Theme.limeLine)
    }
}

struct MigrationCard: View {
    let migration: PodMigration

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                PulseDot(color: Theme.amber)
                Text("Moving the volume" + (migration.toDc.map { " to \($0)" } ?? ""))
                    .font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink)
            }
            if let phase = migration.phase {
                Text(phase).font(Theme.mono(11)).foregroundStyle(Theme.ink2)
            }
            if let started = migration.startedAt {
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    Text(Format.clock(ctx.date.timeIntervalSince1970 - started))
                        .font(Theme.mono(13)).foregroundStyle(Theme.ink1)
                }
            }
            if let fraction = migration.fractionCopied {
                ProgressView(value: fraction).tint(Theme.amber)
            }
            Text("Progress is also posted in Telegram. A migration can't be cancelled.")
                .font(Theme.sans(12)).foregroundStyle(Theme.ink3)
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .card(border: Theme.amber.opacity(0.4))
    }
}

struct BalanceCard: View {
    let store: BalanceStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let line = store.runpodLine {
                let low = store.balance?.runpod?.lowRunway == true
                let refreshFailed = store.error != nil
                VStack(alignment: .leading, spacing: 8) {
                    Text("RunPod").font(Theme.mono(11)).foregroundStyle(Theme.ink3)
                    Text(line).font(Theme.mono(15, .semibold)).foregroundStyle(low ? Theme.amber : Theme.ink)
                    if low {
                        Text("Under 1 h of runway — top up before renting.")
                            .font(Theme.sans(12)).foregroundStyle(Theme.amber)
                    }
                }
                .opacity(refreshFailed ? 0.6 : 1)
                if refreshFailed, let error = store.error {
                    Text("Couldn't refresh — \(error.userMessage)")
                        .font(Theme.sans(12)).foregroundStyle(Theme.amber)
                }
            } else if let error = store.error {
                ErrorBanner(error: error) { await store.load() }
            } else {
                ProgressView().frame(maxWidth: .infinity)
            }
            if let vast = store.vastLine {
                Text("Vast").font(Theme.mono(11)).foregroundStyle(Theme.ink3)
                Text(vast).font(Theme.mono(15, .semibold)).foregroundStyle(Theme.ink)
            }
            ForEach(store.balance?.errors ?? [], id: \.self) { text in
                Text(text).font(Theme.sans(12)).foregroundStyle(Theme.amber)
            }
            Button { Task { await store.loadVast() } } label: {
                HStack(spacing: 8) {
                    if store.isLoadingVast { ProgressView().controlSize(.small) }
                    Text(store.isLoadingVast ? "Reading the Vast credit (~30 s)…" : "Check Vast credit")
                }
            }
            .buttonStyle(SecondaryButtonStyle())
            .disabled(store.isLoadingVast)
            .accessibilityIdentifier("pod.checkVast")
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading).card()
    }
}
