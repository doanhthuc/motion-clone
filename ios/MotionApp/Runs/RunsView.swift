import SwiftUI
import MotionKit

/// Every run as a cover card: what its jobs look like, how they stand, and
/// what they were made with. Until 2026-09-27 this was a list of "0 of 2 jobs ·
/// Stopped" rows — a failed job and one never started read the same, and
/// nothing showed what the run was of.
struct RunsView: View {
    let runs: RunsStore
    let pod: PodStore
    let flow: RunFlow
    @State private var details: RunDetail?
    @State private var continuing = false
    @State private var deleting: RunDeleteTarget?

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 14) {
                if let error = runs.error, !runs.loaded {
                    ErrorBanner(error: error) { await refresh() }.heroSurface()
                }
                if runs.isStale { StaleTag(lastSuccess: runs.lastSuccess) }
                if let p = pod.pod, let lease = p.lease {
                    PodStrip(gpu: p.gpu, lease: lease).heroSurface()
                }
                if let live = runs.live {
                    SectionTitle(text: "Now")
                    if live.status == .phaseA {
                        NavigationLink { RunFlowView(flow: flow, entry: .existing) } label: { card(live) }
                            .buttonStyle(CardPressStyle())
                    } else {
                        link(live)
                    }
                }
                if !runs.recent.isEmpty {
                    SectionTitle(text: "Recent")
                    ForEach(runs.recent) { link($0) }
                    Text("\(runs.runs.count) total").font(.footnote).foregroundStyle(Theme.secondary)
                        .padding(.leading, 4)
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .background(Theme.bg)
        .overlay {
            if runs.loaded && runs.runs.isEmpty { EmptyRuns() }
        }
        .navigationTitle("Runs")
        .navigationBarTitleDisplayMode(.inline)
        .navigationDestination(for: String.self) { id in
            RunDetailView(store: runs.detailStore(for: id), flow: flow, pod: pod)
        }
        .navigationDestination(isPresented: $continuing) { RunFlowView(flow: flow, entry: .existing) }
        .sheet(item: $details) { BatchDetailsSheet(detail: $0, focus: nil) }
        .runDeletion($deleting)
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

    private func card(_ run: RunSummary) -> some View {
        RunCard(run: run, store: runs.detailStore(for: run.id),
                progress: LiveProgress(run: run, tryon: run.status == .phaseA ? flow.tryon : nil))
    }

    /// Tap opens the run; press-and-hold offers what a run is usually opened for.
    private func link(_ run: RunSummary) -> some View {
        let store = runs.detailStore(for: run.id)
        return NavigationLink(value: run.id) { card(run) }
            .buttonStyle(CardPressStyle())
            .contextMenu {
                if let d = store.detail {
                    Button { details = d } label: { Label("Batch details", systemImage: "info.circle") }
                    if canContinue(d) {
                        Button { continuing = true } label: {
                            Label("Continue batch · \(d.jobsTotal - d.jobsDone) left", systemImage: "play.circle")
                        }
                    }
                }
                Button { UIPasteboard.general.string = run.id } label: {
                    Label("Copy run ID", systemImage: "doc.on.doc")
                }
                if !run.status.isLive, store.detail?.lease == nil {
                    Divider()
                    Button(role: .destructive) {
                        deleting = RunDeleteTarget(id: run.id, title: RunName.title(run.batch ?? run.id),
                                                   videos: store.detail?.outputs.count ?? 0)
                    } label: { Label("Delete run", systemImage: "trash") }
                }
            }
    }

    /// The same rule as the run detail's Continue button.
    private func canContinue(_ d: RunDetail) -> Bool {
        d.id == flow.runID && !d.status.isLive && d.lease == nil && pod.pod?.lease == nil
            && d.jobsDone < d.jobsTotal
    }

    private func refresh() async {
        async let a: Void = runs.refresh()
        async let b: Void = pod.refresh()
        async let c: Void = flow.refreshPod()
        _ = await (a, b, c)
        // A Phase A has no journal jobs yet; its looks are the only count.
        if runs.live?.status == .phaseA { await flow.refreshTryon() }
    }
}

private struct SectionTitle: View {
    let text: String
    var body: some View {
        Text(text).font(.title3.weight(.semibold)).padding(.leading, 4).padding(.top, 6)
    }
}

/// A card sinks a little under the finger, so a press reads as a press.
struct CardPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .animation(.snappy(duration: 0.2), value: configuration.isPressed)
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

struct EmptyRuns: View {
    var body: some View {
        EmptyNote(title: "No runs yet", systemImage: "waveform.path.ecg",
                  message: "Runs started from Telegram or this app show up here. Try-on happens on the VPS first — no GPU spend until you confirm.")
    }
}
