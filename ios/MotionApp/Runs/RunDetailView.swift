import SwiftUI
import MotionKit

struct RunDetailView: View {
    @State var store: RunDetailStore
    let flow: RunFlow
    let pod: PodStore
    @Environment(\.scenePhase) private var scenePhase
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            if let error = store.error, store.detail == nil {
                Section { ErrorBanner(error: error) { await store.refresh() } }
            }
            if let d = store.detail {
                Section { StatusHero(detail: d, stale: store.isStale, lastSuccess: store.lastSuccess) }
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
                if d.id == flow.runID,
                   flow.canRetryRental,
                   let failure = flow.pod?.failedRental {
                    Section {
                        Text(failure.detail).font(.subheadline)
                        // The label names the card `retryRental()` actually sends
                        // (`pod.gpu`, the current .env card), not the one that failed.
                        let gpu = flow.pod?.gpu ?? failure.gpu
                        if failure.gpu != gpu {
                            Text("Last failure was on \(failure.gpu); this retries on \(gpu).")
                                .font(.footnote).foregroundStyle(Theme.secondary)
                        }
                        if let message = flow.message {
                            Text(message).font(.footnote).foregroundStyle(Theme.warning)
                        }
                        Button("Retry rental · \(gpu)") { Task { await flow.retryRental() } }
                            .buttonStyle(PrimaryButtonStyle())
                            .disabled(!flow.canSpend)
                            .buttonRow()
                        if flow.needsRecheck {
                            Button("Check again") { Task { await flow.recheck() } }
                                .buttonStyle(SecondaryButtonStyle())
                                .disabled(flow.isSpending)
                                .buttonRow()
                        }
                    } header: {
                        Label("Rental failed", systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(Theme.danger)
                    }
                }
                if d.id == pod.pod?.runId, pod.showsKill(runStatus: d.status), let runID = pod.pod?.runId {
                    Section {
                        KillButton(pod: pod, runID: runID, hasLease: pod.pod?.lease != nil)
                            .buttonRow()
                    } footer: {
                        if d.jobs.count > 1 { Text("Kill stops every remaining job in this batch.") }
                    }
                }
                // A 12-job batch inlined stage-by-stage is a wall of rows; the
                // collapsed list puts the job that failed at the top instead.
                if d.jobs.count > 1 {
                    BatchProgressList(jobs: d.jobs)
                } else {
                    Section("Stages") {
                        ForEach(d.jobs) { job in JobTimeline(job: job) }
                    }
                }
                if !d.outputs.isEmpty, let batch = d.batch, let client = model.client {
                    Section("Outputs") {
                        // The Outputs tab's copy has sizes and durations; the run
                        // detail lists names only, which is enough to open the feed.
                        RunOutputsStrip(client: client, batch: model.outputs?.batches.first { $0.batch == batch }
                            ?? OutputBatch(batch: batch, updatedAt: d.updatedAt,
                                           files: d.outputs.map { OutputFile(name: $0, bytes: 0) }))
                            .listRowInsets(EdgeInsets(top: 12, leading: 16, bottom: 12, trailing: 0))
                    }
                }
            } else if store.error == nil {
                LoadingBlock().listRowBackground(Color.clear)
            }
        }
        .navigationTitle(store.detail?.batch ?? store.runID)
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
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                if detail.status.isLive { PulseDot() }
                if detail.status == .error {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.danger)
                }
                Text(title).font(.title2.bold())
                Spacer()
                if stale { StaleTag(lastSuccess: lastSuccess) }
            }
            if let lease = detail.lease {
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    let elapsed = ctx.date.timeIntervalSince1970 - lease.provisionedAt
                    VStack(alignment: .leading, spacing: 4) {
                        Text(Format.clock(elapsed)).font(.largeTitle.weight(.semibold).monospacedDigit())
                        if let cost = CostEstimate.usd(elapsed: elapsed, ratePerHour: lease.quotedUsdPerHr) {
                            Text("≈ \(Format.usd(cost)) on \(lease.provider) — a quote, not the invoice")
                                .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                        } else {
                            Text("\(lease.provider) — no price quote on this lease")
                                .font(.subheadline).foregroundStyle(Theme.secondary)
                        }
                    }
                }
            }
            ProgressView(value: Double(detail.jobsDone), total: Double(max(detail.jobsTotal, 1)))
                .tint(detail.status == .error ? Theme.danger : Theme.label)
            Text("\(detail.jobsDone) of \(detail.jobsTotal) jobs done · \(detail.id)")
                .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
        }
        .heroSurface()
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

