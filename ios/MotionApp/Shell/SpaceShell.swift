import MotionKit
import SwiftUI

/// Hosts both spaces and slides the sidebar in from the left — edge swipe or
/// the ☰ button. Not NavigationSplitView: on iPhone that collapses into a
/// push stack, not a panel.
///
/// Both spaces stay mounted and the inactive one is only hidden: a `switch`
/// rebuilt the Motion tree on every space change, which reset every tab's
/// navigation stack (Edit in Studio from a pushed run, then back to Motion,
/// landed on the Runs root — reproduced on the simulator 2026-09-26).
struct SpaceShell<Motion: View>: View {
    @Environment(AppModel.self) private var model
    let studio: StudioStore
    @ViewBuilder let motion: () -> Motion
    @State private var drag: CGFloat = 0
    private let width: CGFloat = 300
    /// The floating tab bar sits well inside this margin already, so this is a
    /// defensive gap, not load-bearing: keeps the edge-swipe hot zone from
    /// covering it at all (2026-09-26 review).
    private let edgeStripBottomMargin: CGFloat = 90
    /// Keeps the strip clear of the navigation bar, so the leading edge of
    /// ☰ and Back stays tappable. 44 pt inline bar plus a small margin.
    private let edgeStripTopMargin: CGFloat = 56

    /// The open-swipe strip must never sit over a pushed screen: it took the
    /// system swipe-back there and opened the sidebar instead (simulator,
    /// 2026-09-26). Studio has no pushed screens; in Motion the strip only
    /// exists while the selected tab shows its root.
    private var edgeSwipeEnabled: Bool {
        guard !model.isSidebarOpen else { return false }
        switch model.selectedSpace {
        case .studio: return true
        case .motion: return model.motionTabsAtRoot.contains(model.selectedTab)
        }
    }

    var body: some View {
        let offset = max(0, min(width, (model.isSidebarOpen ? width : 0) + drag))
        ZStack(alignment: .leading) {
            SidebarView(studio: studio)
                .frame(width: width)
                .offset(x: offset - width)
                // Drag-to-close while open. When closed, this view is offset
                // entirely off-screen, so nothing here is reachable to drag.
                .gesture(closeDrag)
            ZStack {
                motion().spaceVisibility(model.selectedSpace == .motion)
                StudioSpaceView(studio: studio).spaceVisibility(model.selectedSpace == .studio)
            }
            .overlay {
                Color.black.opacity(0.35 * offset / width)
                    .ignoresSafeArea()
                    .allowsHitTesting(model.isSidebarOpen)
                    .onTapGesture { withAnimation(.snappy) { model.isSidebarOpen = false } }
                    // Drag-to-close on the dim area too. Hit-testing is off
                    // while closed, so this never competes with anything else.
                    .gesture(closeDrag)
            }
            .offset(x: offset)

            if edgeSwipeEnabled {
                // A narrow hot zone for the open-swipe only — not a gesture on
                // the whole app. That used to contend with ScrubBar's drag,
                // the Outputs feed's ScrollView, and the edge-to-edge
                // Materials row (`.padding(.horizontal, -16)`); this scopes it
                // to a strip too thin for any of those to start inside
                // (2026-09-26 review).
                Color.clear
                    .contentShape(Rectangle())
                    .frame(width: 20)
                    .frame(maxHeight: .infinity, alignment: .leading)
                    .padding(.top, edgeStripTopMargin)
                    .padding(.bottom, edgeStripBottomMargin)
                    .gesture(openDrag)
            }
        }
        .onChange(of: model.isSidebarOpen) { _, open in if open { Task { await studio.loadProjects() } } }
    }

    private var openDrag: some Gesture {
        DragGesture(minimumDistance: 12)
            .onChanged { value in drag = value.translation.width }
            .onEnded { value in
                let projected = value.predictedEndTranslation.width
                withAnimation(.snappy) {
                    model.isSidebarOpen = projected > width / 2
                    drag = 0
                }
            }
    }

    private var closeDrag: some Gesture {
        DragGesture(minimumDistance: 12)
            .onChanged { value in drag = value.translation.width }
            .onEnded { value in
                let projected = width + value.predictedEndTranslation.width
                withAnimation(.snappy) {
                    model.isSidebarOpen = projected > width / 2
                    drag = 0
                }
            }
    }
}

extension View {
    /// The ☰ that opens the sidebar, in the leading slot of a navigation bar.
    func sidebarButton() -> some View { modifier(SidebarButton()) }

    /// Marks the root screen of a Motion tab. A NavigationStack's root gets
    /// `onDisappear` when a screen is pushed over it and `onAppear` when it is
    /// popped back to, so this tracks "that tab is at its root" without a
    /// NavigationPath — the tabs push with value links, destination links and
    /// `navigationDestination(isPresented:)`, which no single path binding sees.
    /// Keyed per tab, so the order in which two tabs' roots appear/disappear on
    /// a tab switch doesn't matter.
    func motionTabRoot(_ tab: AppTab) -> some View { modifier(MotionTabRoot(tab: tab)) }

    /// Hidden, inert and invisible to VoiceOver while its space isn't selected,
    /// but still mounted so its navigation state survives. One known gap: the
    /// Motion tab bar is UIKit underneath and stays in the accessibility tree
    /// (not hittable, not drawn) while Studio shows; `toolbarVisibility(.hidden,
    /// for: .tabBar)` on the tab stacks or roots didn't remove it either
    /// (simulator, 2026-09-26).
    ///
    /// The off-screen offset is load-bearing: `allowsHitTesting(false)` does not
    /// stop the hidden space's UIKit navigation bar from taking touches, so the
    /// hidden Motion bar swallowed every tap on Studio's ☰ at the same spot and
    /// the button's action never ran (logged on the simulator, 2026-09-26).
    /// Moving the hidden space out of the window keeps its state and puts its
    /// UIKit views where no touch can land.
    fileprivate func spaceVisibility(_ visible: Bool) -> some View {
        opacity(visible ? 1 : 0)
            .offset(x: visible ? 0 : 20_000)
            .allowsHitTesting(visible)
            .accessibilityHidden(!visible)
    }
}

private struct MotionTabRoot: ViewModifier {
    @Environment(AppModel.self) private var model
    let tab: AppTab
    func body(content: Content) -> some View {
        content
            .onAppear { model.motionTabsAtRoot.insert(tab) }
            .onDisappear { model.motionTabsAtRoot.remove(tab) }
    }
}

private struct SidebarButton: ViewModifier {
    @Environment(AppModel.self) private var model
    func body(content: Content) -> some View {
        content.toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Button { withAnimation(.snappy) { model.isSidebarOpen = true } } label: {
                    Image(systemName: "line.3.horizontal")
                }
                .accessibilityLabel("Open sidebar")
                .accessibilityIdentifier("sidebar.open")
            }
        }
    }
}
