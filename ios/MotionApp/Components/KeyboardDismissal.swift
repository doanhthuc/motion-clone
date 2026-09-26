import SwiftUI
import UIKit

extension View {
    /// Tapping anywhere outside a text field dismisses the keyboard, app-wide.
    /// Attach once, at the root: it installs a single recognizer on the
    /// window, so it also covers sheets, which are presented in the same
    /// window. Without it the keyboard stayed up after leaving the Studio
    /// prompt, the TikTok link field or the sidebar's rename field, on every
    /// screen (reported on the phone 2026-09-26) — SwiftUI only dismisses
    /// when a focused field's own view goes away.
    func dismissesKeyboardOnOutsideTap() -> some View {
        background(KeyboardDismissInstaller().frame(width: 0, height: 0))
    }
}

private struct KeyboardDismissInstaller: UIViewRepresentable {
    func makeUIView(context: Context) -> InstallerView { InstallerView() }
    func updateUIView(_ uiView: InstallerView, context: Context) {}

    final class InstallerView: UIView, UIGestureRecognizerDelegate {
        private weak var installedOn: UIWindow?
        private lazy var tap: UITapGestureRecognizer = {
            let tap = UITapGestureRecognizer(target: self, action: #selector(dismiss))
            // Observe only: the tap still reaches whatever was tapped (a
            // button, a row, the send arrow) exactly as before.
            tap.cancelsTouchesInView = false
            tap.delaysTouchesBegan = false
            tap.delaysTouchesEnded = false
            tap.delegate = self
            return tap
        }()

        override func didMoveToWindow() {
            super.didMoveToWindow()
            guard let window, window !== installedOn else { return }
            installedOn?.removeGestureRecognizer(tap)
            window.addGestureRecognizer(tap)
            installedOn = window
        }

        @objc private func dismiss() { installedOn?.endEditing(true) }

        /// A tap inside a text input (moving the caret, focusing another
        /// field) must not dismiss the keyboard it is using.
        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer, shouldReceive touch: UITouch) -> Bool {
            var view = touch.view
            while let v = view {
                if v is UITextField || v is UITextView || v is UISearchBar { return false }
                view = v.superview
            }
            return true
        }

        func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                               shouldRecognizeSimultaneouslyWith other: UIGestureRecognizer) -> Bool { true }
    }
}
