import SwiftUI
import MotionKit

/// One run: a status strip, then its jobs as swipeable pages, then whatever
/// the run needs from you (kill, retry the rental). Until 2026-09-26 this was
/// a grouped list — a hero, a disclosure row per job, stage rows under each —
/// in which a job had a file name and empty circles but nothing to look at.
/// Each page now shows the job itself: its finished video's poster, or its
/// try-on image being worked on.
struct RunDetailView: View {
    @State var store: RunDetailStore
    let flow: RunFlow
    let pod: PodStore
    @Environment(\.scenePhase) private var scenePhase
    @State private var selection: String?
    @State private var details: DetailsTarget?

    /// The details sheet, opened for the whole batch or scrolled to one job.
    private struct DetailsTarget: Identifiable {
        let focus: String?
        var id: String { focus ?? "" }
    }

    var body: some View {
        Group {
            if let d = store.detail {
                content(d)
            } else if let error = store.error {
                ScrollView {
                    ErrorBanner(error: error) { await store.refresh() }
                        .heroSurface().padding(20)
                }
            } else {
                LoadingBlock().frame(maxHeight: .infinity)
            }
        }
        .background(Theme.bg)
        .navigationTitle(RunName.title(store.detail?.batch ?? store.runID))
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if store.detail != nil {
                ToolbarItem(placement: .topBarTrailing) {
                    Button { details = DetailsTarget(focus: nil) } label: { Image(systemName: "info.circle") }
                        .accessibilityLabel("Batch details")
                }
            }
        }
        .sheet(item: $details) { target in
            if let d = store.detail { BatchDetailsSheet(detail: d, focus: target.focus) }
        }
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

    private func content(_ d: RunDetail) -> some View {
        let current = d.jobs.first { $0.id == selection } ?? d.jobs.first
        return VStack(spacing: 14) {
            StatusHero(detail: d, stale: store.isStale, lastSuccess: store.lastSuccess)
                .padding(.horizontal, 20)
            if d.jobs.isEmpty {
                WorkCanvas(backdrop: nil, animating: d.status.isLive) {
                    WorkStatus(title: d.status.isLive ? "Starting…" : "No jobs", active: d.status.isLive) {
                        Text(d.status.isLive ? "The first job appears here when it starts" : "This run never queued a job")
                    }
                }
                .padding(.horizontal, 20)
            } else {
                pager(d)
                if d.jobs.count > 1 {
                    StatusDots(items: d.jobs.map { .init(id: $0.id, status: $0.status) },
                               noun: "Job", selection: current?.id) { id in
                        withAnimation(.snappy) { selection = id }
                    }
                }
                if let current { StageStepper(job: current).padding(.horizontal, 20) }
            }
            actions(d).padding(.horizontal, 20)
        }
        .padding(.top, 8)
        .padding(.bottom, 12)
        .onAppear {
            // Land on what needs looking at: a failure, else the job running now.
            if selection == nil {
                selection = (d.jobs.first { $0.status == .error } ?? d.jobs.first { $0.status == .running })?.id
            }
        }
    }

    /// Only the bot's current run can be rented again — `confirm`/`resume` act
    /// on that one manifest — and only while nothing is running or leased.
    private func canContinue(_ d: RunDetail) -> Bool {
        d.id == flow.runID && !d.status.isLive && d.lease == nil && pod.pod?.lease == nil
            && d.jobsDone < d.jobsTotal
    }

    private func pager(_ d: RunDetail) -> some View {
        ScrollView(.horizontal) {
            LazyHStack(spacing: 12) {
                ForEach(d.jobs) { job in
                    JobPage(store: store, detail: d, job: job) { details = DetailsTarget(focus: job.id) }
                        .containerRelativeFrame(.horizontal)
                        .id(job.id)
                }
            }
            .scrollTargetLayout()
        }
        .contentMargins(.horizontal, d.jobs.count > 1 ? 24 : 20, for: .scrollContent)
        .scrollTargetBehavior(.viewAligned)
        .scrollPosition(id: $selection)
        .scrollIndicators(.hidden)
        .scrollDisabled(d.jobs.count < 2)
    }

    /// What the run needs from you, if anything. Usually nothing.
    @ViewBuilder private func actions(_ d: RunDetail) -> some View {
        if d.id == flow.runID, flow.canRetryRental, let failure = flow.pod?.failedRental {
            RetryRentalCard(flow: flow, failure: failure)
        } else if canContinue(d) {
            // The same road as after a fresh Phase A: previews, then the rent
            // panel, where the quote is shown and nothing is rented until Confirm.
            NavigationLink {
                RunFlowView(flow: flow, entry: .existing)
            } label: {
                Label("Continue batch · \(d.jobsTotal - d.jobsDone) left", systemImage: "play.fill")
            }
            .buttonStyle(PrimaryButtonStyle())
            .accessibilityIdentifier("run.continue")
        }
        if d.id == pod.pod?.runId, pod.showsKill(runStatus: d.status), let runID = pod.pod?.runId {
            KillButton(pod: pod, runID: runID, hasLease: pod.pod?.lease != nil)
        }
    }
}

