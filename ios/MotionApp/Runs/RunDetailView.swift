import SwiftUI
import MotionKit

struct RunDetailView: View {
    @State var store: RunDetailStore
    let flow: RunFlow
    let pod: PodStore
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let error = store.error, store.detail == nil { ErrorBanner(error: error) { await store.refresh() } }
                if let d = store.detail {
                    StatusHero(detail: d, stale: store.isStale, lastSuccess: store.lastSuccess)
                    if d.id == flow.runID, flow.canRetryRental, let failure = flow.pod?.failedRental {
                        VStack(alignment: .leading, spacing: 8) {
                            SectionLabel(text: "Rental failed")
                            Text(failure.detail).font(Theme.sans(13)).foregroundStyle(Theme.ink1)
                            // The label names the card `retryRental()` actually sends
                            // (`pod.gpu`, the current .env card), not the one that failed.
                            let gpu = flow.pod?.gpu ?? failure.gpu
                            if failure.gpu != gpu {
                                Text("Last failure was on \(failure.gpu); this retries on \(gpu).")
                                    .font(Theme.sans(12)).foregroundStyle(Theme.amber)
                            }
                            Button("Retry rental · \(gpu)") { Task { await flow.retryRental() } }
                                .buttonStyle(PrimaryButtonStyle())
                                .disabled(!flow.canSpend)
                            if flow.needsRecheck {
                                Button("Check again") { Task { await flow.recheck() } }
                                    .buttonStyle(SecondaryButtonStyle())
                                    .disabled(flow.isSpending)
                            }
                            if let message = flow.message {
                                Text(message).font(Theme.sans(12)).foregroundStyle(Theme.amber)
                            }
                        }
                        .padding(14).card(border: Theme.redLine)
                    }
                    if d.id == pod.pod?.runId, pod.showsKill(runStatus: d.status), let runID = pod.pod?.runId {
                        KillButton(pod: pod, runID: runID, hasLease: pod.pod?.lease != nil)
                        if d.jobs.count > 1 {
                            Text("Kill stops every remaining job in this batch.")
                                .font(Theme.sans(12)).foregroundStyle(Theme.ink3)
                        }
                    }
                    // A 12-job batch inlined stage-by-stage is a wall of rows; the
                    // collapsed list puts the job that failed at the top instead.
                    if d.jobs.count > 1 {
                        BatchProgressList(jobs: d.jobs)
                    } else {
                        ForEach(d.jobs) { job in JobTimeline(job: job, showTitle: false) }
                    }
                    if !d.outputs.isEmpty {
                        SectionLabel(text: "Outputs")
                        ForEach(d.outputs, id: \.self) { name in
                            Text(name).font(Theme.mono(12)).foregroundStyle(Theme.ink1)
                        }
                        Text("Open the Output tab to play or save them.")
                            .font(Theme.sans(12)).foregroundStyle(Theme.ink3)
                    }
                } else if store.error == nil {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 60)
                }
            }
            .padding(.horizontal, 20).padding(.top, 4)
        }
        .background(Theme.bg)
        .navigationTitle(store.runID)
        .navigationBarTitleDisplayMode(.inline)
        // Polls only while this screen is visible AND the app is active;
        // `.task(id:)` restarts/cancels the loop when scenePhase changes.
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await store.poll()
        }
        .task {
            async let a: Void = flow.refreshPod()
            async let b: Void = pod.refresh()
            _ = await (a, b)
        }
    }
}

struct StatusHero: View {
    let detail: RunDetail
    let stale: Bool
    let lastSuccess: Date?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                HStack(spacing: 8) {
                    if detail.status.isLive { PulseDot() }
                    Text(title).font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink)
                }
                Spacer()
                if stale { StaleTag(lastSuccess: lastSuccess) }
            }
            if let lease = detail.lease {
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    let elapsed = ctx.date.timeIntervalSince1970 - lease.provisionedAt
                    VStack(alignment: .leading, spacing: 6) {
                        Text(Format.clock(elapsed)).font(Theme.mono(36, .semibold)).foregroundStyle(Theme.ink)
                        if let cost = CostEstimate.usd(elapsed: elapsed, ratePerHour: lease.quotedUsdPerHr) {
                            Text("≈ \(Format.usd(cost)) on \(lease.provider) — a quote, not the invoice")
                                .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                        } else {
                            Text("\(lease.provider) — no price quote on this lease")
                                .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                        }
                    }
                }
            }
            ProgressView(value: Double(detail.jobsDone), total: Double(max(detail.jobsTotal, 1)))
                .tint(Theme.lime)
            Text("\(detail.jobsDone)/\(detail.jobsTotal) jobs done" + (detail.batch.map { " · \($0)" } ?? ""))
                .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
        }
        .padding(18)
        .card(radius: 20, border: detail.status.isLive ? Theme.limeLine : Theme.line)
    }

    private var title: String {
        switch detail.status {
        case .running: "Running"
        case .phaseA: "Try-on on the VPS"
        case .done: "Done"
        case .error: "Failed"
        case .stopped: "Stopped"
        case .unknown: "Unknown state"
        }
    }
}

