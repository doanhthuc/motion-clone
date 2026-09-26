import MotionKit
import SwiftUI

/// Hosts the current space and slides the sidebar in from the left — edge
/// swipe or the ☰ button. Not NavigationSplitView: on iPhone that collapses
/// into a push stack, not a panel.
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

    var body: some View {
        let offset = max(0, min(width, (model.isSidebarOpen ? width : 0) + drag))
        ZStack(alignment: .leading) {
            SidebarView(studio: studio)
                .frame(width: width)
                .offset(x: offset - width)
                // Drag-to-close while open. When closed, this view is offset
                // entirely off-screen, so nothing here is reachable to drag.
                .gesture(closeDrag)
            Group {
                switch model.selectedSpace {
                case .motion: motion()
                case .studio: StudioSpaceView(studio: studio)
                }
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

            if !model.isSidebarOpen {
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
