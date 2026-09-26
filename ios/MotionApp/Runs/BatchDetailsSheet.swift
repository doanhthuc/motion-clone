import MotionKit
import SwiftUI

/// Everything a batch was made of, job by job: the inputs as pictures, the
/// pipeline and try-on provider, each stage's time, and how many videos came
/// out. Opened from the ⓘ button (whole batch) or a swipe up on a job's page
/// (scrolled to that job).
struct BatchDetailsSheet: View {
    let detail: RunDetail
    var focus: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollViewReader { reader in
                List {
                    Section {
                        row("Batch", detail.batch ?? "—")
                        row("Run", detail.id)
                        row("Jobs", "\(detail.jobsDone) of \(detail.jobsTotal) done")
                        if !pipelines.isEmpty { row("Pipeline", pipelines.joined(separator: ", ")) }
                        row("Videos", "\(detail.outputs.count)")
                    }
                    ForEach(Array(detail.jobs.enumerated()), id: \.element.id) { n, job in
                        JobDetailsSection(detail: detail, job: job, number: n + 1)
                            .id(job.id)
                    }
                }
                .onAppear { if let focus { reader.scrollTo(focus, anchor: .top) } }
            }
            .navigationTitle("Batch details")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .presentationDetents([.medium, .large])
        // Solid: the glass default let the Continue button's lime bleed through.
        .presentationBackground(Theme.bg)
        .presentationDragIndicator(.visible)
    }

    private var pipelines: [String] {
        var seen: [String] = []
        for p in detail.jobs.compactMap(\.setup?.pipeline) where !seen.contains(p) { seen.append(p) }
        return seen
    }

    private func row(_ label: String, _ value: String) -> some View {
        LabeledContent(label) {
            Text(value).lineLimit(1).truncationMode(.middle).monospacedDigit()
        }
    }
}

private struct JobDetailsSection: View {
    let detail: RunDetail
    let job: JobProgress
    let number: Int

    /// Character, outfit, background, then the driver video — the order the
    /// composer lists them in.
    private var inputs: [(role: String, id: String)] {
        let order = ["character", "outfit", "background", "driver"]
        return (job.setup?.inputs ?? [:])
            .sorted { (order.firstIndex(of: $0.key) ?? 99, $0.key) < (order.firstIndex(of: $1.key) ?? 99, $1.key) }
            .map { ($0.key, $0.value) }
    }

    var body: some View {
        Section {
            if !inputs.isEmpty {
                ScrollView(.horizontal) {
                    HStack(alignment: .top, spacing: 10) {
                        ForEach(inputs, id: \.role) { input in
                            InputThumb(role: input.role, materialID: input.id)
                        }
                    }
                    .padding(.vertical, 4)
                }
                .scrollIndicators(.hidden)
            }
            if let setup = job.setup {
                LabeledContent("Pipeline", value: setup.pipeline)
                if let provider = setup.provider { LabeledContent("Try-on by", value: provider) }
            }
            ForEach(Array(job.stages.enumerated()), id: \.offset) { _, stage in
                HStack(spacing: 10) {
                    StageDot(status: stage.status).scaleEffect(0.8).frame(width: 22, height: 22)
                    Text(Format.stageName(stage.name))
                    Spacer()
                    Text(stageDetail(stage)).font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                }
            }
            let files = detail.outputs(forJob: job.id)
            if !files.isEmpty {
                LabeledContent("Videos", value: "\(files.count)")
            }
        } header: {
            HStack {
                Text("\(number). \(job.id)").lineLimit(1).truncationMode(.middle)
                Spacer()
                Text(job.status.rawValue.capitalized)
                    .foregroundStyle(job.status == .error ? Theme.danger : Theme.secondary)
            }
        }
    }

    private func stageDetail(_ stage: StageProgress) -> String {
        switch stage.status {
        case .done: stage.elapsedSec.map { Format.clock($0) } ?? "done"
        case .running: "running"
        case .error: "failed"
        case .pending, .unknown: "—"
        }
    }
}

/// One input as a picture with its role under it. A material deleted since
/// the run shows its name instead.
private struct InputThumb: View {
    let role: String
    let materialID: String
    @Environment(AppModel.self) private var model
    @State private var image: UIImage?
    @State private var loaded = false

    var body: some View {
        VStack(spacing: 4) {
            Theme.surfaceRaised
                .overlay {
                    if let image {
                        Image(uiImage: image).resizable().scaledToFill()
                    } else if loaded {
                        Text(materialID.split(separator: "/").last.map(String.init) ?? materialID)
                            .font(.caption2).foregroundStyle(Theme.secondary)
                            .multilineTextAlignment(.center).padding(4)
                    }
                }
                .frame(width: 72, height: 96)
                .clipShape(.rect(cornerRadius: Theme.Radius.small))
                .overlay(alignment: .bottomTrailing) {
                    if role == "driver" {
                        Image(systemName: "play.fill").font(.caption2).foregroundStyle(.white)
                            .padding(5).shadow(radius: 2)
                    }
                }
            Text(MaterialRole(rawValue: role)?.title ?? role.capitalized)
                .font(.caption).foregroundStyle(Theme.secondary)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(role): \(materialID)")
        .task(id: materialID) {
            image = await model.inputImage(materialID)
            loaded = true
        }
    }
}
