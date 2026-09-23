import SwiftUI
import MotionKit

/// The five GPUs from `GET /v1/gpu/stock`. Selecting is free (no dialog);
/// the rent panel passes `onSelected` to re-read its quote.
struct GpuPickerView: View {
    let store: GpuStore
    let spending: Bool
    let hasLease: Bool
    var onMigrate: ((String) -> Void)? = nil
    var onSelected: (() async -> Void)? = nil
    @State private var expanded: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let error = store.error {
                if store.stock == nil {
                    ErrorBanner(error: error) { await store.load() }
                } else {
                    HStack {
                        Text("Couldn't reach runpodctl — showing the last list.")
                            .font(Theme.sans(12)).foregroundStyle(Theme.amber)
                        Spacer()
                        Button("Retry") { Task { await store.load(force: true) } }
                            .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                    }
                }
            }
            if let stock = store.stock {
                ForEach(stock.gpus) { row in rowView(row, stock: stock) }
            } else if store.isLoading {
                ProgressView("Reading stock…").frame(maxWidth: .infinity).padding(.vertical, 20)
            }
            if hasLease {
                Text("A change applies to the next rental.").font(Theme.sans(12)).foregroundStyle(Theme.ink3)
            }
            if spending {
                Text("A spend request is in flight — the GPU can't change until it's answered.")
                    .font(Theme.sans(12)).foregroundStyle(Theme.ink3)
            }
            if let message = store.message {
                Text(message).font(Theme.sans(12)).foregroundStyle(Theme.amber)
            }
        }
        .opacity(store.isStale ? 0.6 : 1)
    }

    private func rowView(_ row: GpuStockRow, stock: GpuStock) -> some View {
        let selected = row.gpu == stock.selected
        let regions = stock.otherRegions.filter { $0.gpu == row.gpu }
        return VStack(alignment: .leading, spacing: 8) {
            Button {
                Task {
                    if await store.select(row.gpu, whileSpending: spending) { await onSelected?() }
                }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(selected ? Theme.lime : Theme.ink3)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(row.name).font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink)
                        Text(row.summary).font(Theme.mono(11))
                            .foregroundStyle(row.soldOutEverywhere ? Theme.amber : Theme.ink2)
                    }
                    Spacer()
                    if store.selecting == row.gpu { ProgressView().controlSize(.small) }
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .disabled(selected || store.selecting != nil || spending)
            .accessibilityIdentifier("gpu.row.\(row.gpu)")
            if !regions.isEmpty {
                Button(expanded == row.gpu ? "Hide other regions" : "Other regions (\(regions.count))") {
                    expanded = expanded == row.gpu ? nil : row.gpu
                }
                .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                if expanded == row.gpu {
                    ForEach(regions, id: \.datacenter) { region in
                        HStack {
                            Text("\(region.datacenter) · stock \(region.stock)"
                                 + (region.usdPerHr.map { " · \(Format.usd($0))/h" } ?? ""))
                                .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                            Spacer()
                            if let onMigrate {
                                Button("Migrate to \(region.datacenter) →") { onMigrate(region.datacenter) }
                                    .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                            }
                        }
                    }
                }
            }
        }
        .padding(14)
        .card(border: selected ? Theme.limeLine : Theme.line)
    }
}
