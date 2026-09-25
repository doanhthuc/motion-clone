import SwiftUI
import MotionKit

/// Finished outputs as posters, newest batch first, in the Photos grid: three
/// columns edge to edge with hairline gaps. The file names this used to list
/// (`IMG7145-IMG7197-tiktok179024-2.mp4`) differ only at the end, so a list of
/// them could not tell one video from the next; the frame can.
struct OutputsView: View {
    let store: OutputsStore

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
                ForEach(store.batches) { batch in
                    VStack(alignment: .leading, spacing: 10) {
                        BatchHeader(batch: batch).padding(.horizontal, 16)
                        LazyVGrid(columns: columns, spacing: 2) {
                            ForEach(batch.files) { file in
                                NavigationLink {
                                    OutputFeedView(client: store.client, batch: batch, startAt: file)
                                } label: {
                                    OutputPosterTile(client: store.client, batch: batch.batch, file: file)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel(file.name)
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
        .refreshable { await store.refresh() }
        .task { await store.refresh() }
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
