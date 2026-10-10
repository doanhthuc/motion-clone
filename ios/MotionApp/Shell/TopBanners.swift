import MotionKit
import SwiftUI

/// The app-wide notices: an unverified kill, a spend or migrate in flight, and
/// a GPU that came into stock. Until 2026-10-10 they sat in a `safeAreaInset`
/// around the whole `TabView`, which the tabs' iOS 26 navigation bars ignore:
/// the banners covered the header (☰, Select) instead of pushing anything
/// down, and every UI test that tapped the header in their first 8 s missed.
///
/// Now they sit under the header of each tab's root screen. On a pushed screen
/// the two money notices go above its header and push it down, so a spend in
/// flight is still visible where the spend happens; the stock notice waits
/// for the root.
struct TopBanners: View {
    @Environment(AppModel.self) private var model
    var includeFired = true

    var body: some View {
        VStack(spacing: 8) {
            if let pod = model.pod { KillBanner(pod: pod) }
            if let flow = model.runFlow, let migrate = model.migrate { SpendBanner(flow: flow, migrate: migrate) }
            if includeFired, let subs = model.gpuSubs { FiredBanner(subs: subs) }
        }
    }
}

extension View {
    /// On a root screen's content, inside its `NavigationStack`: under the header.
    func rootBanners() -> some View {
        safeAreaInset(edge: .top) { TopBanners() }
    }

    /// On a Motion tab's `NavigationStack`: the money notices above a pushed
    /// screen's header, while the root's own `rootBanners` is out of sight.
    func pushedBanners(_ tab: AppTab) -> some View {
        modifier(PushedBanners(tab: tab))
    }
}

private struct PushedBanners: ViewModifier {
    @Environment(AppModel.self) private var model
    let tab: AppTab

    func body(content: Content) -> some View {
        content.safeAreaInset(edge: .top) {
            if !model.motionTabsAtRoot.contains(tab) { TopBanners(includeFired: false) }
        }
    }
}
