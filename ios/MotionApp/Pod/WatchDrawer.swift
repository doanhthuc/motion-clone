import SwiftUI
import MotionKit

/// The Pod stage's drawer (2026-09-27 spec §2), built like New Job's basket.
/// Collapsed it is one bar: "Watching · N", with ⚡ when auto-resume is
/// armed. Open, it lists the subs (trash to remove) and the recent firings.
/// Not a system sheet: the GPU sheet and Balance sheet must still open over
/// the stage.
@MainActor
struct WatchDrawer: View {
    enum Level { case collapsed, open }

    let subs: GpuSubsStore
    @Binding var level: Level
    @GestureState private var drag: CGFloat = 0

    static let collapsedHeight: CGFloat = 48
    private var expanded: Bool { level == .open }

    var body: some View {
        VStack(spacing: 0) {
            handle
            if expanded { list.transition(.move(edge: .bottom).combined(with: .opacity)) }
        }
        .background(.regularMaterial, in: .rect(cornerRadius: 20))
        .clipShape(.rect(cornerRadius: 20))
        .offset(y: expanded ? max(drag, 0) : min(max(drag, -40), 0) * 0.3)
        .animation(.snappy, value: level)
    }

    private var handle: some View {
        Button { withAnimation(.snappy) { level = expanded ? .collapsed : .open } } label: {
            HStack(spacing: 10) {
                Image(systemName: expanded ? "chevron.down" : "chevron.up")
                    .font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
                Image(systemName: "bell.fill").foregroundStyle(subs.subs.isEmpty ? Theme.secondary : Theme.accent)
                Text("Watching · \(subs.subs.count)").font(.subheadline.weight(.semibold))
                if subs.armed != nil {
                    Image(systemName: "bolt.fill").foregroundStyle(Theme.accent)
                        .accessibilityLabel("auto-resume armed")
                }
                Spacer(minLength: 0)
                if !subs.unseen.isEmpty {
                    Text("\(subs.unseen.count) new").font(.caption.weight(.semibold))
                        .padding(.horizontal, 8).padding(.vertical, 3)
                        .background(Theme.accent.opacity(0.15), in: .capsule)
                        .foregroundStyle(Theme.accent)
                }
            }
            .padding(.horizontal, 16)
            .frame(height: Self.collapsedHeight)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .simultaneousGesture(
            DragGesture(minimumDistance: 12)
                .updating($drag) { value, state, _ in state = value.translation.height }
                .onEnded { value in
                    withAnimation(.snappy) {
                        if value.translation.height < -40 { level = .open }
                        if value.translation.height > 40 { level = .collapsed }
                    }
                })
        .accessibilityHint(expanded ? "Collapse the watch list" : "Show the watch list")
        .accessibilityIdentifier("pod.watch")
    }

    private var list: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                if subs.subs.isEmpty {
                    // A pre-deploy bot has no /v1/gpu/subs (2026-09-27): the
                    // bell is hidden, so don't tell the user to tap it.
                    Text(subs.unsupported ? "Subscriptions need a bot update."
                                          : "Nothing watched. Open a GPU and tap 🔔 on a datacenter.")
                        .font(.subheadline).foregroundStyle(Theme.secondary)
                        .padding(16)
                }
                ForEach(subs.subs) { sub in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(sub.name) @ \(sub.datacenter)").font(.body)
                            if let armed = sub.autoResume {
                                Label("Auto-resumes \(armed.runId) at ≤ \(Format.usd(armed.maxUsdPerHr))/h",
                                      systemImage: "bolt.fill")
                                    .font(.footnote).foregroundStyle(Theme.accent)
                            }
                        }
                        Spacer()
                        Button(role: .destructive) { Task { await subs.unwatch(sub) } } label: {
                            Image(systemName: "trash").frame(minWidth: 44, minHeight: 44)
                        }
                        .buttonStyle(.borderless)
                        .accessibilityLabel("Stop watching \(sub.name) at \(sub.datacenter)")
                    }
                    .padding(.horizontal, 16).padding(.vertical, 4)
                    Divider().padding(.leading, 16)
                }
                if !subs.fired.isEmpty {
                    Text("Recent").font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
                        .padding(.horizontal, 16).padding(.top, 12).padding(.bottom, 4)
                    ForEach(subs.fired) { firing in
                        FiringRow(firing: firing, new: subs.unseen.contains(firing))
                            .padding(.horizontal, 16).padding(.vertical, 6)
                    }
                }
            }
        }
        .onAppear { subs.markSeen() }
    }
}

/// One past firing: what came into stock, and what auto-resume did about it.
struct FiringRow: View {
    let firing: GpuSubFiring
    let new: Bool

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { ctx in
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    if new { Circle().fill(Theme.accent).frame(width: 6, height: 6) }
                    Text("\(firing.name) @ \(firing.datacenter) · \(firing.stock)").font(.subheadline)
                    Spacer()
                    Text(Format.ago(ctx.date.timeIntervalSince1970 - firing.firedAt))
                        .font(.footnote).foregroundStyle(Theme.secondary)
                }
                if firing.resumed {
                    // "resumed" only means a drain started; the rental itself
                    // can still fail, so this must not claim a pod exists.
                    Label("Auto-resume started a rental — check the run", systemImage: "bolt.fill")
                        .font(.footnote).foregroundStyle(Theme.accent)
                } else if firing.refused, let reason = firing.reason {
                    Label("Auto-resume skipped: \(reason)", systemImage: "exclamationmark.triangle")
                        .font(.footnote).foregroundStyle(Theme.warning)
                }
            }
        }
    }
}
