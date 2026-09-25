import MotionKit
import SwiftUI

struct RentPanelView: View {
    let flow: RunFlow
    @Environment(AppModel.self) private var model
    @State private var changingGpu = false

    var body: some View {
        Group {
            if let panel = flow.panel {
                Section {
                    runpodRow(panel.runpod)
                    vastRow(panel.vast)
                } header: {
                    Text("Rent GPU")
                } footer: {
                    Text("\(panel.jobs) job\(panel.jobs == 1 ? "" : "s") · about \(Int(panel.estimateMin)) min"
                         + (panel.afterPhaseA ? " · try-on already done" : ""))
                        .monospacedDigit()
                }
                Section {
                    Button("Change GPU") { changingGpu = true }
                        .disabled(flow.isSpending)
                        .accessibilityIdentifier("runflow.changeGpu")
                        // On the trigger row, not the Group: a modifier on a
                        // Group applies to every child, so it would present once per section.
                        .sheet(isPresented: $changingGpu) { gpuSheet }
                    if panel.runpod.soldOut {
                        Button("Migrate the volume…") {
                            model.selectedTab = .pod
                            model.migrateSheet = MigrateRequest(destination: nil)
                        }
                        .accessibilityIdentifier("runflow.migrate")
                    }
                }
                Section {
                    if let price = flow.quote(for: flow.selectedProvider) {
                        // Shown but disabled while any spend is unanswered, so the
                        // price stays visible next to "Check again".
                        Button {
                            Task { await flow.confirm() }
                        } label: {
                            Text("Confirm · ~\(Format.usd(price)) quote").monospacedDigit()
                        }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(!flow.canConfirm(flow.selectedProvider))
                        .accessibilityIdentifier("runflow.confirm")
                        .buttonRow()
                    } else if flow.quote(for: .runpod) == nil && flow.quote(for: .vast) == nil {
                        Label("Nothing can be rented right now.", systemImage: "xmark.octagon")
                            .font(.headline).foregroundStyle(Theme.danger)
                            .accessibilityIdentifier("runflow.soldOut")
                    }
                    if flow.isSpending {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text(flow.inFlightLabel ?? "").font(.subheadline).foregroundStyle(Theme.secondary)
                        }
                    }
                } footer: {
                    if flow.quote(for: flow.selectedProvider) != nil {
                        Text("A quote from the estimate, not the invoice.")
                            .accessibilityIdentifier("runflow.quote")
                    }
                }
            } else if flow.isLoadingPanel {
                LoadingBlock(title: "Reading stock and prices…")
                    .listRowBackground(Color.clear)
            }
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

    private func runpodRow(_ row: RentPanelRunpod) -> some View {
        providerRow(provider: .runpod, title: row.gpu,
                    detail: [row.datacenter, row.stock.map { "stock \($0)" },
                             row.usdPerHr.map { "\(Format.usd($0))/h" }].compactMap { $0 }.joined(separator: " · "),
                    blockers: row.soldOut ? ["Sold out at \(row.datacenter ?? "the home datacenter")"] : [])
    }

    private func vastRow(_ row: RentPanelVast) -> some View {
        providerRow(provider: .vast, title: "Vast",
                    detail: [row.usdPerHr.map { "\(Format.usd($0))/h" },
                             row.sessionUsd.map { "session ~\(Format.usd($0))" }].compactMap { $0 }.joined(separator: " · "),
                    blockers: row.canSpend ? [] : row.blockers)
    }

    private func providerRow(provider: SpendProvider, title: String, detail: String, blockers: [String]) -> some View {
        let enabled = flow.quote(for: provider) != nil
        let selected = flow.selectedProvider == provider
        return Button {
            flow.selectedProvider = provider
        } label: {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.body).foregroundStyle(enabled ? Theme.label : Theme.tertiary)
                    if !detail.isEmpty {
                        Text(detail).font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                    }
                    ForEach(blockers, id: \.self) { b in
                        Text(b).font(.footnote).foregroundStyle(Theme.warning)
                    }
                }
                Spacer(minLength: 0)
                if selected && enabled {
                    Image(systemName: "checkmark").font(.body.weight(.semibold)).foregroundStyle(Theme.accent)
                }
            }
            .contentShape(.rect)
        }
        .disabled(!enabled || flow.isSpending)
        .accessibilityAddTraits(selected && enabled ? .isSelected : [])
    }
}
