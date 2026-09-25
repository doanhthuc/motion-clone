import SwiftUI
import MotionKit

struct OutputsView: View {
    let store: OutputsStore

    var body: some View {
        List {
            if let error = store.error, !store.loaded {
                Section { ErrorBanner(error: error) { await store.refresh() } }
            }
            if store.isStale {
                Section { StaleTag(lastSuccess: store.lastSuccess) }
            }
            ForEach(store.batches) { batch in
                Section(batch.batch) {
                    ForEach(batch.files) { file in
                        NavigationLink {
                            OutputFeedView(client: store.client, batch: batch, startAt: file)
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: file.isVideo ? "play.rectangle" : "photo")
                                    .foregroundStyle(Theme.secondary)
                                    .frame(width: 24)
                                    .accessibilityHidden(true)
                                Text(file.name).font(.body).lineLimit(1).truncationMode(.middle)
                                Spacer(minLength: 8)
                                Text(ByteCountFormatter.string(fromByteCount: Int64(file.bytes), countStyle: .file))
                                    .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                            }
                        }
                    }
                }
            }
        }
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