// MARK: - hero

/// Status, pod clock and progress in one strip, so the page below gets the height.
struct StatusHero: View {
    let detail: RunDetail
    let stale: Bool
    let lastSuccess: Date?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if detail.status.isLive { PulseDot(color: Theme.accent) }
                if detail.status == .error {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.danger)
                }
                Text(title).font(.title3.bold())
                Spacer(minLength: 8)
                if stale {
                    StaleTag(lastSuccess: lastSuccess)
                } else if let lease = detail.lease {
                    LeaseClock(lease: lease)
                }
            }
            ProgressView(value: Double(detail.jobsDone), total: Double(max(detail.jobsTotal, 1)))
                .tint(detail.status == .error ? Theme.danger : Theme.accent)
            HStack {
                Text("\(detail.jobsDone) of \(detail.jobsTotal) jobs done")
                Spacer()
                Text(detail.id).lineLimit(1)
            }
            .font(.footnote.monospacedDigit()).foregroundStyle(Theme.secondary)
        }
        .heroSurface()
    }

    private var title: String {
        switch detail.status {
        case .running: "Generating"
        case .phaseA: "Try-on on the VPS"
        case .done: "Done"
        case .error: "Failed"
        case .stopped: "Stopped"
        case .unknown: "Unknown state"
        }
    }
}

/// "12:04 · ≈ $0.20" — the pod's clock and a quote, never the invoice.
private struct LeaseClock: View {
    let lease: RunLease

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { ctx in
            let elapsed = ctx.date.timeIntervalSince1970 - lease.provisionedAt
            VStack(alignment: .trailing, spacing: 1) {
                Text(Format.clock(elapsed)).font(.headline.monospacedDigit())
                Group {
                    if let cost = CostEstimate.usd(elapsed: elapsed, ratePerHour: lease.quotedUsdPerHr) {
                        Text("≈ \(Format.usd(cost)) · \(lease.provider)")
                    } else {
                        Text("\(lease.provider) · no quote")
                    }
                }
                .font(.caption.monospacedDigit()).foregroundStyle(Theme.secondary)
            }
            .accessibilityElement(children: .combine)
            .accessibilityHint("Elapsed pod time and a price quote, not the invoice")
        }
    }
}

// MARK: - one job

private struct JobPage: View {
    let store: RunDetailStore
    let detail: RunDetail
    let job: JobProgress
    let showDetails: () -> Void
    @Environment(AppModel.self) private var model
    @State private var tryon: UIImage?

    private var files: [String] { detail.outputs(forJob: job.id) }
    private var runningStage: StageProgress? { job.stages.first { $0.status == .running } }
    private var failedStage: StageProgress? { job.stages.first { $0.status == .error } }

