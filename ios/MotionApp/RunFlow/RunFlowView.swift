import MotionKit
import SwiftUI

struct RunFlowView: View {
    let flow: RunFlow
    let entry: RunFlow.Entry
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss

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
        .task { await flow.start(entry) }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await flow.pollTryon()
        }
    }

    @ViewBuilder private var content: some View {
        switch flow.phase {
        case .loading:
            ProgressView().frame(maxWidth: .infinity).padding(.top, 60)
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
            ForEach(flow.tryon?.previews ?? []) { preview in
                TryonPreviewCard(flow: flow, preview: preview)
            }
            Button("Continue to rent") { Task { await flow.continueToRent() } }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(flow.isSpending)
        case .rentPanel:
            RentPanelView(flow: flow)
        case .choiceRequired:
            SectionLabel(text: "Try-on already ran")
            Text("These inputs already have a try-on. Reusing it spends no Gemini/Qwen quota; re-running pays for it again.")
                .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
            Button("Reuse try-on (no quota)") { Task { await flow.choose(.reuse) } }
                .buttonStyle(PrimaryButtonStyle()).disabled(flow.isSpending)
            Button("Re-run try-on") { Task { await flow.choose(.rerun) } }
                .buttonStyle(SecondaryButtonStyle()).disabled(flow.isSpending)
        case let .started(runID):
            Label("Started — progress is on the run screen and in Telegram.", systemImage: "checkmark.circle.fill")
                .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.lime)
            if let client = model.client {
                NavigationLink("Open \(runID)") {
                    RunDetailView(store: RunDetailStore(client: client, runID: runID), flow: flow)
                }
                .buttonStyle(SecondaryButtonStyle())
            }
        case .outcomeUnknown:
            Label("Couldn't tell whether this went through. The Runs tab shows the pod.", systemImage: "questionmark.circle")
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
                .disabled(flow.isSpending)
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
