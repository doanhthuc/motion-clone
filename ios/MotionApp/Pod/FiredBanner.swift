import SwiftUI
import MotionKit

/// The newest firing the app has not shown yet. Telegram already buzzed the
/// phone; this is the same news for whoever opens the app first. Moved from
/// the Pod stage to RootView's top inset (2026-09-27) so it shows on every
/// tab — an auto-resume that started a rental matters wherever the user is —
/// and from there under each tab root's header (`TopBanners`, 2026-10-10).
struct FiredBanner: View {
    @Environment(AppModel.self) private var model
    let subs: GpuSubsStore
    /// Hidden at once on a tap, before the store's write lands.
    @State private var hidden: String?

    /// The drawer lists the same firing; showing both would say it twice.
    private var drawerShowing: Bool {
        model.selectedSpace == .motion && model.selectedTab == .pod && model.watchDrawerOpen
    }

    var body: some View {
        // The 8 s auto-hide marks the banner shown, not the firing seen: the
        // drawer still counts it as new, but the banner doesn't come back on
        // the next launch.
        if !drawerShowing, let firing = subs.bannerFiring, firing.id != hidden {
            let text = firing.resumed
                ? "⚡ \(firing.name) @ \(firing.datacenter) came into stock — auto-resume started a rental, check the run."
                : firing.refused
                    ? "🔔 \(firing.name) @ \(firing.datacenter) came into stock. Auto-resume skipped: \(firing.reason ?? "refused")."
                    : "🔔 \(firing.name) @ \(firing.datacenter) came into stock (\(firing.stock))."
            MessageCard(text: text) { dismiss(firing) }
                .heroSurface()
                .contentShape(.rect)
                .onTapGesture { hidden = firing.id; subs.markBannered(firing); model.openWatchList() }
                .modifier(SwipeUpToDismiss { dismiss(firing) })
                .task(id: firing.id) {
                    // A cancelled sleep (the screen was pushed over) leaves it for next time.
                    guard (try? await Task.sleep(for: .seconds(8))) != nil else { return }
                    hidden = firing.id
                    subs.markBannered(firing)
                }
                .transition(.move(edge: .top).combined(with: .opacity))
                .accessibilityAction(named: "Show the watch list") { model.openWatchList() }
                .accessibilityIdentifier("pod.firedBanner")
                .padding(.horizontal, 16)
        }
    }

    private func dismiss(_ firing: GpuSubFiring) {
        hidden = firing.id
        subs.markSeen()
    }
}
