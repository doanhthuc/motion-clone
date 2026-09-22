import SwiftUI

/// Tokens from the Claude Design canvas "Motion — iPhone App". Dark only.
enum Theme {
    static let bg = Color(hex: 0x0A0A0C)
    static let surface = Color(hex: 0x141418)
    static let surface2 = Color(hex: 0x1B1B20)
    static let surface3 = Color(hex: 0x232329)
    static let line = Color.white.opacity(0.07)
    static let line2 = Color.white.opacity(0.14)
    static let ink = Color.white
    static let ink1 = Color(hex: 0xEDEDF0)
    static let ink2 = Color(hex: 0x9B9BA4)
    static let ink3 = Color(hex: 0x6C6C75)
    static let lime = Color(hex: 0xB9DD6B)
    static let limeInk = Color(hex: 0x0A0A0C)
    static let limeDim = Color(hex: 0xB9DD6B).opacity(0.10)
    static let limeLine = Color(hex: 0xB9DD6B).opacity(0.32)
    static let red = Color(hex: 0xFF5C5C)
    static let redDim = Color(hex: 0xFF5C5C).opacity(0.13)
    static let redLine = Color(hex: 0xFF5C5C).opacity(0.40)
    static let amber = Color(hex: 0xF5B84B)

    static func sans(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .custom("Space Grotesk", size: size).weight(weight)
    }
    static func mono(_ size: CGFloat, _ weight: Font.Weight = .regular) -> Font {
        .custom("JetBrains Mono", size: size).weight(weight).monospacedDigit()
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
    /// The design's card: surface fill, hairline border, rounded corners.
    func card(radius: CGFloat = 15, border: Color = Theme.line) -> some View {
        background(Theme.surface, in: .rect(cornerRadius: radius))
            .overlay(RoundedRectangle(cornerRadius: radius).strokeBorder(border))
    }
}
