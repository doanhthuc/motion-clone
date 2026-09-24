import SwiftUI
import MotionKit

struct PulseDot: View {
    var color: Color = Theme.lime
    var size: CGFloat = 8
    @State private var dim = false
    var body: some View {
        Circle().fill(color).frame(width: size, height: size)
            .opacity(dim ? 0.35 : 1)
            .animation(.easeInOut(duration: 0.75).repeatForever(autoreverses: true), value: dim)
            .onAppear { dim = true }
    }
}

struct SectionLabel: View {
    let text: String
    var body: some View {
        Text(text.uppercased()).font(Theme.mono(11)).tracking(0.9).foregroundStyle(Theme.ink3)
    }
}

/// "stale · 2m ago" — shown when the last refresh failed but older data is on screen.
struct StaleTag: View {
    let lastSuccess: Date?
    var body: some View {
        TimelineView(.periodic(from: .now, by: 10)) { ctx in
            Text("stale · " + Format.ago(ctx.date.timeIntervalSince(lastSuccess ?? ctx.date)))
                .font(Theme.mono(10)).foregroundStyle(Theme.amber)
        }
    }
}

struct ErrorBanner: View {
    let error: APIError
    var retry: (() async -> Void)?
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.red)
                Text(error.userMessage).font(Theme.sans(13)).foregroundStyle(Theme.ink1)
                Spacer(minLength: 0)
                if let retry {
                    Button("Retry") { Task { await retry() } }
                        .font(Theme.sans(13, .semibold)).foregroundStyle(Theme.lime)
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
                        .font(Theme.mono(11))
                        .foregroundStyle(Theme.ink2)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .font(Theme.sans(12, .semibold))
                .foregroundStyle(Theme.ink2)
                .tint(Theme.lime)
            }
        }
        .padding(12)
        .background(Theme.redDim, in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.redLine))
    }
}
