import SwiftUI
import MotionKit

/// Red, behind a confirmation dialog. Never disabled by a pending spend —
/// only by a kill already sending or polling (Phase 5 design §4).
struct KillButton: View {
    let pod: PodStore
    let runID: String
    let hasLease: Bool
    @State private var confirming = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { confirming = true } label: {
                HStack(spacing: 8) {
                    if pod.isKilling { ProgressView().tint(Theme.danger) }
                    Text(label)
                }
            }
            .buttonStyle(DestructiveButtonStyle())
            .disabled(pod.isKilling)
            .accessibilityIdentifier("pod.kill")
            .confirmationDialog(hasLease ? "Destroy the pod now?" : "Stop the try-on phase?",
                                isPresented: $confirming, titleVisibility: .visible) {
                Button(hasLease ? "Destroy the pod" : "Stop the try-on phase", role: .destructive) {
                    Task { await pod.kill(runID: runID) }
                }
            } message: {
                Text(hasLease ? "Jobs in progress are lost; finished outputs stay."
                              : "Nothing was rented; Gemini calls already made are not refunded.")
            }
            KillNotice(pod: pod)
        }
    }

    private var label: String {
        switch pod.killState {
        case .idle: hasLease ? "Kill · destroy the pod" : "Kill · stop the try-on phase"
        case .sending: "Sending kill…"
        case .killing: "Killing… (up to ~3 min)"
        }
    }
}

/// The last kill's outcome, also shown after Kill itself disappears.
struct KillNotice: View {
    let pod: PodStore
    var body: some View {
        if let notice = pod.killNotice {
            Text(notice.text).font(.subheadline)
                .foregroundStyle(notice.isError ? Theme.danger : Theme.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        if pod.killStillRunning {
            Button("Check again") { Task { await pod.checkKillAgain() } }
                .buttonStyle(SecondaryButtonStyle())
        }
    }
}

/// On every tab until acknowledged or overwritten by a later successful kill.
struct KillBanner: View {
    let pod: PodStore
    @State private var confirming = false

    var body: some View {
        if let kill = pod.unverifiedKill {
            VStack(alignment: .leading, spacing: 8) {
                Label("Pod may still be billing — check RunPod", systemImage: "exclamationmark.octagon.fill")
                    .font(.headline).foregroundStyle(Theme.danger)
                Text(kill.message).font(.subheadline)
                Button("I checked — the pod is gone") { confirming = true }
                    .font(.subheadline.weight(.semibold))
            }
            .heroSurface()
            .padding(.horizontal, 16)
            .accessibilityIdentifier("pod.unverifiedKill")
            .confirmationDialog("Only confirm after checking the RunPod console (or runpodctl get pod).",
                                isPresented: $confirming, titleVisibility: .visible) {
                Button("The pod is gone — hide this", role: .destructive) { pod.acknowledgeUnverifiedKill() }
            }
        }
    }
}
