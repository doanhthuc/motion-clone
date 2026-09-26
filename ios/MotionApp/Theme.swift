import SwiftUI

/// The app's design tokens. Dark only (`MotionApp` pins the color scheme).
///
/// Structure comes from iOS itself: inset-grouped `List`s, SF Pro text styles
/// (so Dynamic Type works), system sheets and the glass tab bar. The brand is
/// one accent color and nothing else — the 2026-09-25 audit counted lime in 54
/// places meaning eight different things, amber on every untouched form field,
/// eight corner radii and 31 font sizes, and those, not any single choice, are
/// what made the app read as generated.
enum Theme {
    // Surfaces — the system grouped palette, so a `List` row and a hand-built
    // surface (a grid tile, a hero block) are the same color.
    static let bg = Color(uiColor: .systemGroupedBackground)
    static let surface = Color(uiColor: .secondarySystemGroupedBackground)
    static let surfaceRaised = Color(uiColor: .tertiarySystemGroupedBackground)

    // Text and glyphs.
    static let label = Color(uiColor: .label)
    static let secondary = Color(uiColor: .secondaryLabel)
    /// Placeholder glyphs and disabled text only — too faint for text people must read.
    static let tertiary = Color(uiColor: .tertiaryLabel)

    /// Interactive and selected, never decoration or status. It is the app tint,
    /// so plain `Button`s and links pick it up without naming it.
    static let accent = Color(hex: 0xB9DD6B)
    /// Text on an `accent` fill.
    static let onAccent = Color(hex: 0x0A0A0C)
    /// Something needs attention: a check failed, money may be at risk. Never the
    /// resting state of a field nobody has touched yet.
    static let warning = Color(hex: 0xF5B84B)
    static let danger = Color(hex: 0xFF5C5C)

    enum Radius {
        /// Thumbnails and small media.
        static let small: CGFloat = 8
        /// Buttons, hero blocks, anything grouped-list sized.
        static let medium: CGFloat = 12
    }
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255)
    }
}

extension View {
    /// A standalone block on the screen background — at most one per screen,
    /// for the status that screen exists to show. Everything else is a `List` row.
    func heroSurface() -> some View {
        padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
    }

    /// A full-width button sitting in a `List` without a row behind it.
    func buttonRow() -> some View {
        listRowInsets(EdgeInsets(top: 4, leading: 0, bottom: 4, trailing: 0))
            .listRowBackground(Color.clear)
    }
}

/// The one filled button a screen state may have.
struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        StyledButton(configuration: configuration, kind: .primary)
    }
}

/// Gray fill, no border — everything that is not the primary action.
struct SecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        StyledButton(configuration: configuration, kind: .secondary)
    }
}

/// Secondary weight with red text: the action is dangerous, not the default.
struct DestructiveButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        StyledButton(configuration: configuration, kind: .destructive)
    }
}

/// Red fill, white text: a screen whose main action is to delete the thing
/// it shows. Don't give its Button `role: .destructive`: the role paints the
/// label red over the red fill, whatever this style sets (2026-09-26).
struct DangerButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        StyledButton(configuration: configuration, kind: .danger)
    }
}

/// The body of all four styles. A view rather than code in `makeBody`, so
/// `isEnabled` resolves even when `AnyButtonStyle` calls `makeBody` by hand —
/// an `@Environment` on the style struct itself is only filled in when SwiftUI
/// installs that style.
private struct StyledButton: View {
    enum Kind { case primary, secondary, destructive, danger }
    let configuration: ButtonStyleConfiguration
    let kind: Kind
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        configuration.label
            .font(.headline)
            .foregroundStyle(foreground)
            .frame(maxWidth: .infinity, minHeight: 50)
            .background(background.opacity(configuration.isPressed ? 0.7 : 1),
                        in: .rect(cornerRadius: Theme.Radius.medium))
            .opacity((kind == .primary || kind == .danger) && !isEnabled ? 0.4 : 1)
            .contentShape(.rect)
    }

    private var foreground: Color {
        switch kind {
        case .primary: Theme.onAccent
        case .secondary: isEnabled ? Theme.label : Theme.tertiary
        case .destructive: isEnabled ? Theme.danger : Theme.tertiary
        case .danger: .white
        }
    }

    private var background: Color {
        switch kind {
        case .primary: Theme.accent
        case .danger: Theme.danger
        case .secondary, .destructive: Theme.surface
        }
    }
}

struct AnyButtonStyle: ButtonStyle {
    private let make: (Configuration) -> AnyView
    init<S: ButtonStyle>(_ style: S) { make = { AnyView(style.makeBody(configuration: $0)) } }
    func makeBody(configuration: Configuration) -> some View { make(configuration) }
}
