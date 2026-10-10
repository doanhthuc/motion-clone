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
    @State private var selection = Selection()

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
                    if selection.active {
                        // A running run is never deletable; it stays in view, inert.
                        card(live).opacity(0.4).allowsHitTesting(false)
                    } else if live.status == .phaseA {
                        NavigationLink { RunFlowView(flow: flow, entry: .existing) } label: { card(live) }
                            .buttonStyle(CardPressStyle())
                    } else {
                        link(live)
                    }
                }
                if !runs.recent.isEmpty {
                    SectionTitle(text: "Recent")
                    ForEach(runs.recent) { run in
                        if selection.active { selectableCard(run) } else { link(run) }
                    }
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
        .sheet(item: $details) { BatchDetailsSheet(detail: $0, store: runs.detailStore(for: $0.id), focus: nil) }
        .runDeletion($deleting) { failed in withAnimation(.snappy) { selection.keep(failed) } }
        .selectionMode($selection, selectable: deletable.map(\.id), busy: false) {
            let chosen = deletable.filter { selection.contains($0.id) }
            deleting = RunDeleteTarget(
                ids: chosen.map(\.id),
                title: chosen.count == 1 ? RunName.title(chosen[0].batch ?? chosen[0].id) : "\(chosen.count) runs",
                videos: chosen.reduce(0) { $0 + (runs.detailStore(for: $1.id).detail?.outputs.count ?? 0) })
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

    /// What the long-press Delete offers, as a list: not running, no pod attached.
    private var deletable: [RunSummary] {
        runs.recent.filter { !$0.status.isLive && runs.detailStore(for: $0.id).detail?.lease == nil }
    }

    /// In selection mode a tap picks the run; one that can't be deleted is dimmed and inert.
    @ViewBuilder private func selectableCard(_ run: RunSummary) -> some View {
        let canDelete = deletable.contains { $0.id == run.id }
        let picked = selection.contains(run.id)
        Button { selection.toggle(run.id) } label: {
            card(run)
                .overlay {
                    if picked { RoundedRectangle(cornerRadius: 22).strokeBorder(Theme.accent, lineWidth: 3) }
                }
                // Leading: each cover already carries its job's status check on the right.
                .overlay(alignment: .topLeading) {
                    if canDelete { SelectionCheck(selected: picked).padding(12) }
                }
        }
        .buttonStyle(CardPressStyle())
        .disabled(!canDelete)
        .opacity(canDelete ? 1 : 0.4)
        .accessibilityAddTraits(picked ? .isSelected : [])
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
