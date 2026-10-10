import SwiftUI
import MotionKit

/// Finished outputs as posters, newest batch first, in the Photos grid: three
/// columns edge to edge with hairline gaps. The file names this used to list
/// (`IMG7145-IMG7197-tiktok179024-2.mp4`) differ only at the end, so a list of
/// them could not tell one video from the next; the frame can. As in Photos,
/// a long press peeks at the frame over Save, Share and Delete, and Select
/// deletes many at once (2026-10-10).
struct OutputsView: View {
    let store: OutputsStore
    @State private var selection = Selection()
    @State private var exporter = MediaExporter()
    /// One file from a long press, or the whole selection.
    @State private var deleting: [String]?
    @State private var bulkDeleting = false

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 2), count: 3)

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 28) {
                if let error = store.error, !store.loaded {
                    ErrorBanner(error: error) { await store.refresh() }
                        .heroSurface().padding(.horizontal, 16)
                }
                if store.isStale {
                    StaleTag(lastSuccess: store.lastSuccess).padding(.horizontal, 16)
                }
                if let message = store.message {
                    MessageCard(text: message) { store.dismissMessage() }
                        .heroSurface().padding(.horizontal, 16)
                }
                ForEach(store.batches) { batch in
                    VStack(alignment: .leading, spacing: 10) {
                        BatchHeader(batch: batch).padding(.horizontal, 16)
                        LazyVGrid(columns: columns, spacing: 2) {
                            ForEach(batch.files) { file in
                                if selection.active {
                                    selectableTile(batch, file)
                                } else {
                                    tile(batch, file)
                                }
                            }
                        }
                    }
                }
            }
            .padding(.top, 8)
            .padding(.bottom, 24)
        }
        .background(Theme.bg)
        .overlay {
            if store.loaded && store.batches.isEmpty {
                EmptyNote(title: "No outputs yet", systemImage: "play.rectangle",
                          message: "Finished videos from every run land here.")
            }
        }
        .navigationTitle("Outputs")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await store.refresh() }
        .task { await store.refresh() }
        .selectionMode($selection, selectable: allKeys, busy: bulkDeleting) {
            deleting = allKeys.filter(selection.contains)
        }
        .confirmationDialog(deleteTitle, isPresented: Binding { deleting != nil } set: { if !$0 { deleting = nil } },
                            titleVisibility: .visible, presenting: deleting) { keys in
            Button(keys.count == 1 ? "Delete" : "Delete \(keys.count)", role: .destructive) { delete(keys) }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text("Removed from the VPS. Copies already saved to Photos stay.")
        }
        .sheet(item: $exporter.sharing) { shared in
            ActivityView(items: [shared.url])
                .presentationDetents([.medium, .large])
                .onDisappear { try? FileManager.default.removeItem(at: shared.url.deletingLastPathComponent()) }
        }
        .overlay(alignment: .bottom) { MediaStatusView(exporter: exporter).padding(.bottom, 12) }
    }

    private var allKeys: [String] {
        store.batches.flatMap { b in b.files.map { OutputsStore.key(b.batch, $0) } }
    }

    private var deleteTitle: String {
        let n = deleting?.count ?? 0
        let videos = deleting?.compactMap { store.file(for: $0) }.allSatisfy(\.file.isVideo) ?? true
        let noun = videos ? (n == 1 ? "video" : "videos") : (n == 1 ? "file" : "files")
        return n == 1 ? "Delete this \(noun)?" : "Delete \(n) \(noun)?"
    }

    /// Tap opens the feed; a long press peeks at the frame over Photos' quick actions.
    private func tile(_ batch: OutputBatch, _ file: OutputFile) -> some View {
        NavigationLink {
            OutputFeedView(client: store.client, batch: batch, startAt: file)
        } label: {
            OutputPosterTile(client: store.client, batch: batch.batch, file: file)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(file.name)
        .contextMenu {
            Button(file.isVideo ? "Save Video" : "Save Image", systemImage: "square.and.arrow.down") {
                Task { await exporter.saveToPhotos(isVideo: file.isVideo, download(batch.batch, file)) }
            }
            Button("Share", systemImage: "square.and.arrow.up") {
                Task { await exporter.share(download(batch.batch, file)) }
            }
            Divider()
            Button("Delete", systemImage: "trash", role: .destructive) {
                deleting = [OutputsStore.key(batch.batch, file)]
            }
        } preview: {
            OutputPeek(client: store.client, batch: batch.batch, file: file)
        }
    }

    private func selectableTile(_ batch: OutputBatch, _ file: OutputFile) -> some View {
        let key = OutputsStore.key(batch.batch, file)
        let picked = selection.contains(key)
        return Button { selection.toggle(key) } label: {
            OutputPosterTile(client: store.client, batch: batch.batch, file: file)
                .overlay { if picked { Color.white.opacity(0.15) } }
                // Top, not Photos' bottom corner: the duration badge sits there.
                .overlay(alignment: .topTrailing) { SelectionCheck(selected: picked) }
        }
        .buttonStyle(.plain)
        .accessibilityLabel(file.name)
        .accessibilityAddTraits(picked ? .isSelected : [])
    }

    private func download(_ batch: String, _ file: OutputFile) -> () async throws -> URL {
        let client = store.client
        return { try await client.download("v1", "outputs", batch, file.name) }
    }

    private func delete(_ keys: [String]) {
        bulkDeleting = true
        Task {
            let kept = await store.delete(keys)
            if selection.active { withAnimation(.snappy) { selection.keep(kept) } }
            bulkDeleting = false
        }
    }
}

