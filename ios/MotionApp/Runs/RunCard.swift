import MotionKit
import SwiftUI

/// One run on the Runs tab: a strip of its jobs' pictures, then its name,
/// status, a bar with one segment per job, and what it was made with. The
/// jobs come from the run's detail, read when the card appears; until that
/// answers, the card shows what the run list alone knows.
struct RunCard: View {
    let run: RunSummary
    let store: RunDetailStore
    let progress: LiveProgress

    private var jobs: [JobProgress] { store.detail?.jobs ?? [] }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            covers
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(RunName.title(run.batch ?? run.id)).font(.headline).lineLimit(1)
                Spacer(minLength: 0)
                RunStatusChip(status: run.status)
            }
            if !jobs.isEmpty { SegmentBar(statuses: jobs.map(\.status)) }
            VStack(alignment: .leading, spacing: 3) {
                Text(countsText).font(.subheadline.monospacedDigit())
                    .foregroundStyle(store.detail.map { RunDigest($0).failed > 0 } == true ? Theme.danger : Theme.label)
                TimelineView(.periodic(from: .now, by: 30)) { ctx in
                    Text(metaText(now: ctx.date)).font(.footnote).foregroundStyle(Theme.secondary).lineLimit(1)
                }
            }
        }
        .padding(12)
        .background(Theme.surface, in: .rect(cornerRadius: 22))
        .contentShape(.rect(cornerRadius: 22))
        .accessibilityElement(children: .combine)
        // A changed run re-reads its detail; an unchanged one answers 304.
        .task(id: run.updatedAt) { await store.refresh() }
    }

    // MARK: - covers

    @ViewBuilder private var covers: some View {
        if jobs.isEmpty {
            placeholder.frame(height: 190)
        } else {
            let shown = Array(jobs.prefix(3))
            HStack(spacing: 6) {
                ForEach(Array(shown.enumerated()), id: \.element.id) { i, job in
                    JobCover(store: store, job: job, runLive: run.status.isLive)
                        .overlay {
                            if i == shown.count - 1, jobs.count > shown.count {
                                ZStack {
                                    Color.black.opacity(0.55)
                                    Text("+\(jobs.count - shown.count + 1)").font(.title2.weight(.semibold))
                                        .foregroundStyle(.white)
                                }
                            }
                        }
                        .clipShape(.rect(cornerRadius: 14))
                }
            }
            .frame(height: 190)
        }
    }

    /// No jobs to show yet: a Phase A still dressing, or a detail not read yet.
    @ViewBuilder private var placeholder: some View {
        ZStack {
            Theme.surfaceRaised
            if run.status.isLive { ScanBand() }
            VStack(spacing: 6) {
                Image(systemName: run.status == .phaseA ? "tshirt" : "film.stack")
                    .font(.title2)
                    .symbolEffect(.pulse, options: .repeating, isActive: run.status.isLive)
                    .foregroundStyle(run.status.isLive ? Theme.accent : Theme.tertiary)
                if run.status == .phaseA {
                    Text("Dressing looks on the VPS").font(.footnote).foregroundStyle(Theme.secondary)
                }
            }
        }
        .clipShape(.rect(cornerRadius: 14))
    }

    // MARK: - text

    private var countsText: String {
        if run.status == .phaseA {
            return progress.total > 0
                ? "\(progress.done) of \(progress.total) looks ready · no GPU rented yet"
                : "Starting try-on · no GPU rented yet"
        }
        if let d = store.detail { return RunDigest(d).countsText }
        return "\(run.jobsDone) of \(run.jobsTotal) jobs done"
    }

    private func metaText(now: Date) -> String {
        let age = Format.ago(now.timeIntervalSince1970 - run.updatedAt)
        let setup = store.detail.flatMap { RunDigest($0).setupText }
        let videos = store.detail.map(\.outputs.count) ?? 0
        return ([setup, videos > 0 ? "\(videos) video\(videos == 1 ? "" : "s")" : nil, age]
            .compactMap { $0 }).joined(separator: " · ")
    }
}

/// A job's best picture so far: its video's poster, else its try-on, else
/// the character it started from. A running job is scanned; a finished or
/// failed one wears a badge.
private struct JobCover: View {
    let store: RunDetailStore
    let job: JobProgress
    let runLive: Bool
    @Environment(AppModel.self) private var model
    @State private var image: UIImage?

