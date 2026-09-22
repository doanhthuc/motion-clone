import SwiftUI
import MotionKit

struct OutputsView: View {
    let store: OutputsStore

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Outputs").font(Theme.sans(33, .bold)).foregroundStyle(Theme.ink)
                    Spacer()
                    if store.isStale { StaleTag(lastSuccess: store.lastSuccess) }
                    Text("\(store.batches.count) batches").font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                }
                if let error = store.error, !store.loaded { ErrorBanner(error: error) { await store.refresh() } }
                if store.loaded && store.batches.isEmpty {
                    Text("No finished outputs yet.").font(Theme.sans(14)).foregroundStyle(Theme.ink2)
                        .frame(maxWidth: .infinity).padding(.vertical, 60)
                }
                ForEach(store.batches) { batch in
                    SectionLabel(text: batch.batch)
                    ForEach(batch.files) { file in
                        NavigationLink {
                            OutputPlayerView(client: store.client, batch: batch.batch, file: file)
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: file.isVideo ? "play.rectangle.fill" : "photo")
                                    .foregroundStyle(Theme.lime).frame(width: 28)
                                Text(file.name).font(Theme.mono(12)).foregroundStyle(Theme.ink1).lineLimit(1)
                                Spacer(minLength: 0)
                                Text(ByteCountFormatter.string(fromByteCount: Int64(file.bytes), countStyle: .file))
                                    .font(Theme.mono(11)).foregroundStyle(Theme.ink3)
                            }
                            .padding(12).card()
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(.horizontal, 20)
        }
        .background(Theme.bg)
        .refreshable { await store.refresh() }
        .task { await store.refresh() }
    }
}
