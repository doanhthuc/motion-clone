import SwiftUI

/// A banner dismissed by flicking it up, the way a notification goes.
struct SwipeUpToDismiss: ViewModifier {
    let dismiss: () -> Void
    @GestureState private var drag: CGFloat = 0

    func body(content: Content) -> some View {
        content
            .offset(y: min(drag, 0))
            .gesture(DragGesture(minimumDistance: 10)
                .updating($drag) { value, state, _ in state = value.translation.height }
                .onEnded { value in if value.translation.height < -30 { dismiss() } })
            .accessibilityAction(named: "Dismiss", dismiss)
    }
}
