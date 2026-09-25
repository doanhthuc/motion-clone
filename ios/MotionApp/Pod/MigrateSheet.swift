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
            List {
                content(typed: $flow.typed)
                Section {
                    if flow.needsRecheck {
                        Button("Check again") { Task { await flow.recheck() } }
                            .disabled(flow.isSending)
                    }
                    if flow.isSending {
                        HStack(spacing: 8) {
                            ProgressView()
                            VStack(alignment: .leading, spacing: 2) {
                                Text(flow.inFlightLabel ?? "").font(.footnote.monospacedDigit()).foregroundStyle(Theme.secondary)
                                if let note = flow.retryNote {
                                    Text(note).font(.footnote.monospacedDigit()).foregroundStyle(Theme.warning)
                                }
                            }
                        }
                    }
                    if let message = flow.message {
                        Text(message).font(.subheadline).foregroundStyle(Theme.warning)
                    }
                }
            }
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
            ProgressView("Checking \(dc)…").frame(maxWidth: .infinity).listRowBackground(Color.clear)
        case .confirm(let ask):
            confirmation(ask, typed: typed)
        case .started(let dc):
            Section {
                Label("Migration to \(dc) started", systemImage: "checkmark.circle").font(.headline)
            } footer: {
                Text("Progress shows on the Pod tab and in Telegram.")
            }
            Section { Button("Done") { dismiss() }.buttonStyle(PrimaryButtonStyle()).buttonRow() }
        case .outcomeUnknown:
            Section { Button("Done") { dismiss() }.buttonStyle(SecondaryButtonStyle()).buttonRow() }
        }
    }

    @ViewBuilder private var chooser: some View {
        let blocker = MigrateFlow.blocker(pod: pod.pod, runStatus: runStatus)
        Section {
            Text("Copies the Network Volume (models, Postgres, MinIO) to another datacenter, then deletes the current one once the copy verifies. About 25–30 minutes, on two temporary CPU pods.")
                .font(.subheadline).foregroundStyle(Theme.secondary)
            if let blocker {
                Label(blocker, systemImage: "exclamationmark.triangle.fill")
                    .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.danger)
                    .accessibilityIdentifier("migrate.blocked")
            }
        }
        Section("Destination") {
            if let stock = gpu.stock {
                let destinations = stock.destinations
                if destinations.isEmpty {
                    Text("No other datacenter has a GPU in stock right now.")
                        .font(.subheadline).foregroundStyle(Theme.secondary)
                }
                ForEach(destinations) { destination in
                    Button { Task { await flow.ask(toDc: destination.datacenter) } } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(destination.datacenter).font(.body).foregroundStyle(Theme.label)
                                Text(destination.gpus.joined(separator: " · "))
                                    .font(.subheadline).foregroundStyle(Theme.secondary)
                            }
                            Spacer()
                            if destination.datacenter == flow.destination {
                                Image(systemName: "checkmark").font(.body.weight(.semibold))
                                    .foregroundStyle(Theme.accent)
                            }
                        }
                        .contentShape(.rect)
                    }
                    .disabled(blocker != nil || flow.isSending || flow.needsRecheck || flow.pendingNotice != nil)
                    .accessibilityIdentifier("migrate.dest.\(destination.datacenter)")
                }
            } else if let error = gpu.error {
                ErrorBanner(error: error) { await gpu.load() }
            } else {
                ProgressView("Reading stock…").frame(maxWidth: .infinity)
            }
        }
    }

    // Section-level TimelineViews rather than one around the whole step: a
    // TimelineView is not a transparent List container, so wrapping Sections
    // in one would collapse them into a single row.
    @ViewBuilder
    private func confirmation(_ ask: MigrateAsk, typed: Binding<String>) -> some View {
        Section {
            Text("\(ask.homeDatacenter) → \(ask.toDc)").font(.title3.weight(.semibold))
            Text(ask.warning).font(.subheadline).foregroundStyle(Theme.danger)
                .accessibilityIdentifier("migrate.warning")
        }
        Section {
            TextField("Type \(ask.toDc) to confirm", text: typed)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .font(.body.monospaced())
                .accessibilityIdentifier("migrate.typed")
        } footer: {
            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                let left = flow.secondsLeft(at: ctx.date)
                Text(left > 0 ? "Confirmation valid for \(Format.clock(Double(left)))"
                              : "This confirmation expired.")
                    .monospacedDigit()
                    .foregroundStyle(left > 0 ? Theme.secondary : Theme.warning)
            }
        }
        Section {
            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                VStack(spacing: 8) {
                    Button("Migrate and delete the old volume") { Task { await flow.migrate() } }
                        .buttonStyle(DestructiveButtonStyle())
                        .disabled(!flow.canMigrate(at: ctx.date) || spendBlocked)
                        .accessibilityIdentifier("migrate.confirm")
                    if flow.secondsLeft(at: ctx.date) == 0 {
                        Button("Expired — ask again") { Task { await flow.ask(toDc: ask.toDc) } }
                            .buttonStyle(SecondaryButtonStyle())
                    }
                }
            }
            .buttonRow()
        } footer: {
            VStack(alignment: .leading, spacing: 4) {
                if spendBlocked {
                    Text("Another spend request is unanswered — resolve it before migrating.")
                }
                if flow.dropBlocked {
                    Text("A batch drop is still in flight — wait for it before moving the volume.")
                }
            }
            .foregroundStyle(Theme.warning)
        }
    }
}
