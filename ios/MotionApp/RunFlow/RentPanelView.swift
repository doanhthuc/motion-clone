import MotionKit
import SwiftUI

struct RentPanelView: View {
    let flow: RunFlow
    @Environment(AppModel.self) private var model
    @State private var changingGpu = false

    var body: some View {
        Group {
            if let panel = flow.panel {
                // One card per Section: the List clips a Section to its own
                // (larger) corner radius, which cut the corners of hand-drawn cards.
                Section {
                    runpodCard(panel.runpod)
                } header: {
                    header(panel)
                }
                Section { vastCard(panel.vast) }
                Section {
                    Button { changingGpu = true } label: {
                        HStack {
                            Text("Change GPU").foregroundStyle(Theme.label)
                            Spacer()
                            Text(Self.shortGpu(panel.runpod.gpu)).foregroundStyle(Theme.secondary)
                            Image(systemName: "chevron.right")
                                .font(.footnote.weight(.semibold)).foregroundStyle(Theme.tertiary)
                        }
                        .contentShape(.rect)
                    }
                    .disabled(flow.isSpending)
                    .accessibilityIdentifier("runflow.changeGpu")
                    // On the trigger row, not the Group: a modifier on a
                    // Group applies to every child, so it would present once per section.
                    .sheet(isPresented: $changingGpu) { gpuSheet }
                    // Migrating moves the Network Volume; a Community pod mounts none.
                    if panel.runpod.soldOut && !panel.runpod.isCommunity {
                        Button("Migrate the volume…") {
                            model.selectedTab = .pod
                            model.migrateSheet = MigrateRequest(destination: nil)
                        }
                        .accessibilityIdentifier("runflow.migrate")
                    }
                }
            } else if flow.isLoadingPanel {
                LoadingBlock(title: "Reading stock and prices…")
                    .listRowBackground(Color.clear)
            }
        }
    }

    /// The job summary and a Refresh that re-reads stock past the bot's cache —
    /// the same read as pull-to-refresh, which nobody finds. Community 5090
    /// stock flips Low/None every ~10 s (measured 2026-10-03), so a visible
    /// re-check is the main thing this screen is for when RunPod reads sold out.
    private func header(_ panel: RentPanel) -> some View {
        HStack(alignment: .center) {
            Text("\(panel.jobs) job\(panel.jobs == 1 ? "" : "s") · ~\(Int(panel.estimateMin)) min"
                 + (panel.afterPhaseA ? " · try-on done" : ""))
                .monospacedDigit()
            Spacer()
            Button {
                Task { await flow.loadPanel(force: true) }
            } label: {
                ZStack {
                    // Both layers always laid out, so the header doesn't jump.
                    Label("Refresh", systemImage: "arrow.clockwise").opacity(flow.isLoadingPanel ? 0 : 1)
                    if flow.isLoadingPanel { ProgressView().controlSize(.small) }
                }
                .font(.subheadline.weight(.semibold))
                .frame(minHeight: 44)
                .contentShape(.rect)
            }
            .buttonStyle(.borderless)
            .textCase(nil)
            .disabled(flow.isLoadingPanel || flow.isSpending)
            .accessibilityIdentifier("runflow.refreshPanel")
        }
    }

