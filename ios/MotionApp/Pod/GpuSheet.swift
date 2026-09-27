import SwiftUI
import MotionKit

/// One GPU's datacenters, sold-out ones included (`?all=1`), each with its
/// bell (2026-09-27 spec §2). The home row can also arm auto-resume when
/// the run is stuck on a stock-out; other rows offer Migrate and say why
/// renting there is not instant.
struct GpuSheet: View {
    let gpu: String
    let stock: GpuStock
    let subs: GpuSubsStore
    let gpuStore: GpuStore
    let pod: PodStore
    let spending: Bool
    let onMigrate: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    private var row: GpuStockRow? { stock.gpus.first { $0.gpu == gpu } }
    private var selected: Bool { stock.selected == gpu }
    /// The run auto-resume may attach to, or nil.
    private var stuckRunID: String? {
        guard pod.pod?.failedRental?.stockOut == true, pod.pod?.lease == nil else { return nil }
        return pod.pod?.runId
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button {
                        Task { if await gpuStore.select(gpu, whileSpending: spending) { dismiss() } }
                    } label: {
                        HStack {
                            Label(selected ? "Next rental uses this GPU" : "Use for next rental",
                                  systemImage: selected ? "checkmark.circle.fill" : "circle")
                            Spacer()
                            if gpuStore.selecting == gpu { ProgressView() }
                        }
                    }
                    .disabled(selected || spending || gpuStore.selecting != nil)
                    .accessibilityIdentifier("gpu.use")
                } footer: {
                    if spending { Text("A spend request is in flight — the GPU can't change until it's answered.") }
                    else if pod.pod?.lease != nil { Text("A change applies to the next rental.") }
                }
                Section {
                    // `stock.datacenters == nil` means the server hasn't shipped `?all=1`'s
                    // `datacenters` field yet (pre-deploy, 2026-09-27 spec §1) — distinct from
                    // runpodctl genuinely listing nothing, so a UI test (and a user) can tell
                    // "not deployed" apart from "the feature is broken".
                    if stock.datacenters == nil {
                        Text("This server doesn't list datacenters yet — update the bot.")
                            .foregroundStyle(Theme.secondary)
                            .accessibilityIdentifier("gpu.dc.unsupported")
                    } else {
                        let rows = stock.datacenters(for: gpu)
                        if rows.isEmpty {
                            Text("runpodctl lists no datacenter for this GPU right now.")
                                .foregroundStyle(Theme.secondary)
                        }
                        ForEach(rows) { dc in datacenterRow(dc) }
                    }
                } header: {
                    Text("Datacenters")
                } footer: {
                    Text((subs.unsupported ? "" : "🔔 sends one Telegram message the moment it has stock, then clears itself. ")
                         + "Only 📍 is rentable now; elsewhere needs the volume synced first.")
                }
            }
            .navigationTitle(row?.name ?? gpu)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .onDisappear {
                for dc in stock.datacenters(for: gpu) { subs.dismissMessage(gpu: gpu, datacenter: dc.datacenter) }
            }
        }
        .presentationDetents([.medium, .large])
        .accessibilityIdentifier("gpu.sheet")
    }

    private func datacenterRow(_ dc: GpuDatacenter) -> some View {
        let home = dc.datacenter == stock.homeDatacenter
        let sub = subs.sub(gpu: gpu, datacenter: dc.datacenter)
        let busy = subs.inFlight.contains("\(gpu)|\(dc.datacenter)")
        // Each row carries its own refusal (2026-09-27): a sheet-wide line
        // could not say which datacenter's bell or bolt it was about.
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text((home ? "📍 " : "") + dc.datacenter).font(.body)
                    Text("\(dc.stock) · " + (dc.usdPerHr.map { "\(Format.usd($0))/h" } ?? "price ?"))
                        .font(.subheadline.monospacedDigit())
                        .foregroundStyle(dc.soldOut ? Theme.warning : Theme.secondary)
                }
                Spacer()
                if busy { ProgressView() }
                // A bot deployed before /v1/gpu/subs existed 404s the bell and
                // bolt, so neither is offered (2026-09-27); Migrate needs no sub.
                if home, let runID = stuckRunID {
                    if !subs.unsupported { boltButton(dc, sub: sub, runID: runID) }
                } else if !home {
                    // Visible, not a long-press: the old GPU row's globe menu was
                    // the only way to find Migrate, and this replaces it.
                    Button { dismiss(); onMigrate(dc.datacenter) } label: {
                        Image(systemName: "airplane.departure").foregroundStyle(Theme.secondary)
                            .frame(minWidth: 44, minHeight: 44)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel("Migrate to \(dc.datacenter)")
                    .accessibilityIdentifier("gpu.migrate.\(dc.datacenter)")
                }
                if !subs.unsupported { bellButton(dc, sub: sub) }
            }
            // Only the controls: the refusal text below stays readable (not
            // greyed out) while a retry of the same pair is in flight.
            .disabled(busy)
            if let message = subs.message(gpu: gpu, datacenter: dc.datacenter) {
                Text(message).font(.footnote).foregroundStyle(Theme.warning)
                    .accessibilityIdentifier("gpu.dc.message.\(dc.datacenter)")
            }
        }
        .accessibilityIdentifier("gpu.dc.\(dc.datacenter)")
    }

    private func bellButton(_ dc: GpuDatacenter, sub: GpuSub?) -> some View {
        Button {
            Task {
                if let sub { await subs.unwatch(sub) }
                else { await subs.watch(gpu: gpu, datacenter: dc.datacenter, autoResumeRunID: nil) }
            }
        } label: {
            Image(systemName: sub == nil ? "bell" : "bell.fill")
                .foregroundStyle(sub == nil ? Theme.secondary : Theme.accent)
                .frame(minWidth: 44, minHeight: 44)
        }
        .buttonStyle(.borderless)
        .accessibilityLabel(sub == nil ? "Watch \(dc.datacenter)" : "Stop watching \(dc.datacenter)")
        .accessibilityIdentifier("gpu.bell.\(dc.datacenter)")
    }

    /// Arms (or disarms) auto-resume. The quoted ceiling is the price the
    /// server will store — the $/h shown on this row right now.
    private func boltButton(_ dc: GpuDatacenter, sub: GpuSub?, runID: String) -> some View {
        let armed = sub?.autoResume != nil
        return Menu {
            if armed {
                Button("Notify only", systemImage: "bell") {
                    Task { await subs.watch(gpu: gpu, datacenter: dc.datacenter, autoResumeRunID: nil) }
                }
            } else {
                Button {
                    Task { await subs.watch(gpu: gpu, datacenter: dc.datacenter, autoResumeRunID: runID) }
                } label: {
                    Text("Notify + auto-resume the stuck run")
                    Text("Rents automatically at ≤ " + (dc.usdPerHr.map { "\(Format.usd($0))/h" } ?? "today's price"))
                }
            }
        } label: {
            Image(systemName: armed ? "bolt.fill" : "bolt")
                .foregroundStyle(armed ? Theme.accent : Theme.secondary)
                .frame(minWidth: 44, minHeight: 44)
        }
        .accessibilityLabel(armed ? "Auto-resume armed" : "Arm auto-resume")
        .accessibilityIdentifier("gpu.bolt.\(dc.datacenter)")
    }
}