/// The long-press preview: the poster frame at full size, as Photos lifts
/// the photo out of the grid.
private struct OutputPeek: View {
    let client: APIClient
    let batch: String
    let file: OutputFile
    @State private var poster: UIImage?

    var body: some View {
        Group {
            if let poster {
                Image(uiImage: poster).resizable().scaledToFit()
            } else {
                Theme.surface.aspectRatio(9 / 16, contentMode: .fit)
                    .overlay { ProgressView() }
            }
        }
        .frame(width: 300)
        .task {
            poster = OutputPosters.shared.cached(batch: batch, file: file)
            if poster == nil { poster = await OutputPosters.shared.poster(client: client, batch: batch, file: file) }
        }
    }
}

/// "Thu, Sep 24" over "11:36 · 2 videos". Batch names the runner stamps are
/// `YYYY-MM-DD-HHMM`; any other name is shown as typed, dated by its last write.
struct BatchHeader: View {
    let batch: OutputBatch

    var body: some View {
        let stamp = BatchStamp(batch.batch)
        VStack(alignment: .leading, spacing: 2) {
            Text(stamp.map { $0.date.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day()) } ?? batch.batch)
                .font(.title3.weight(.semibold))
                .foregroundStyle(Theme.label)
            Text(detail(stamp))
                .font(.subheadline.monospacedDigit())
                .foregroundStyle(Theme.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    private func detail(_ stamp: BatchStamp?) -> String {
        let when = (stamp?.date ?? Date(timeIntervalSince1970: batch.updatedAt))
        let time = stamp != nil ? when.formatted(date: .omitted, time: .shortened)
                                : when.formatted(.relative(presentation: .named))
        return "\(time) · \(countText)"
    }

    private var countText: String {
        let videos = batch.files.filter(\.isVideo).count
        let images = batch.files.count - videos
        var parts: [String] = []
        if videos > 0 { parts.append(videos == 1 ? "1 video" : "\(videos) videos") }
        if images > 0 { parts.append(images == 1 ? "1 image" : "\(images) images") }
        return parts.joined(separator: ", ")
    }
}

/// The runner's `2026-09-24-1136` batch stamp, read as local time.
struct BatchStamp {
    let date: Date

    init?(_ name: String) {
        let parts = name.split(separator: "-")
        guard parts.count == 4, parts[3].count == 4,
              let y = Int(parts[0]), let mo = Int(parts[1]), let d = Int(parts[2]), let hm = Int(parts[3]),
              let date = Calendar.current.date(from: DateComponents(
                  year: y, month: mo, day: d, hour: hm / 100, minute: hm % 100))
        else { return nil }
        self.date = date
    }
}