struct JobTimeline: View {
    let job: JobProgress
    let showTitle: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if showTitle {
                Text(job.id).font(Theme.mono(12, .semibold)).foregroundStyle(Theme.ink1).padding(.bottom, 10)
            }
            ForEach(Array(job.stages.enumerated()), id: \.offset) { index, stage in
                HStack(alignment: .top, spacing: 14) {
                    VStack(spacing: 0) {
                        StageDot(status: stage.status)
                        if index < job.stages.count - 1 {
                            Rectangle().fill(Theme.line2).frame(width: 2).frame(minHeight: 20)
                        }
                    }
                    VStack(alignment: .leading, spacing: 3) {
                        Text(Format.stageName(stage.name))
                            .font(Theme.sans(15, .semibold))
                            .foregroundStyle(stage.status == .running ? Theme.lime
                                             : stage.status == .error ? Theme.red
                                             : stage.status == .done ? Theme.ink : Theme.ink2)
                        Text(detailLine(stage)).font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                    }
                    .padding(.bottom, 18)
                }
            }
        }
    }

    private func detailLine(_ stage: StageProgress) -> String {
        switch stage.status {
        case .done: stage.elapsedSec.map { "done · \(Format.clock($0))" } ?? "done"
        case .running: "running"
        case .error: "failed"
        case .pending, .unknown: "waiting"
        }
    }
}

struct StageDot: View {
    let status: StageStatus
    var body: some View {
        ZStack {
            switch status {
            case .done:
                Circle().fill(Theme.ink)
                Image(systemName: "checkmark").font(.system(size: 12, weight: .bold)).foregroundStyle(.black)
            case .running:
                Circle().strokeBorder(Theme.lime, lineWidth: 2)
                ProgressView().controlSize(.mini).tint(Theme.lime)
            case .error:
                Circle().fill(Theme.redDim)
                Image(systemName: "xmark").font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.red)
            case .pending, .unknown:
                Circle().strokeBorder(Theme.line2, lineWidth: 1.5)
            }
        }
        .frame(width: 26, height: 26)
    }
}

struct BatchProgressList: View {
    let jobs: [JobProgress]
    @State private var expanded: Set<String> = []

    var body: some View {
        let summary = BatchSummary(jobs)
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(text: "Batch · \(summary.done)/\(summary.total) done")
            Text("\(summary.running) running · \(summary.failed) failed")
                .font(Theme.mono(11)).foregroundStyle(summary.failed > 0 ? Theme.red : Theme.ink2)
                .accessibilityIdentifier("batch.summary")
            ForEach(summary.ordered) { job in
                VStack(alignment: .leading, spacing: 10) {
                    Button {
                        if expanded.contains(job.id) { expanded.remove(job.id) } else { expanded.insert(job.id) }
                    } label: {
                        HStack(spacing: 10) {
                            StageDot(status: job.status)
                            Text(job.id).font(Theme.mono(12, .semibold)).foregroundStyle(Theme.ink1).lineLimit(1)
                            Spacer(minLength: 0)
                            if job.finishedSec > 0 {
                                Text(Format.clock(job.finishedSec)).font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                            }
                            Image(systemName: expanded.contains(job.id) ? "chevron.up" : "chevron.down")
                                .foregroundStyle(Theme.ink3)
                        }
                    }
                    .buttonStyle(.plain)
                    if expanded.contains(job.id) { JobTimeline(job: job, showTitle: false) }
                }
                .padding(12).card()
            }
        }
    }
}