    var body: some View {
        Group {
            if let first = files.first, let batch = outputBatch {
                output(batch: batch, file: batch.files.first { $0.name == first }
                       ?? OutputFile(name: first, bytes: 0))
            } else {
                working
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .clipShape(.rect(cornerRadius: 22))
        .overlay(alignment: .bottom) { PageCaption(title: job.id, subtitle: subtitle) }
        .overlay(alignment: .topTrailing) {
            Button(action: showDetails) {
                Image(systemName: "info.circle.fill")
                    .font(.title3).symbolRenderingMode(.hierarchical).foregroundStyle(.white)
                    .padding(12)
            }
            .accessibilityLabel("Details for \(job.id)")
        }
        // A swipe up opens this job's details; simultaneous so the pager's
        // sideways drag is never blocked.
        .simultaneousGesture(
            DragGesture(minimumDistance: 24).onEnded { value in
                guard value.translation.height < -60,
                      abs(value.translation.width) < abs(value.translation.height) / 2 else { return }
                showDetails()
            }
        )
        .task(id: job.id) {
            let loaded = await store.tryonImage(forJob: job.id).flatMap(UIImage.init(data:))
            withAnimation(.easeOut(duration: 0.4)) { tryon = loaded }
        }
    }

    /// The Outputs tab's copy has sizes and durations; fall back to names only.
    private var outputBatch: OutputBatch? {
        guard let batch = detail.batch else { return nil }
        return model.outputs?.batches.first { $0.batch == batch }
            ?? OutputBatch(batch: batch, updatedAt: detail.updatedAt,
                           files: detail.outputs.map { OutputFile(name: $0, bytes: 0) })
    }

    @ViewBuilder private func output(batch: OutputBatch, file: OutputFile) -> some View {
        if let client = model.client {
            NavigationLink {
                OutputFeedView(client: client, batch: batch, startAt: file)
            } label: {
                FramedPoster(client: client, batch: batch.batch, file: file)
                    .overlay {
                        Image(systemName: "play.fill")
                            .font(.system(size: 26))
                            .foregroundStyle(.white)
                            .frame(width: 64, height: 64)
                            .background(.ultraThinMaterial, in: .circle)
                    }
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Play \(file.name)")
        }
    }

    /// Only a running job is blurred and scanned. A queued, stopped or failed
    /// one already has a finished try-on, and that picture is worth seeing.
    @ViewBuilder private var working: some View {
        if job.status != .running, let tryon {
            FramedImage(image: tryon)
                .overlay(alignment: .bottomLeading) {
                    jobStatus
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .background(.ultraThinMaterial, in: .rect(cornerRadius: Theme.Radius.medium))
                        .padding(.horizontal, 12).padding(.bottom, 78)
                }
        } else {
            WorkCanvas(backdrop: tryon, animating: job.status == .running) { jobStatus }
        }
    }

    @ViewBuilder private var jobStatus: some View {
            switch job.status {
            case .running:
                WorkStatus(title: "\(Format.stageName(runningStage?.name ?? "working"))…", active: true) {
                    Text(stagePosition)
                }
            case .error:
                WorkStatus(title: "Failed", active: false, symbol: "exclamationmark.triangle.fill",
                           tint: Theme.danger) {
                    Text(failedStage.map { "at \(Format.stageName($0.name))" } ?? "No output")
                }
            case .done:
                WorkStatus(title: "Done", active: false, symbol: "checkmark.circle.fill", tint: Theme.accent) {
                    Text("The video is not in Outputs yet")
                }
            case .pending, .unknown:
                WorkStatus(title: detail.status.isLive ? "Queued" : "Not run", active: false,
                           symbol: detail.status.isLive ? "hourglass" : "pause.circle") {
                    Text(detail.status.isLive ? "Waits for the job before it" : "The run stopped before this job")
                }
            }
    }

    private var stagePosition: String {
        guard let running = runningStage,
              let n = job.stages.firstIndex(where: { $0.name == running.name }) else { return "Working" }
        return "Stage \(n + 1) of \(job.stages.count)"
    }

    private var subtitle: String? {
        if files.count > 1 { return "\(files.count) versions · swipe the feed for the rest" }
        if job.finishedSec > 0 { return "\(Format.clock(job.finishedSec)) of work" }
        return nil
    }
}

/// A finished video's poster, fitted, over a blur of itself — the try-on
/// carousel's framing, so a portrait frame fills the page instead of
/// floating in a 9:16 tile narrower than it.
private struct FramedPoster: View {
    let client: APIClient
    let batch: String
    let file: OutputFile
    @State private var poster: UIImage?

    var body: some View {
        FramedImage(image: poster)
            .task(id: file.id) {
                poster = OutputPosters.shared.cached(batch: batch, file: file)
                if poster == nil { poster = await OutputPosters.shared.poster(client: client, batch: batch, file: file) }
            }
    }
}

/// A picture fitted to the page over a blur of itself, so a portrait frame
/// fills the page without being cropped.
struct FramedImage: View {
    let image: UIImage?

    var body: some View {
        Theme.surface
            .overlay {
                if let image {
                    Image(uiImage: image).resizable().scaledToFill().blur(radius: 40).opacity(0.55)
                }
            }
            .overlay {
                if let image { Image(uiImage: image).resizable().scaledToFit() }
            }
            .clipped()
    }
}

/// The current job's stages in one row: ✓ Try-on 2:09 — ● Motion — ○ Enhance.
private struct StageStepper: View {
    let job: JobProgress

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 6) {
                ForEach(Array(job.stages.enumerated()), id: \.offset) { index, stage in
                    if index > 0 {
                        Rectangle().fill(Theme.tertiary).frame(width: 10, height: 1.5)
                    }
                    HStack(spacing: 5) {
                        StageDot(status: stage.status).scaleEffect(0.75).frame(width: 20, height: 20)
                        Text(Format.stageName(stage.name))
                            .font(.footnote.weight(stage.status == .running ? .semibold : .regular))
                            .foregroundStyle(stage.status == .error ? Theme.danger
                                             : stage.status == .pending || stage.status == .unknown
                                             ? Theme.secondary : Theme.label)
                        if let s = stage.elapsedSec, stage.status == .done {
                            Text(Format.clock(s)).font(.caption.monospacedDigit()).foregroundStyle(Theme.secondary)
                        }
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityValue(stage.status.rawValue)
                }
            }
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .frame(height: 28)
    }
}

struct StageDot: View {
    let status: StageStatus
    var body: some View {
        ZStack {
            switch status {
            case .done:
                Circle().fill(Theme.accent)
                Image(systemName: "checkmark").font(.system(size: 12, weight: .bold)).foregroundStyle(Theme.onAccent)
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

// MARK: - rental failed

private struct RetryRentalCard: View {
    let flow: RunFlow
    let failure: FailedRental

    var body: some View {
        // The label names the card `retryRental()` actually sends
        // (`pod.gpu`, the current .env card), not the one that failed.
        let gpu = flow.pod?.gpu ?? failure.gpu
        VStack(alignment: .leading, spacing: 10) {
            Label("Rental failed", systemImage: "exclamationmark.triangle.fill")
                .font(.headline).foregroundStyle(Theme.danger)
            Text(failure.detail).font(.subheadline)
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
            if flow.needsRecheck {
                Button("Check again") { Task { await flow.recheck() } }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(flow.isSpending)
            }
        }
        .heroSurface()
    }
}

