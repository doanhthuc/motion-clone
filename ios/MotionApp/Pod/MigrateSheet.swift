import SwiftUI
import MotionKit

/// ask → typed confirmation → migrate (Phase 5 design §6). The confirm button
/// enables only on an exact match with `to_dc`, before the token expires.
struct MigrateSheet: View {
    let request: MigrateRequest
    let flow: MigrateFlow
    let gpu: GpuStore
    let pod: PodStore
    let runStatus: RunStatus?
    /// `!RunFlow.canSpend`: a Phase 4 spend is unanswered or replaying, and the
    /// gate would refuse the migrate (design §6).
    let spendBlocked: Bool
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        @Bindable var flow = flow
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    content(typed: $flow.typed)
                    if flow.needsRecheck {
                        Button("Check again") { Task { await flow.recheck() } }
                            .buttonStyle(SecondaryButtonStyle())
                            .disabled(flow.isSending)
                    }
                    if flow.isSending {
                        HStack(spacing: 8) {
                            ProgressView()
                            VStack(alignment: .leading, spacing: 2) {
                                Text(flow.inFlightLabel ?? "").font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                                if let note = flow.retryNote {
                                    Text(note).font(Theme.mono(11)).foregroundStyle(Theme.amber)
                                }
                            }
                        }
                    }
                    if let message = flow.message {
                        Text(message).font(Theme.sans(13)).foregroundStyle(Theme.amber)
                    }
                }
                .padding(20)
            }
            .background(Theme.bg)
            .navigationTitle("Move volume")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }.disabled(flow.isSending)
                }
            }
            .task {
                flow.begin(preselect: request.destination)
                if gpu.stock == nil { await gpu.load() }
            }
        }
        .interactiveDismissDisabled(flow.isSending)
    }

    @ViewBuilder private func content(typed: Binding<String>) -> some View {
        switch flow.step {
        case .choose:
            chooser
        case .asking(let dc):
            ProgressView("Checking \(dc)…").frame(maxWidth: .infinity).padding(.top, 30)
        case .confirm(let ask):
            confirmation(ask, typed: typed)
        case .started(let dc):
            Label("Migration to \(dc) started", systemImage: "checkmark.circle")
                .font(Theme.sans(15, .semibold)).foregroundStyle(Theme.lime)
            Text("Progress shows on the Pod tab and in Telegram.")
                .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
            Button("Done") { dismiss() }.buttonStyle(PrimaryButtonStyle())
        case .outcomeUnknown:
            Button("Done") { dismiss() }.buttonStyle(SecondaryButtonStyle())
        }
    }

    @ViewBuilder private var chooser: some View {
        let blocker = MigrateFlow.blocker(pod: pod.pod, runStatus: runStatus)
        Text("Copies the Network Volume (models, Postgres, MinIO) to another datacenter, then deletes the current one once the copy verifies. About 25–30 minutes, on two temporary CPU pods.")
            .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
        if let blocker {
            Text(blocker).font(Theme.sans(13, .semibold)).foregroundStyle(Theme.red)
                .accessibilityIdentifier("migrate.blocked")
        }
        if let stock = gpu.stock {
            let destinations = stock.destinations
            if destinations.isEmpty {
                Text("No other datacenter has a GPU in stock right now.")
                    .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
            }
            ForEach(destinations) { destination in
                Button { Task { await flow.ask(toDc: destination.datacenter) } } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(destination.datacenter).font(Theme.mono(15, .semibold)).foregroundStyle(Theme.ink)
                        Text(destination.gpus.joined(separator: " · "))
                            .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                    }
                    .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                    .card(border: destination.datacenter == flow.destination ? Theme.limeLine : Theme.line)
                }
                .buttonStyle(.plain)
                .disabled(blocker != nil || flow.isSending || flow.needsRecheck || flow.pendingNotice != nil)
                .accessibilityIdentifier("migrate.dest.\(destination.datacenter)")
            }
        } else if let error = gpu.error {
            ErrorBanner(error: error) { await gpu.load() }
        } else {
            ProgressView("Reading stock…").frame(maxWidth: .infinity)
        }
    }

    private func confirmation(_ ask: MigrateAsk, typed: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("\(ask.homeDatacenter) → \(ask.toDc)").font(Theme.mono(18, .semibold)).foregroundStyle(Theme.ink)
            Text(ask.warning).font(Theme.sans(14)).foregroundStyle(Theme.red)
                .accessibilityIdentifier("migrate.warning")
            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                let left = flow.secondsLeft(at: ctx.date)
                VStack(alignment: .leading, spacing: 12) {
                    Text(left > 0 ? "Confirmation valid for \(Format.clock(Double(left)))"
                                  : "This confirmation expired.")
                        .font(Theme.mono(11)).foregroundStyle(left > 0 ? Theme.ink2 : Theme.amber)
                    TextField("Type \(ask.toDc) to confirm", text: typed)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(Theme.mono(15)).padding(12).card()
                        .accessibilityIdentifier("migrate.typed")
                    Button("Migrate and delete the old volume") { Task { await flow.migrate() } }
                        .buttonStyle(DestructiveButtonStyle())
                        .disabled(!flow.canMigrate(at: ctx.date) || spendBlocked)
                        .accessibilityIdentifier("migrate.confirm")
                    if spendBlocked {
                        Text("Another spend request is unanswered — resolve it before migrating.")
                            .font(Theme.sans(12)).foregroundStyle(Theme.amber)
                    }
                    if left == 0 {
                        Button("Expired — ask again") { Task { await flow.ask(toDc: ask.toDc) } }
                            .buttonStyle(SecondaryButtonStyle())
                    }
                }
            }
        }
    }
}
