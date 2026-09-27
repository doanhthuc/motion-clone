import MotionKit
import SwiftUI

struct RunFlowView: View {
    let flow: RunFlow
    let entry: RunFlow.Entry
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.dismiss) private var dismiss
    @State private var didStart = false

    /// Phase A, running or done, is a pager of looks rather than list rows.
    private var showsCarousel: Bool { flow.phase == .phaseARunning || flow.phase == .previews }

    var body: some View {
        Group {
            if showsCarousel {
                VStack(spacing: 0) {
                    if hasNotices {
                        VStack(spacing: 8) { notices }
                            .padding(.horizontal, 20).padding(.top, 8)
                    }
                    TryonCarousel(flow: flow)
                }
                .background(Theme.bg)
            } else if flow.phase == .compose, let draftStore = model.draft,
                      let materials = model.materials, let library = model.tryonLibrary {
                RunComposeView(flow: flow, draftStore: draftStore, materials: materials,
                               library: library) {
                    if hasNotices { VStack(spacing: 8) { notices } }
                }
            } else {
                List {
                    if hasNotices { Section { notices } }
                    content
                }
                .safeAreaInset(edge: .bottom) { actionBar }
            }
        }
        .navigationTitle(showsCarousel ? "Try-on" : "Run")
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

    private var hasNotices: Bool { flow.error != nil || flow.message != nil || flow.needsRecheck }

    @ViewBuilder private var notices: some View {
        if let error = flow.error { ErrorBanner(error: error) { await flow.start(entry) } }
        if let message = flow.message { MessageCard(text: message) { flow.dismissMessage() } }
        if flow.needsRecheck {
            Button("Check again") { Task { await flow.recheck() } }
                .disabled(flow.isSpending)
        }
    }

    @ViewBuilder private var content: some View {
        switch flow.phase {
        case .loading:
            if flow.error == nil {
                LoadingBlock().listRowBackground(Color.clear)
            }
        case .compose:
            EmptyView()   // `RunComposeView`, outside the List
        case .phaseARunning, .previews:
            EmptyView()   // `TryonCarousel`, outside the List
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

    @ViewBuilder private var actionBar: some View {
        switch flow.phase {
        case .rentPanel where flow.panel != nil:
            RunActionBar { RentConfirmBar(flow: flow) }
        case let .choiceRequired(_, provider):
            let price = flow.quote(for: provider)
            // Both buttons rent a pod (a `confirm` with reuse/rerun try-on) —
            // the label and the quote say so, so a tap here is never a
            // surprise spend of a different kind than Confirm on the rent panel.
            RunActionBar {
                Button(price.map { "Reuse try-on & rent · ~\(Format.usd($0))" } ?? "Reuse try-on & rent") {
                    Task { await flow.choose(.reuse) }
                }
                .buttonStyle(PrimaryButtonStyle()).disabled(!flow.canSpend || price == nil)
                Button(price.map { "Re-run try-on & rent · ~\(Format.usd($0))" } ?? "Re-run try-on & rent") {
                    Task { await flow.choose(.rerun) }
                }
                .buttonStyle(SecondaryButtonStyle()).disabled(!flow.canSpend || price == nil)
            }
        default:
            EmptyView()
        }
    }
}
