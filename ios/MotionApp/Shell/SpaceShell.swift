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

    var body: some View {
        @Bindable var model = model
        let offset = max(0, min(width, (model.isSidebarOpen ? width : 0) + drag))
        ZStack(alignment: .leading) {
            SidebarView(studio: studio)
                .frame(width: width)
                .offset(x: offset - width)
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
            }
            .offset(x: offset)
        }
        .gesture(
            DragGesture(minimumDistance: 12)
                .onChanged { value in
                    let fromEdge = value.startLocation.x < 24
                    if model.isSidebarOpen || fromEdge { drag = value.translation.width }
                }
                .onEnded { value in
                    let projected = (model.isSidebarOpen ? width : 0) + value.predictedEndTranslation.width
                    withAnimation(.snappy) {
                        model.isSidebarOpen = projected > width / 2
                        drag = 0
                    }
                }
        )
        .onChange(of: model.isSidebarOpen) { _, open in if open { Task { await studio.loadProjects() } } }
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

/// Replaced by the real Studio screen in Task 8.
struct StudioSpaceView: View {
    let studio: StudioStore
    var body: some View { NavigationStack { Text("Image Studio").sidebarButton() } }
}
