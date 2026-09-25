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
        List {
            if let error = pod.error, pod.pod == nil {
                Section { ErrorBanner(error: error) { await pod.refresh() } }
            }
            leaseSection
            if let migration = pod.pod?.migration, migration.running {
                Section { MigrationCard(migration: migration) }
            }
            BalanceSection(store: balance)
            GpuPickerView(store: gpu, spending: flow.isSpending, hasLease: pod.pod?.lease != nil,
                          onMigrate: { model.migrateSheet = MigrateRequest(destination: $0) })
            Section {
                Button("Move volume…") { model.migrateSheet = MigrateRequest(destination: nil) }
                    .accessibilityIdentifier("pod.moveVolume")
            } footer: {
                Text("Copies the Network Volume to another datacenter.")
            }
        }
        .navigationTitle("Pod")
        .navigationBarTitleDisplayMode(.inline)
        // An inline bar already separates the first card from the top; the
        // inset-grouped default added ~35pt of empty band under it.
        .contentMargins(.top, 8, for: .scrollContent)
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
            if let lease = status.lease {
                Section {
                    LeaseCard(gpu: status.gpu, lease: lease)
                        .opacity(pod.isStale ? 0.6 : 1)
                }
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
            }
            Section {
                if status.lease == nil {
                    Label("No pod running", systemImage: "moon.zzz")
                        .foregroundStyle(Theme.secondary)
                        .opacity(pod.isStale ? 0.6 : 1)
                        .accessibilityIdentifier("pod.none")
                }
                if pod.isStale {
                    HStack {
                        StaleTag(lastSuccess: pod.lastSuccess)
                        Spacer()
                        Button("Retry") { Task { await pod.refresh() } }.buttonStyle(.borderless)
                    }
                }
                // The server decides kill's own visibility/state; staleness of the
                // read never hides or disables it.
                if pod.showsKill(runStatus: runs.live?.status), let runID = status.runId {
                    KillButton(pod: pod, runID: runID, hasLease: status.lease != nil)
                        .buttonRow()
                } else {
                    KillNotice(pod: pod)
                }
            }
        } else if pod.error == nil {
            LoadingBlock().listRowBackground(Color.clear)
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
        .heroSurface()
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

/// Balance rows: the runway is the hero number, the Vast credit is on tap.
struct BalanceSection: View {
    let store: BalanceStore

    var body: some View {
        Section("Balance") {
            if let line = store.runpodLine {
                let low = store.balance?.runpod?.lowRunway == true
                let refreshFailed = store.error != nil
                VStack(alignment: .leading, spacing: 4) {
                    Text("RunPod").font(.subheadline).foregroundStyle(Theme.secondary)
                    Text(line).font(.title3.weight(.semibold).monospacedDigit())
                        .foregroundStyle(low ? Theme.warning : Theme.label)
                    if low {
                        Label("Under 1 h of runway — top up before renting.", systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote).foregroundStyle(Theme.warning)
                    }
                    if refreshFailed, let error = store.error {
                        Text("Couldn't refresh — \(error.userMessage)")
                            .font(.footnote).foregroundStyle(Theme.warning)
                    }
                }
                .opacity(refreshFailed ? 0.6 : 1)
                .padding(.vertical, 2)
            } else if let error = store.error {
                ErrorBanner(error: error) { await store.load() }
            } else {
                LoadingBlock()
            }
            if let vast = store.vastLine {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Vast").font(.subheadline).foregroundStyle(Theme.secondary)
                    Text(vast).font(.title3.weight(.semibold).monospacedDigit())
                }
                .padding(.vertical, 2)
            }
            ForEach(store.balance?.errors ?? [], id: \.self) { text in
                Text(text).font(.footnote).foregroundStyle(Theme.warning)
            }
            Button { Task { await store.loadVast() } } label: {
                HStack(spacing: 8) {
                    Text(store.isLoadingVast ? "Reading the Vast credit (~30 s)…" : "Check Vast credit")
                    if store.isLoadingVast { Spacer(); ProgressView() }
                }
            }
            .disabled(store.isLoadingVast)
            .accessibilityIdentifier("pod.checkVast")
        }
    }
}
