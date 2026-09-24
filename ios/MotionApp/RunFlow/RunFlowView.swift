import MotionKit
import SwiftUI

struct RunFlowView: View {
    let flow: RunFlow
    let entry: RunFlow.Entry
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @State private var didStart = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let error = flow.error { ErrorBanner(error: error) { await flow.start(entry) } }
                if let message = flow.message { MessageCard(text: message) { flow.dismissMessage() } }
                if flow.needsRecheck {
                    Button("Check again") { Task { await flow.recheck() } }
                        .buttonStyle(SecondaryButtonStyle())
                        .disabled(flow.isSpending)
                }
                content
            }
            .padding(.horizontal, 20).padding(.top, 4)
        }
        .background(Theme.bg)
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
                ProgressView().frame(maxWidth: .infinity).padding(.top, 60)
            }
        case .compose:
            compose
        case .phaseARunning:
            SectionLabel(text: "Try-on on the VPS")
            ForEach(flow.tryon?.previews ?? []) { preview in
                HStack {
                    StageDot(status: preview.status)
                    Text(preview.run).font(Theme.mono(12)).foregroundStyle(Theme.ink1)
                }
            }
            if flow.tryon?.previews.isEmpty ?? true {
                HStack(spacing: 8) { PulseDot(); Text("Starting…").font(Theme.sans(13)).foregroundStyle(Theme.ink2) }
            }
        case .previews:
            SectionLabel(text: "Try-on previews")
            // One card per look (Phase 6 shared try-on): `flow.cards` is the
            // leaders only, so a group's followers render inside their
            // leader's card via the "Used by K videos" label, not as their
            // own cards.
            ForEach(flow.cards) { preview in
                TryonPreviewCard(flow: flow, preview: preview)
            }
            Button("Continue to rent") { Task { await flow.continueToRent() } }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(flow.isSpending)
        case .rentPanel:
            RentPanelView(flow: flow)
        case let .choiceRequired(_, provider):
            SectionLabel(text: "Try-on already ran")
            Text("These inputs already have a try-on. Reusing it spends no Gemini/Qwen quota; re-running pays for it again.")
                .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
            let price = flow.quote(for: provider)
            if let price {
                let providerName = provider == .runpod ? (flow.panel?.runpod.gpu ?? "RunPod") : "Vast"
                Text("~\(Format.usd(price)) quote on \(providerName)")
                    .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
            } else {
                Text("No price yet — pull to refresh the rent panel.")
                    .font(Theme.sans(13)).foregroundStyle(Theme.amber)
            }
            // Both buttons rent a pod (a `confirm` with reuse/rerun try-on) —
            // the label and the quote say so, so a tap here is never a
            // surprise spend of a different kind than Confirm on the rent panel.
            Button(price.map { "Reuse try-on & rent · ~\(Format.usd($0))" } ?? "Reuse try-on & rent") {
                Task { await flow.choose(.reuse) }
            }
            .buttonStyle(PrimaryButtonStyle()).disabled(!flow.canSpend || price == nil)
            Button(price.map { "Re-run try-on & rent · ~\(Format.usd($0))" } ?? "Re-run try-on & rent") {
                Task { await flow.choose(.rerun) }
            }
            .buttonStyle(SecondaryButtonStyle()).disabled(!flow.canSpend || price == nil)
        case let .started(runID):
            Label("Started — progress is on the run screen and in Telegram.", systemImage: "checkmark.circle.fill")
                .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.lime)
            if let client = model.client, let pod = model.pod {
                NavigationLink("Open \(runID)") {
                    RunDetailView(store: RunDetailStore(client: client, runID: runID), flow: flow, pod: pod)
                }
                .buttonStyle(SecondaryButtonStyle())
            }
        case .outcomeUnknown:
            Label("Couldn't tell whether this went through. The Pod tab shows the pod.", systemImage: "questionmark.circle")
                .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.amber)
        }
    }

    @Environment(AppModel.self) private var model

    @ViewBuilder private var compose: some View {
        SectionLabel(text: "Next step")
        let jobs = flow.draft?.jobs ?? 0
        Text("\(jobs) job\(jobs == 1 ? "" : "s") in the draft.")
            .font(Theme.sans(14)).foregroundStyle(Theme.ink1)
        if flow.hasLocalTryon {
            Button("Preview try-on") { Task { await flow.startPhaseA() } }
                .buttonStyle(PrimaryButtonStyle())
                .accessibilityIdentifier("runflow.previewTryon")
                .disabled(!flow.canSpend)
            Text("Spends Gemini/Qwen quota — no pod is rented.")
                .font(Theme.mono(11)).foregroundStyle(Theme.ink3)
        }
        Button("Rent without preview") { Task { await flow.continueToRent() } }
            .buttonStyle(flow.hasLocalTryon ? AnyButtonStyle(SecondaryButtonStyle()) : AnyButtonStyle(PrimaryButtonStyle()))
            .accessibilityIdentifier("runflow.rentWithoutPreview")
            .disabled(flow.isSpending)
        if !(flow.tryon?.previews.isEmpty ?? true) {
            Button("View last previews") { Task { await flow.start(.existing) } }
                .buttonStyle(SecondaryButtonStyle())
        }
    }
}

struct MessageCard: View {
    let text: String
    let dismiss: () -> Void
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "info.circle").foregroundStyle(Theme.amber)
            Text(text).font(Theme.sans(13)).foregroundStyle(Theme.ink1)
            Spacer(minLength: 0)
            Button("Dismiss", action: dismiss).font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
        }
        .padding(12).card(border: Theme.amber.opacity(0.4))
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.limeInk)
            .frame(maxWidth: .infinity).padding(.vertical, 13)
            .background(Theme.lime.opacity(configuration.isPressed ? 0.7 : 1), in: .rect(cornerRadius: 12))
    }
}

struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.ink1)
            .frame(maxWidth: .infinity).padding(.vertical, 13)
            .background(Theme.surface2.opacity(configuration.isPressed ? 0.7 : 1), in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.line2))
    }
}

struct AnyButtonStyle: ButtonStyle {
    private let make: (Configuration) -> AnyView
    init<S: ButtonStyle>(_ style: S) { make = { AnyView(style.makeBody(configuration: $0)) } }
    func makeBody(configuration: Configuration) -> some View { make(configuration) }
}
