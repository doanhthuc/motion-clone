import MotionKit
import SwiftUI

/// Everything a batch was made of, job by job: the inputs as pictures, the
/// pipeline and try-on provider, each stage's time, and how many videos came
/// out. Opened from the ⓘ button (whole batch) or a swipe up on a job's page
/// (scrolled to that job).
struct BatchDetailsSheet: View {
    let detail: RunDetail
    /// Reads each job's try-on — once a job has its video, the run page shows
    /// only the poster, and this was the one place left to look for it.
    let store: RunDetailStore
    var focus: String?
    @Environment(\.dismiss) private var dismiss
    /// Presented from here: a cover hung on a List section closed this sheet
    /// instead of opening (simulator, 2026-09-28).
    @State private var viewing: TryonTarget?

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
                        JobDetailsSection(detail: detail, store: store, job: job, number: n + 1) { viewing = $0 }
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
        .fullScreenCover(item: $viewing) { TryonViewer(target: $0) }
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
    let store: RunDetailStore
    let job: JobProgress
    let number: Int
    let view: (TryonTarget) -> Void
    @State private var tryonData: Data?
    @State private var tryon: UIImage?

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
            if !inputs.isEmpty || tryon != nil {
                ScrollView(.horizontal) {
                    HStack(alignment: .top, spacing: 10) {
                        ForEach(inputs, id: \.role) { input in
                            InputThumb(role: input.role, materialID: input.id)
                        }
                        if let tryon, let tryonData {
                            // The try-on is made from the inputs, not one of
                            // them: a rule the height of the tiles sets it apart.
                            if !inputs.isEmpty {
                                Rectangle().fill(Theme.tertiary).frame(width: 1, height: 96)
                                    .padding(.horizontal, 4)
                                    .accessibilityHidden(true)
                            }
                            Button { view(TryonTarget(name: job.id, image: tryon, data: tryonData)) } label: { TryonThumb(image: tryon) }
                                .buttonStyle(.plain)
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
        .task(id: "\(job.id)/\(detail.updatedAt)") {
            tryonData = await store.tryonImage(forJob: job.id)
            tryon = tryonData.flatMap(UIImage.init(data:))
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

private struct TryonTarget: Identifiable {
    let name: String
    let image: UIImage
    /// The bytes as served, saved as-is rather than re-encoded.
    let data: Data
    var id: String { name }
}

/// The job's try-on next to the inputs it was made from, marked so it does
/// not read as one more input.
private struct TryonThumb: View {
    let image: UIImage

    var body: some View {
        VStack(spacing: 4) {
            Image(uiImage: image).resizable().scaledToFill()
                .frame(width: 72, height: 96)
                .clipShape(.rect(cornerRadius: Theme.Radius.small))
                .overlay {
                    RoundedRectangle(cornerRadius: Theme.Radius.small).strokeBorder(Theme.accent, lineWidth: 1.5)
                }
                .overlay(alignment: .bottomTrailing) {
                    Image(systemName: "sparkles").font(.caption2).foregroundStyle(.white)
                        .padding(5).shadow(radius: 2)
                }
            Text("Try-on").font(.caption).foregroundStyle(Theme.accent)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Try-on image")
        .accessibilityAddTraits([.isButton, .isImage])
        .accessibilityHint("Opens it full screen")
    }
}

/// A try-on at full size on black: pinch to zoom, double tap to toggle zoom,
/// drag down or Done to close, long press to save or share.
private struct TryonViewer: View {
    let target: TryonTarget
    @Environment(\.dismiss) private var dismiss
    @State private var scale: CGFloat = 1
    @GestureState private var pinch: CGFloat = 1
    @GestureState private var drag: CGSize = .zero
    @State private var exporter = MediaExporter()
    @State private var showingActions = false

    var body: some View {
        NavigationStack {
            Image(uiImage: target.image).resizable().scaledToFit()
                .scaleEffect(scale * pinch)
                .offset(y: scale == 1 ? max(drag.height, 0) : 0)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .contentShape(.rect)
                .gesture(MagnifyGesture()
                    .updating($pinch) { value, state, _ in state = value.magnification }
                    .onEnded { value in scale = min(max(scale * value.magnification, 1), 4) })
                .simultaneousGesture(DragGesture(minimumDistance: 20)
                    .updating($drag) { value, state, _ in state = value.translation }
                    .onEnded { value in if scale == 1, value.translation.height > 120 { dismiss() } })
                .onTapGesture(count: 2) { withAnimation(.snappy) { scale = scale > 1 ? 1 : 2 } }
                .onLongPressGesture(minimumDuration: 0.35) { showingActions = true }
                .overlay(alignment: .bottom) {
                    MediaStatusView(exporter: exporter).padding(.horizontal, 16).padding(.bottom, 12)
                }
                .accessibilityLabel("Try-on for \(target.name)")
                .accessibilityAddTraits(.isImage)
                .accessibilityAction(named: "Save to Photos") {
                    Task { await exporter.saveToPhotos(isVideo: false, file) }
                }
                .accessibilityAction(named: "Share") { Task { await exporter.share(file) } }
                .background(.black)
                .navigationTitle("Try-on")
                .navigationBarTitleDisplayMode(.inline)
                .toolbarBackground(.hidden, for: .navigationBar)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
                }
        }
        .mediaActions(isPresented: $showingActions, exporter: exporter, isVideo: false, rate: nil, download: file)
    }

    /// Already in memory, so "downloading" is writing it out. Its own folder:
    /// save and share both remove the file's parent directory afterwards.
    private func file() async throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let ext = target.data.starts(with: [0x89, 0x50, 0x4E, 0x47]) ? "png" : "jpg"
        let url = dir.appendingPathComponent("\(target.name)-tryon.\(ext)")
        try target.data.write(to: url)
        return url
    }
}
