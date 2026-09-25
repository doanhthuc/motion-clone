import SwiftUI
import MotionKit

/// The five GPUs from `GET /v1/gpu/stock`, as one `List` section. Selecting is
/// free (no dialog); the rent panel passes `onSelected` to re-read its quote.
struct GpuPickerView: View {
    let store: GpuStore
    let spending: Bool
    let hasLease: Bool
    var onMigrate: ((String) -> Void)? = nil
    var onSelected: (() async -> Void)? = nil

    var body: some View {
        Section {
            if let error = store.error {
                if store.stock == nil {
                    ErrorBanner(error: error) { await store.load() }
                } else {
                    HStack {
                        Text("Couldn't reach runpodctl — showing the last list.")
                            .font(.footnote).foregroundStyle(Theme.warning)
                        Spacer()
                        Button("Retry") { Task { await store.load(force: true) } }
                            .font(.footnote.weight(.semibold)).buttonStyle(.borderless)
                    }
                }
            }
            if let stock = store.stock {
                ForEach(stock.gpus) { row in rowView(row, stock: stock) }
            } else if store.isLoading {
                LoadingBlock(title: "Reading stock…")
            }
        } header: {
            Text("GPU")
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                if hasLease { Text("A change applies to the next rental.") }
                if spending {
                    Text("A spend request is in flight — the GPU can't change until it's answered.")
                }
                if let message = store.message {
                    Text(message).foregroundStyle(Theme.warning)
                }
            }
        }
        .opacity(store.isStale ? 0.6 : 1)
    }

    private func rowView(_ row: GpuStockRow, stock: GpuStock) -> some View {
        let selected = row.gpu == stock.selected
        let regions = stock.otherRegions.filter { $0.gpu == row.gpu }
        return HStack(spacing: 12) {
            selectButton(row, selected: selected)
            if !regions.isEmpty { RegionsMenu(regions: regions, onMigrate: onMigrate) }
        }
    }

    private func selectButton(_ row: GpuStockRow, selected: Bool) -> some View {
        Button {
            // Selected rows stay enabled so they don't render dimmed; a tap on
            // the current choice is simply a no-op.
            guard !selected else { return }
            Task {
                if await store.select(row.gpu, whileSpending: spending) { await onSelected?() }
            }
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(row.name).font(.body).foregroundStyle(Theme.label)
                    Text(row.summary).font(.subheadline.monospacedDigit())
                        .foregroundStyle(row.soldOutEverywhere ? Theme.warning : Theme.secondary)
                }
                Spacer()
                if store.selecting == row.gpu {
                    ProgressView()
                } else if selected {
                    Image(systemName: "checkmark").font(.body.weight(.semibold)).foregroundStyle(Theme.accent)
                }
            }
            .contentShape(.rect)
        }
        .buttonStyle(.borderless)
        .disabled(store.selecting != nil || spending)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("gpu.row.\(row.gpu)")
    }
}

/// Other datacenters with this GPU, as a menu at the row's trailing edge —
/// a row of its own under every GPU doubled the list's length.
private struct RegionsMenu: View {
    let regions: [GpuRegion]
    let onMigrate: ((String) -> Void)?

    var body: some View {
        Menu {
            ForEach(regions, id: \.datacenter) { region in
                let line = "\(region.datacenter) · stock \(region.stock)"
                    + (region.usdPerHr.map { " · \(Format.usd($0))/h" } ?? "")
                if let onMigrate {
                    // Two Texts in a menu item's label: title plus subtitle.
                    Button { onMigrate(region.datacenter) } label: {
                        Text("Migrate to \(region.datacenter)")
                        Text(line)
                    }
                } else {
                    Text(line)
                }
            }
        } label: {
            Label("\(regions.count)", systemImage: "globe")
                .font(.subheadline)
                .foregroundStyle(Theme.secondary)
                .frame(minWidth: 44, minHeight: 44)
        }
        .accessibilityLabel("Other regions (\(regions.count))")
    }
}
