import MotionKit
import SwiftUI

/// Two-column masonry, newest first: each tile is one slot of one generation.
struct StudioGrid: View {
    let studio: StudioStore
    let onOpen: (StudioGeneration, String) -> Void

    struct Tile: Identifiable {
        let generation: StudioGeneration
        let slot: Int
        var id: String { "\(generation.id)-\(slot)" }
        var state: StudioSlot { generation.slots[slot] }
    }

    private var tiles: [Tile] {
        (studio.project?.generations ?? []).reversed().flatMap { g in
            g.slots.indices.map { Tile(generation: g, slot: $0) }
        }
    }

    var body: some View {
        let all = tiles
        HStack(alignment: .top, spacing: 4) {
            column(all.enumerated().filter { $0.offset % 2 == 0 }.map(\.element))
            column(all.enumerated().filter { $0.offset % 2 == 1 }.map(\.element))
        }
    }

    private func column(_ items: [Tile]) -> some View {
        LazyVStack(spacing: 4) {
            ForEach(items) { tile in StudioTile(studio: studio, tile: tile, onOpen: onOpen) }
        }
    }
}

private struct StudioTile: View {
    let studio: StudioStore
    let tile: StudioGrid.Tile
    let onOpen: (StudioGeneration, String) -> Void
    @State private var image: UIImage?

    private var ratio: CGFloat {
        let parts = tile.generation.aspect.split(separator: ":").compactMap { Double($0) }
        return parts.count == 2 ? CGFloat(parts[0] / parts[1]) : 1
    }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12).fill(Theme.surface)
            switch tile.state.status {
            case .queued, .running:
                StackLoader(size: 40)
            case .error:
                VStack(spacing: 8) {
                    Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.danger)
                    Text(tile.state.error ?? "Failed").font(.caption2).multilineTextAlignment(.center)
                        .foregroundStyle(Theme.secondary).lineLimit(4)
                    // Priced like Send: Retry is a paid call too, and only
                    // the failed slots are re-bought (Qwen: all of them).
                    let n = studio.retryCount(for: tile.generation)
                    Button("Retry · \(StudioFormat.usd(tile.generation.unitPriceUsd * Double(n)))") {
                        Task { _ = await studio.retry(tile.generation) }
                    }
                    .buttonStyle(.bordered).controlSize(.small)
                    .disabled(studio.isSending)
                }.padding(8)
            case .done:
                if let image {
                    Image(uiImage: image).resizable().scaledToFill()
                } else {
                    ProgressView()
                }
            }
        }
        .aspectRatio(ratio, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: 12))
        .contentShape(Rectangle())
        .onTapGesture { if let id = tile.state.image { onOpen(tile.generation, id) } }
        .task(id: tile.state.image) {
            guard let pid = studio.project?.id, let id = tile.state.image,
                  let data = await studio.image(projectID: pid, imageID: id) else { return }
            image = UIImage(data: data)
        }
        .accessibilityIdentifier("studio.tile")
    }
}
