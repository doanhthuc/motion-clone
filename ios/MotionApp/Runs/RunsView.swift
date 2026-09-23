import SwiftUI
import MotionKit

struct RunsView: View {
    let runs: RunsStore
    let pod: PodStore
    let flow: RunFlow

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 13) {
                header
                if let error = runs.error, !runs.loaded {
                    ErrorBanner(error: error) { await refresh() }
                }
                if let p = pod.pod, let lease = p.lease { PodStrip(gpu: p.gpu, lease: lease) }
                if runs.loaded && runs.runs.isEmpty { EmptyRuns() }
                if let live = runs.live {
                    if live.status == .phaseA {
                        NavigationLink { RunFlowView(flow: flow, entry: .existing) } label: { LiveRunCard(run: live) }
                            .buttonStyle(.plain)
                    } else {
                        NavigationLink(value: live.id) { LiveRunCard(run: live) }.buttonStyle(.plain)
                    }
                }
                if !runs.recent.isEmpty {
                    SectionLabel(text: "Recent").padding(.top, 2)
                    ForEach(runs.recent) { run in
                        NavigationLink(value: run.id) { RunRow(run: run) }.buttonStyle(.plain)
                    }
                }
            }
            .padding(.horizontal, 20)
        }
        .background(Theme.bg)
        .navigationDestination(for: String.self) { id in
            if let client = runsClient { RunDetailView(store: RunDetailStore(client: client, runID: id), flow: flow) }
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                NavigationLink { SettingsView() } label: { Image(systemName: "gearshape") }
            }
        }
        .refreshable { await refresh() }
        .task { await refresh() }
    }

    @Environment(AppModel.self) private var model
    private var runsClient: APIClient? { model.client }

    private func refresh() async {
        async let a: Void = runs.refresh()
        async let b: Void = pod.refresh()
        _ = await (a, b)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Runs").font(Theme.sans(33, .bold)).foregroundStyle(Theme.ink)
            HStack(spacing: 12) {
                if runs.live != nil {
                    HStack(spacing: 6) { PulseDot(size: 7); Text("1 generating") }
                        .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                }
                Text("\(runs.runs.count) total").font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                if runs.isStale { StaleTag(lastSuccess: runs.lastSuccess) }
            }
        }
        .padding(.top, 6)
    }
}

struct PodStrip: View {
    let gpu: String
    let lease: PodLease
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { ctx in
            let elapsed = ctx.date.timeIntervalSince1970 - lease.provisionedAt
            HStack(spacing: 11) {
                PulseDot()
                Text("Pod live").font(Theme.sans(13, .semibold)).foregroundStyle(Theme.ink1)
                Text("\(gpu) · \(lease.provider)").font(Theme.mono(11)).foregroundStyle(Theme.ink2).lineLimit(1)
                Spacer(minLength: 0)
                if let rate = lease.quotedUsdPerHr {
                    Text("\(Format.usd(rate))/h").font(Theme.mono(13, .semibold)).foregroundStyle(Theme.lime)
                }
                Text(Format.clock(elapsed)).font(Theme.mono(12)).foregroundStyle(Theme.ink2)
            }
            .padding(.horizontal, 14).padding(.vertical, 12)
            .card(radius: 14)
        }
    }
}

struct LiveRunCard: View {
    let run: RunSummary
    var body: some View {
        VStack(alignment: .leading, spacing: 13) {
            HStack(spacing: 6) {
                PulseDot(size: 6)
                Text(run.status == .phaseA ? "Try-on on the VPS" : "Generating")
            }
            .font(Theme.sans(11, .bold)).foregroundStyle(Theme.lime)
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(Theme.limeDim, in: .capsule)
            Text(run.batch ?? run.id).font(Theme.sans(18, .semibold)).foregroundStyle(Theme.ink)
            ProgressView(value: Double(run.jobsDone), total: Double(max(run.jobsTotal, 1)))
                .tint(Theme.lime)
            Text("\(run.jobsDone)/\(run.jobsTotal) jobs done · \(run.id)")
                .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
        }
        .padding(14)
        .card(radius: 20, border: Theme.limeLine)
    }
}

struct RunRow: View {
    let run: RunSummary
    var body: some View {
        HStack(spacing: 13) {
            VStack(alignment: .leading, spacing: 4) {
                Text(run.batch ?? run.id).font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink)
                Text("\(run.id) · \(run.jobsDone)/\(run.jobsTotal) jobs")
                    .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
            }
            Spacer(minLength: 0)
            StatusBadge(status: run.status)
        }
        .padding(11)
        .card(border: run.status == .error ? Theme.redDim : Theme.line)
    }
}

struct StatusBadge: View {
    let status: RunStatus
    var body: some View {
        switch status {
        case .done:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.ink)
        case .error:
            Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.red)
        case .running, .phaseA:
            PulseDot()
        case .stopped, .unknown:
            Text(status == .stopped ? "stopped" : "unknown").font(Theme.mono(10)).foregroundStyle(Theme.ink3)
        }
    }
}

struct EmptyRuns: View {
    var body: some View {
        VStack(spacing: 10) {
            Text("Nothing yet").font(Theme.sans(22, .bold)).foregroundStyle(Theme.ink)
            Text("Runs started from Telegram or this app show up here. Try-on happens on the VPS first — no GPU spend until you confirm.")
                .font(Theme.sans(14)).foregroundStyle(Theme.ink2).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 60)
    }
}
