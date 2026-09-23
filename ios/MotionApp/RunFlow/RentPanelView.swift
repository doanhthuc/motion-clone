import MotionKit
import SwiftUI

struct RentPanelView: View {
    let flow: RunFlow
    @Environment(AppModel.self) private var model
    @State private var changingGpu = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionLabel(text: "Rent GPU")
            if let panel = flow.panel {
                Text("\(panel.jobs) job\(panel.jobs == 1 ? "" : "s") · about \(Int(panel.estimateMin)) min"
                     + (panel.afterPhaseA ? " · try-on already done" : ""))
                    .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                runpodRow(panel.runpod)
                vastRow(panel.vast)
                HStack(spacing: 10) {
                    Button("Change GPU") { changingGpu = true }
                        .buttonStyle(SecondaryButtonStyle())
                        .disabled(flow.isSpending)
                        .accessibilityIdentifier("runflow.changeGpu")
                    if panel.runpod.soldOut {
                        Button("Migrate →") {
                            model.selectedTab = .pod
                            model.migrateSheet = MigrateRequest(destination: nil)
                        }
                        .buttonStyle(SecondaryButtonStyle())
                        .accessibilityIdentifier("runflow.migrate")
                    }
                }
                if let price = flow.quote(for: flow.selectedProvider) {
                    // Shown but disabled while any spend is unanswered, so the
                    // price stays visible next to "Check again".
                    Button {
                        Task { await flow.confirm() }
                    } label: {
                        Text("Confirm · ~\(Format.usd(price)) quote")
                    }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(!flow.canConfirm(flow.selectedProvider))
                    .accessibilityIdentifier("runflow.confirm")
                    Text("A quote from the estimate, not the invoice.")
                        .font(Theme.mono(10)).foregroundStyle(Theme.ink3)
                        .accessibilityIdentifier("runflow.quote")
                } else if flow.quote(for: .runpod) == nil && flow.quote(for: .vast) == nil {
                    Label("Nothing can be rented right now.", systemImage: "xmark.octagon")
                        .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.red)
                        .accessibilityIdentifier("runflow.soldOut")
                }
                if flow.isSpending {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text(flow.inFlightLabel ?? "").font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                    }
                }
            } else if flow.isLoadingPanel {
                ProgressView("Reading stock and prices…").frame(maxWidth: .infinity).padding(.top, 30)
            }
        }
        .sheet(isPresented: $changingGpu) {
            if let gpu = model.gpu {
                NavigationStack {
                    ScrollView {
                        GpuPickerView(store: gpu, spending: flow.isSpending, hasLease: false,
                                      onSelected: {
                                          changingGpu = false
                                          await flow.reloadPanelAfterGpuChange()
                                      })
                        .padding(20)
                    }
                    .background(Theme.bg)
                    .navigationTitle("Change GPU")
                    .navigationBarTitleDisplayMode(.inline)
                    .task { if gpu.stock == nil { await gpu.load() } }
                }
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
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Image(systemName: selected && enabled ? "largecircle.fill.circle" : "circle")
                        .foregroundStyle(enabled ? Theme.lime : Theme.ink3)
                    Text(title).font(Theme.sans(15, .semibold)).foregroundStyle(enabled ? Theme.ink : Theme.ink3)
                    Spacer()
                }
                if !detail.isEmpty { Text(detail).font(Theme.mono(11)).foregroundStyle(Theme.ink2) }
                ForEach(blockers, id: \.self) { b in
                    Text(b).font(Theme.sans(12)).foregroundStyle(Theme.amber)
                }
            }
            .padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .card(border: selected && enabled ? Theme.limeLine : Theme.line)
        }
        .buttonStyle(.plain)
        .disabled(!enabled || flow.isSpending)
    }
}
