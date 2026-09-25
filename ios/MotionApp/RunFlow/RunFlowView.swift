import MotionKit
import SwiftUI

struct RunFlowView: View {
    let flow: RunFlow
    let entry: RunFlow.Entry
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @State private var didStart = false

    var body: some View {
        List {
            if flow.error != nil || flow.message != nil || flow.needsRecheck {
                Section {
                    if let error = flow.error { ErrorBanner(error: error) { await flow.start(entry) } }
                    if let message = flow.message { MessageCard(text: message) { flow.dismissMessage() } }
                    if flow.needsRecheck {
                        Button("Check again") { Task { await flow.recheck() } }
                            .disabled(flow.isSpending)
                    }
                }
            }
            content
        }
        .navigationTitle("Run")
        .navigationBarTitleDisplayMode(.inline)
        // A shared RunFlow survives navigation (Runs/NewJob both hold the same
        // instance), so re-running `start` on every appear — e.g. popping back
        // from RunDetailView after `.started` — would reset a phase that just
        // finished, drop `.choiceRequired`, or race a spend in flight. `.task`
        // itself restarts on every appear regardless of view identity, so the
        // guard has to live here, not in the task's cancellation. A genuinely
        // new navigation (new `RunFlowView` value) gets a fresh `didStart`.
        .refreshable {
            switch flow.phase {
            case .rentPanel, .choiceRequired:
                await flow.loadPanel(force: true)
            default:
                await flow.refreshTryon()
            }
        }
        .task {
            guard !didStart else { return }
            didStart = true
            await flow.start(entry)
        }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await flow.pollTryon()
        }
    }

    @ViewBuilder private var content: some View {
        switch flow.phase {
        case .loading:
            if flow.error == nil {
                ProgressView().frame(maxWidth: .infinity).listRowBackground(Color.clear)
            }
        case .compose:
            compose
        case .phaseARunning:
            Section("Try-on on the VPS") {
                ForEach(flow.tryon?.previews ?? []) { preview in
                    HStack(spacing: 12) {
                        StageDot(status: preview.status)
                        Text(preview.run).font(.subheadline).lineLimit(1).truncationMode(.middle)
                    }
                }
                if flow.tryon?.previews.isEmpty ?? true {
                    HStack(spacing: 10) { ProgressView(); Text("Starting…").foregroundStyle(Theme.secondary) }
                }
            }
        case .previews:
            // One card per look (Phase 6 shared try-on): `flow.cards` is the
            // leaders only, so a group's followers render inside their
            // leader's card via the "Used by K videos" label, not as their
            // own cards.
            ForEach(flow.cards) { preview in
                TryonPreviewCard(flow: flow, preview: preview)
            }
            Section {
                Button("Continue to rent") { Task { await flow.continueToRent() } }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(flow.isSpending)
                    .buttonRow()
            }
        case .rentPanel:
            RentPanelView(flow: flow)
        case let .choiceRequired(_, provider):
            let price = flow.quote(for: provider)
            Section {
                Text("These inputs already have a try-on. Reusing it spends no Gemini/Qwen quota; re-running pays for it again.")
                    .font(.subheadline)
                if let price {
                    let providerName = provider == .runpod ? (flow.panel?.runpod.gpu ?? "RunPod") : "Vast"
                    Text("~\(Format.usd(price)) quote on \(providerName)")
                        .font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                } else {
                    Text("No price yet — pull to refresh the rent panel.")
                        .font(.subheadline).foregroundStyle(Theme.warning)
                }
            } header: {
                Text("Try-on already ran")
            }
            // Both buttons rent a pod (a `confirm` with reuse/rerun try-on) —
            // the label and the quote say so, so a tap here is never a
            // surprise spend of a different kind than Confirm on the rent panel.
            Section {
                Button(price.map { "Reuse try-on & rent · ~\(Format.usd($0))" } ?? "Reuse try-on & rent") {
                    Task { await flow.choose(.reuse) }
                }
                .buttonStyle(PrimaryButtonStyle()).disabled(!flow.canSpend || price == nil)
                .buttonRow()
                Button(price.map { "Re-run try-on & rent · ~\(Format.usd($0))" } ?? "Re-run try-on & rent") {
                    Task { await flow.choose(.rerun) }
                }
                .buttonStyle(SecondaryButtonStyle()).disabled(!flow.canSpend || price == nil)
                .buttonRow()
            }
        case let .started(runID):
            Section {
                Label("Started — progress is on the run screen and in Telegram.", systemImage: "checkmark.circle.fill")
                    .font(.headline)
                if let client = model.client, let pod = model.pod {
                    NavigationLink("Open \(runID)") {
                        RunDetailView(store: RunDetailStore(client: client, runID: runID), flow: flow, pod: pod)
                    }
                }
            }
        case .outcomeUnknown:
            Section {
                Label("Couldn't tell whether this went through. The Pod tab shows the pod.", systemImage: "questionmark.circle")
                    .font(.headline).foregroundStyle(Theme.warning)
            }
        }
    }

    @Environment(AppModel.self) private var model

    @ViewBuilder private var compose: some View {
        let jobs = flow.draft?.jobs ?? 0
        Section("Next step") {
            Text("\(jobs) job\(jobs == 1 ? "" : "s") in the draft.")
        }
        Section {
            if flow.hasLocalTryon {
                Button("Preview try-on") { Task { await flow.startPhaseA() } }
                    .buttonStyle(PrimaryButtonStyle())
                    .accessibilityIdentifier("runflow.previewTryon")
                    .disabled(!flow.canSpend)
                    .buttonRow()
            }
            Button("Rent without preview") { Task { await flow.continueToRent() } }
                .buttonStyle(flow.hasLocalTryon ? AnyButtonStyle(SecondaryButtonStyle()) : AnyButtonStyle(PrimaryButtonStyle()))
                .accessibilityIdentifier("runflow.rentWithoutPreview")
                .disabled(flow.isSpending)
                .buttonRow()
            if !(flow.tryon?.previews.isEmpty ?? true) {
                Button("View last previews") { Task { await flow.start(.existing) } }
                    .buttonStyle(SecondaryButtonStyle())
                    .buttonRow()
            }
        } footer: {
            if flow.hasLocalTryon { Text("Preview spends Gemini/Qwen quota — no pod is rented.") }
        }
    }
}