    var body: some View {
        Theme.surfaceRaised
            .overlay {
                if let image {
                    Image(uiImage: image).resizable().scaledToFill()
                        .opacity(dimmed ? 0.7 : 1)
                        .saturation(dimmed ? 0.5 : 1)
                        .transition(.opacity)
                } else {
                    Image(systemName: "person.crop.rectangle").font(.title2).foregroundStyle(Theme.tertiary)
                }
            }
            .overlay { if job.status == .running { ScanBand() } }
            .overlay(alignment: .topTrailing) { badge.padding(6) }
            .clipped()
            .frame(maxWidth: .infinity)
            .task(id: "\(job.id)/\(store.detail?.outputs.count ?? 0)") {
                let loaded = await load()
                withAnimation(.easeOut(duration: 0.3)) { image = loaded }
            }
    }

    /// A job the run never reached is shown, but quieter than one it did.
    private var dimmed: Bool { job.status == .pending || job.status == .unknown }

    @ViewBuilder private var badge: some View {
        switch job.status {
        case .done:
            glyph("checkmark", fg: Theme.onAccent, bg: Theme.accent)
        case .error:
            glyph("xmark", fg: .white, bg: Theme.danger)
        case .running:
            ProgressView().controlSize(.mini).tint(.white)
                .frame(width: 22, height: 22).background(.black.opacity(0.5), in: .circle)
        case .pending, .unknown:
            if runLive { glyph("hourglass", fg: .white, bg: .black.opacity(0.5)) }
        }
    }

    private func glyph(_ name: String, fg: Color, bg: Color) -> some View {
        Image(systemName: name).font(.system(size: 11, weight: .bold)).foregroundStyle(fg)
            .frame(width: 22, height: 22).background(bg, in: .circle)
    }

    private func load() async -> UIImage? {
        if let d = store.detail, let batch = d.batch, let name = d.outputs(forJob: job.id).first {
            let file = OutputFile(name: name, bytes: 0)
            if let hit = OutputPosters.shared.cached(batch: batch, file: file) { return hit }
            if let poster = await OutputPosters.shared.poster(client: store.client, batch: batch, file: file) {
                return poster
            }
        }
        if let data = await store.tryonImage(forJob: job.id), let tryon = UIImage(data: data) { return tryon }
        return await model.inputImage(job.setup?.inputs[MaterialRole.character.rawValue])
    }
}

/// One segment per job, colored by where it stands.
private struct SegmentBar: View {
    let statuses: [StageStatus]
    var body: some View {
        HStack(spacing: 3) {
            ForEach(Array(statuses.enumerated()), id: \.offset) { _, status in
                Capsule().fill(color(status)).frame(height: 5)
            }
        }
        .accessibilityHidden(true)
    }

    private func color(_ status: StageStatus) -> Color {
        switch status {
        case .done: Theme.accent
        case .error: Theme.danger
        case .running: Theme.label
        case .pending, .unknown: Theme.tertiary.opacity(0.5)
        }
    }
}

/// The run's state as a capsule: icon and word, never color alone.
struct RunStatusChip: View {
    let status: RunStatus
    var body: some View {
        HStack(spacing: 5) {
            if status.isLive {
                PulseDot(color: Theme.accent, size: 6)
            } else {
                Image(systemName: symbol).font(.caption.weight(.semibold))
            }
            Text(word).font(.caption.weight(.semibold))
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 9).padding(.vertical, 4)
        .background(tint.opacity(0.14), in: .capsule)
    }

    private var word: String {
        switch status {
        case .running: "Generating"
        case .phaseA: "Try-on"
        case .done: "Done"
        case .error: "Failed"
        case .stopped: "Stopped"
        case .unknown: "Unknown"
        }
    }

    private var symbol: String {
        switch status {
        case .done: "checkmark"
        case .error: "exclamationmark.triangle.fill"
        default: "pause.fill"
        }
    }

    private var tint: Color {
        switch status {
        case .running, .phaseA, .done: Theme.accent
        case .error: Theme.danger
        case .stopped, .unknown: Theme.secondary
        }
    }
}