    @ViewBuilder private var gpuSheet: some View {
        if let gpu = model.gpu {
            NavigationStack {
                List {
                    GpuPickerView(store: gpu, spending: flow.isSpending, hasLease: false,
                                  onSelected: {
                                      changingGpu = false
                                      await flow.reloadPanelAfterGpuChange()
                                  })
                }
                .navigationTitle("Change GPU")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { changingGpu = false } }
                }
                .task { if gpu.stock == nil { await gpu.load() } }
            }
        }
    }

    /// "NVIDIA GeForce RTX 5090" → "RTX 5090": the vendor prefix pushed the
    /// price off the line on a phone.
    static func shortGpu(_ name: String) -> String {
        var s = name
        for prefix in ["NVIDIA ", "GeForce "] where s.hasPrefix(prefix) { s.removeFirst(prefix.count) }
        return s
    }

    private func runpodCard(_ row: RentPanelRunpod) -> some View {
        let title = row.isCommunity ? "RunPod Community" : "RunPod"
        let place = row.isCommunity ? nil : row.datacenter
        let blocker: String? = !row.soldOut ? nil
            : row.isCommunity ? "No Community host passes the filters right now — tap Refresh"
            : "Sold out at \(row.datacenter ?? "the home datacenter")"
        return providerCard(provider: .runpod, title: title,
                            detail: [Self.shortGpu(row.gpu), place,
                                     row.usdPerHr.map { "\(Format.usd($0))/h" }].compactMap { $0 },
                            stock: row.soldOut ? nil : row.stock,
                            blockers: blocker.map { [$0] } ?? [])
    }

    private func vastCard(_ row: RentPanelVast) -> some View {
        providerCard(provider: .vast, title: "Vast",
                     detail: [row.usdPerHr.map { "\(Format.usd($0))/h" }].compactMap { $0 },
                     stock: nil,
                     blockers: row.canSpend ? [] : row.blockers)
    }

    private func providerCard(provider: SpendProvider, title: String, detail: [String],
                              stock: String?, blockers: [String]) -> some View {
        let quote = flow.quote(for: provider)
        let enabled = quote != nil
        let selected = flow.selectedProvider == provider && enabled
        return Button {
            flow.selectedProvider = provider
        } label: {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: selected ? "largecircle.fill.circle" : "circle")
                    .font(.title3)
                    .foregroundStyle(selected ? Theme.accent : Theme.tertiary)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(title).font(.headline)
                            .foregroundStyle(enabled ? Theme.label : Theme.secondary)
                        Spacer(minLength: 8)
                        if let quote {
                            Text("~\(Format.usd(quote))").font(.title3.weight(.semibold).monospacedDigit())
                                .foregroundStyle(Theme.label)
                        }
                    }
                    HStack(spacing: 8) {
                        if !detail.isEmpty {
                            Text(detail.joined(separator: " · "))
                                .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                        }
                        if let stock { StockPill(stock: stock) }
                    }
                    ForEach(blockers, id: \.self) { b in
                        Label(b, systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote).foregroundStyle(Theme.warning)
                    }
                }
            }
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .disabled(!enabled || flow.isSpending)
        // A tint, not a stroke: the Section clips its rows to a corner radius
        // no shape here could match, and a stroke lost its corners to it.
        .listRowBackground(Theme.surface.overlay(Theme.accent.opacity(selected ? 0.14 : 0)))
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("runflow.provider.\(provider == .runpod ? "runpod" : "vast")")
    }
}

/// RunPod's stock word ("High"/"Medium"/"Low") as a small dot + label. Low is
/// amber: it is what the 5090 reads just before it flips to none.
private struct StockPill: View {
    let stock: String

    var body: some View {
        let low = stock.lowercased() == "low"
        HStack(spacing: 4) {
            Circle().frame(width: 6, height: 6)
            Text(stock)
        }
        .font(.caption.weight(.semibold))
        .foregroundStyle(low ? Theme.warning : Theme.secondary)
        .padding(.horizontal, 8).padding(.vertical, 3)
        .background(Theme.surfaceRaised, in: .capsule)
        .accessibilityLabel("Stock \(stock)")
    }
}

/// Confirm and what gates it, in the run flow's bottom bar.
struct RentConfirmBar: View {
    let flow: RunFlow

    var body: some View {
        if flow.isSpending {
            HStack(spacing: 10) {
                ProgressView()
                Text(flow.inFlightLabel ?? "").font(.subheadline).foregroundStyle(Theme.secondary)
            }
        }
        if let price = flow.quote(for: flow.selectedProvider) {
            Text("A quote from the estimate, not the invoice.")
                .font(.footnote).foregroundStyle(Theme.secondary)
                .accessibilityIdentifier("runflow.quote")
            // Shown but disabled while any spend is unanswered, so the
            // price stays visible next to "Check again".
            Button {
                Task { await flow.confirm() }
            } label: {
                Text("Confirm · \(flow.selectedProvider == .runpod ? "RunPod" : "Vast") · ~\(Format.usd(price)) quote")
                    .monospacedDigit()
            }
            .buttonStyle(PrimaryButtonStyle())
            .disabled(!flow.canConfirm(flow.selectedProvider))
            .accessibilityIdentifier("runflow.confirm")
        } else if flow.quote(for: .runpod) == nil && flow.quote(for: .vast) == nil {
            Label("Nothing can be rented right now.", systemImage: "xmark.octagon")
                .font(.headline).foregroundStyle(Theme.danger)
                .accessibilityIdentifier("runflow.soldOut")
        }
    }
}
