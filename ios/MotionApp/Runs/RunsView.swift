import SwiftUI
import MotionKit

struct RunsView: View {
    let runs: RunsStore
    let pod: PodStore
    let flow: RunFlow

    var body: some View {
        List {
            if let error = runs.error, !runs.loaded {
                Section { ErrorBanner(error: error) { await refresh() } }
            }
            if runs.isStale {
                Section { StaleTag(lastSuccess: runs.lastSuccess) }
            }
            if let p = pod.pod, let lease = p.lease {
                Section { PodStrip(gpu: p.gpu, lease: lease) }
            }
            if let live = runs.live {
                Section("Now") {
                    if live.status == .phaseA {
                        NavigationLink { RunFlowView(flow: flow, entry: .existing) } label: {
                            LiveRunRow(run: live, progress: LiveProgress(run: live, tryon: flow.tryon))
                        }
                    } else {
                        NavigationLink(value: live.id) {
                            LiveRunRow(run: live, progress: LiveProgress(run: live, tryon: nil))
                        }
                    }
                }
            }
            if !runs.recent.isEmpty {
                Section {
                    ForEach(runs.recent) { run in
                        NavigationLink(value: run.id) { RunRow(run: run) }
                    }
                } header: {
                    Text("Recent")
                } footer: {
                    Text("\(runs.runs.count) total")
                }
            }
        }
        .overlay {
            if runs.loaded && runs.runs.isEmpty { EmptyRuns() }
        }
        .navigationTitle("Runs")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: String.self) { id in
            if let client = runsClient {
                RunDetailView(store: RunDetailStore(client: client, runID: id), flow: flow, pod: pod)
            }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink { SettingsView() } label: { Image(systemName: "gearshape") }
                    .accessibilityLabel("Settings")
            }
        }
        .refreshable { await refresh() }
        .task { await refresh() }
        // While something runs, keep the Now card moving. Stops when the run
        // does, or when the tab goes out of view.
        .task(id: runs.live?.id) {
            while runs.live != nil, !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                guard !Task.isCancelled else { return }
                await refresh()
            }
        }
    }

    @Environment(AppModel.self) private var model
    private var runsClient: APIClient? { model.client }

    private func refresh() async {
        async let a: Void = runs.refresh()
        async let b: Void = pod.refresh()
        _ = await (a, b)
        // A Phase A has no journal jobs yet; its looks are the only count.
        if runs.live?.status == .phaseA {
            if flow.runID != runs.live?.id { await flow.refreshPod() }
            await flow.refreshTryon()
        }
    }
}

struct PodStrip: View {
    let gpu: String
    let lease: PodLease
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { ctx in
            let elapsed = ctx.date.timeIntervalSince1970 - lease.provisionedAt
            HStack(spacing: 10) {
                PulseDot()
                VStack(alignment: .leading, spacing: 2) {
                    Text("Pod live").font(.body)
                    Text("\(gpu) · \(lease.provider)").font(.subheadline).foregroundStyle(Theme.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(Format.clock(elapsed)).font(.body.monospacedDigit())
                    if let rate = lease.quotedUsdPerHr {
                        Text("\(Format.usd(rate))/h").font(.subheadline.monospacedDigit())
                            .foregroundStyle(Theme.secondary)
                    }
                }
            }
        }
    }
}

struct LiveRunRow: View {
    let run: RunSummary
    let progress: LiveProgress

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                PulseDot(color: Theme.accent, size: 6)
                Text(run.status == .phaseA ? "Try-on on the VPS" : "Generating")
                    .font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
            }
            Text(run.batch ?? run.id).font(.headline)
            if progress.total > 0 {
                ProgressView(value: Double(progress.done), total: Double(progress.total))
                    .tint(Theme.accent)
                Text(countText)
                    .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
            } else {
                Text(run.status == .phaseA ? "Starting try-on · no GPU rented yet" : "Starting…")
                    .font(.subheadline).foregroundStyle(Theme.secondary)
            }
        }
        .padding(.vertical, 4)
    }

    private var countText: String {
        switch progress.unit {
        case .looks: "\(progress.done) of \(progress.total) looks ready · no GPU rented yet"
        case .jobs: "\(progress.done) of \(progress.total) jobs done"
        }
    }
}

struct RunRow: View {
    let run: RunSummary
    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(run.batch ?? run.id).font(.body)
                Text("\(run.jobsDone) of \(run.jobsTotal) jobs")
                    .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
            }
            Spacer(minLength: 0)
            StatusBadge(status: run.status)
        }
    }
}

/// Icon and word together, so status never rides on color alone.
struct StatusBadge: View {
    let status: RunStatus
    var body: some View {
        switch status {
        case .done:
            Label("Done", systemImage: "checkmark.circle").foregroundStyle(Theme.secondary)
                .labelStyle(.iconOnly).accessibilityLabel("Done")
        case .error:
            Label("Failed", systemImage: "exclamationmark.triangle.fill")
                .font(.subheadline).foregroundStyle(Theme.danger)
        case .running, .phaseA:
            PulseDot().accessibilityLabel("Running")
        case .stopped, .unknown:
            Text(status == .stopped ? "Stopped" : "Unknown").font(.subheadline).foregroundStyle(Theme.secondary)
        }
    }
}

struct EmptyRuns: View {
    var body: some View {
        EmptyNote(title: "No runs yet", systemImage: "waveform.path.ecg",
                  message: "Runs started from Telegram or this app show up here. Try-on happens on the VPS first — no GPU spend until you confirm.")
    }
}