/// The stages of one job. Callers print the job id when it is worth showing —
/// `BatchProgressList`'s row header does; the single-job path does not.
struct JobTimeline: View {
    let job: JobProgress

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(job.stages.enumerated()), id: \.offset) { index, stage in
                HStack(alignment: .top, spacing: 14) {
                    VStack(spacing: 0) {
                        StageDot(status: stage.status)
                        if index < job.stages.count - 1 {
                            Rectangle().fill(Theme.tertiary).frame(width: 2).frame(minHeight: 20)
                        }
                    }
                    VStack(alignment: .leading, spacing: 3) {
                        Text(Format.stageName(stage.name))
                            .font(.body)
                            .foregroundStyle(stage.status == .error ? Theme.danger
                                             : stage.status == .pending || stage.status == .unknown
                                             ? Theme.secondary : Theme.label)
                        Text(detailLine(stage)).font(.footnote.monospacedDigit()).foregroundStyle(Theme.secondary)
                    }
                    .padding(.bottom, index < job.stages.count - 1 ? 14 : 0)
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
                Circle().fill(Theme.label)
                Image(systemName: "checkmark").font(.system(size: 12, weight: .bold)).foregroundStyle(.black)
            case .running:
                Circle().strokeBorder(Theme.label, lineWidth: 2)
                ProgressView().controlSize(.mini)
            case .error:
                Circle().fill(Theme.danger.opacity(0.18))
                Image(systemName: "xmark").font(.system(size: 11, weight: .bold)).foregroundStyle(Theme.danger)
            case .pending, .unknown:
                Circle().strokeBorder(Theme.tertiary, lineWidth: 1.5)
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
        Section {
            ForEach(summary.ordered) { job in
                DisclosureGroup(isExpanded: Binding(
                    get: { expanded.contains(job.id) },
                    set: { if $0 { expanded.insert(job.id) } else { expanded.remove(job.id) } })
                ) {
                    JobTimeline(job: job).padding(.vertical, 4)
                } label: {
                    HStack(spacing: 10) {
                        StageDot(status: job.status)
                        Text(job.id).font(.subheadline).lineLimit(1).truncationMode(.middle)
                        Spacer(minLength: 0)
                        if job.finishedSec > 0 {
                            Text(Format.clock(job.finishedSec)).font(.subheadline.monospacedDigit())
                                .foregroundStyle(Theme.secondary)
                        }
                    }
                }
            }
        } header: {
            Text("Batch · \(summary.done) of \(summary.total) done")
        } footer: {
            Text("\(summary.running) running · \(summary.failed) failed")
                .foregroundStyle(summary.failed > 0 ? Theme.danger : Theme.secondary)
                .accessibilityIdentifier("batch.summary")
        }
    }
}

/// The run's finished files as posters in a sideways strip; each opens the
/// same full-screen feed the Outputs tab does, starting at that file.
private struct RunOutputsStrip: View {
    let client: APIClient
    let batch: OutputBatch

    var body: some View {
        ScrollView(.horizontal) {
            LazyHStack(spacing: 8) {
                ForEach(batch.files) { file in
                    NavigationLink {
                        OutputFeedView(client: client, batch: batch, startAt: file)
                    } label: {
                        OutputPosterTile(client: client, batch: batch.batch, file: file)
                            .frame(width: 120)
                            .clipShape(.rect(cornerRadius: Theme.Radius.small))
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(file.name)
                }
            }
            .padding(.trailing, 16)
        }
        .scrollIndicators(.hidden)
    }
}
