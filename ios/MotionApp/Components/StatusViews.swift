import SwiftUI
import MotionKit

/// "Live" — the pod is billing or a run is generating. Status, not an action,
/// so it is green-free: the accent color is reserved for what can be tapped.
struct PulseDot: View {
    var color: Color = Theme.label
    var size: CGFloat = 8
    @State private var dim = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    var body: some View {
        Circle().fill(color).frame(width: size, height: size)
            .opacity(dim && !reduceMotion ? 0.35 : 1)
            .animation(.easeInOut(duration: 0.75).repeatForever(autoreverses: true), value: dim)
            .onAppear { dim = true }
            .accessibilityHidden(true)
    }
}

/// "Stale · 2m ago" — shown when the last refresh failed but older data is on screen.
struct StaleTag: View {
    let lastSuccess: Date?
    var body: some View {
        TimelineView(.periodic(from: .now, by: 10)) { ctx in
            Label("Stale · " + Format.ago(ctx.date.timeIntervalSince(lastSuccess ?? ctx.date)) + " ago",
                  systemImage: "clock.arrow.circlepath")
                .font(.footnote).foregroundStyle(Theme.warning)
        }
    }
}

/// A failed request, with the fix next to it. Sits in a `List` row or a
/// section of its own; it has no border or tint of its own.
struct ErrorBanner: View {
    let error: APIError
    var retry: (() async -> Void)?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.danger)
                Text(error.userMessage).font(.subheadline).foregroundStyle(Theme.label)
                Spacer(minLength: 0)
                if let retry {
                    Button("Retry") { Task { await retry() } }
                        .font(.subheadline.weight(.semibold))
                        .buttonStyle(.borderless)
                }
            }
            // Only a 422 `invalid` has a detail (APIError.detailMessage), so
            // every other ErrorBanner call site renders exactly as it did
            // before this VStack existed: one greedy child, same frame.
            // Collapsed by default: the raw text is the validator's own output,
            // and it is the only thing that names the real problem, so it is
            // reachable but not in the way.
            if let detail = error.detailMessage {
                DisclosureGroup("Details") {
                    Text(detail)
                        .font(.footnote.monospaced())
                        .foregroundStyle(Theme.secondary)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .font(.footnote.weight(.semibold))
                .foregroundStyle(Theme.secondary)
            }
        }
    }
}

/// An informational note with a Dismiss — the `ErrorBanner` shape without the red.
struct MessageCard: View {
    let text: String
    let dismiss: () -> Void
    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "info.circle").foregroundStyle(Theme.secondary)
            Text(text).font(.subheadline).foregroundStyle(Theme.label)
            Spacer(minLength: 0)
            Button("Dismiss", action: dismiss)
                .font(.subheadline.weight(.semibold))
                .buttonStyle(.borderless)
        }
    }
}

/// Centered placeholder for a list with nothing in it — no box around it.
struct EmptyNote: View {
    let title: String
    let systemImage: String
    var message: String?
    var body: some View {
        ContentUnavailableView {
            Label(title, systemImage: systemImage)
        } description: {
            if let message { Text(message) }
        }
        .frame(maxWidth: .infinity)
    }
}
