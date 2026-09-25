import SwiftUI

/// The app mark, animated: the three frames gather into one stack, back card
/// first, then peel apart again one at a time. For screen- and section-level
/// waits; a spinner inside a button or row stays a `ProgressView`, because at
/// that size the cards are too small to read.
struct StackLoader: View {
    var size: CGFloat = 56
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Group {
            if reduceMotion {
                StackMark(mid: 1, back: 1)
            } else {
                TimelineView(.animation) { ctx in
                    let (mid, back) = Self.offsets(at: ctx.date.timeIntervalSinceReferenceDate)
                    StackMark(mid: mid, back: back)
                }
            }
        }
        .frame(width: size, height: size)
        .accessibilityElement()
        .accessibilityLabel("Loading")
    }

    static let period: Double = 2.4

    /// How far each card sits from the one in front of it: 1 = fanned out as in
    /// the icon, 0 = hidden behind it. Gather is back-then-mid, split is
    /// mid-then-back, with a beat held at both ends.
    static func offsets(at time: Double) -> (mid: CGFloat, back: CGFloat) {
        let t = time.truncatingRemainder(dividingBy: period) / period
        func ramp(_ a: Double, _ b: Double) -> CGFloat {
            let x = min(max((t - a) / (b - a), 0), 1)
            return CGFloat(x * x * (3 - 2 * x))
        }
        let back = 1 - ramp(0.08, 0.28) + ramp(0.78, 0.98)
        let mid = 1 - ramp(0.28, 0.48) + ramp(0.58, 0.78)
        return (mid, back)
    }
}

/// Three sheared rounded cards, drawn to the same proportions as the app icon
/// (ios/MotionApp/Assets.xcassets/AppIcon.appiconset).
private struct StackMark: View {
    var mid: CGFloat
    var back: CGFloat

    // Icon geometry, in the 1024-canvas units the icon was drawn in.
    private static let card = CGSize(width: 398, height: 460)
    private static let radius: CGFloat = 46
    private static let gap: CGFloat = 20
    private static let shear: CGFloat = 0.307       // cards rise to the right
    private static let step = CGSize(width: -145, height: -62)
    // Bounding box of the fanned mark: 2 steps left plus a card, and from the
    // back card's raised top-right corner down to the front card's bottom.
    private static let bounds = CGRect(x: 2 * step.width, y: 2 * step.height - shear * card.width,
                                       width: card.width - 2 * step.width,
                                       height: card.height - 2 * step.height + shear * card.width)
    // The icon's shades: the accent, and the accent darkened for depth.
    private static let colors = [Color(hex: 0x5E7A2A), Color(hex: 0x8FA852), Theme.accent]

    var body: some View {
        Canvas { ctx, size in
            // Scale for the fanned mark, so a card is the same size in every
            // frame, but centre what is visible now: otherwise the gathered
            // stack sits in the fanned mark's bottom-right corner.
            let k = min(size.width / Self.bounds.width, size.height / Self.bounds.height)
            let spread = mid + back
            let now = CGRect(x: spread * Self.step.width, y: spread * Self.step.height - Self.shear * Self.card.width,
                             width: Self.card.width - spread * Self.step.width,
                             height: Self.card.height - spread * Self.step.height + Self.shear * Self.card.width)
            let origin = CGPoint(x: size.width / 2 - now.midX * k, y: size.height / 2 - now.midY * k)
            let steps = [mid + back, mid, 0]         // back, mid, front
            for (i, s) in steps.enumerated() {
                let topLeft = CGPoint(x: origin.x + Self.step.width * s * k,
                                      y: origin.y + Self.step.height * s * k)
                // Cut a gap into whatever is behind, then paint the card.
                ctx.blendMode = .clear
                ctx.fill(Self.path(topLeft, k: k, grow: Self.gap), with: .color(.black))
                ctx.blendMode = .normal
                ctx.fill(Self.path(topLeft, k: k, grow: 0), with: .color(Self.colors[i]))
            }
        }
    }

    private static func path(_ topLeft: CGPoint, k: CGFloat, grow: CGFloat) -> Path {
        let rect = CGRect(x: -grow * k, y: -grow * k,
                          width: (card.width + 2 * grow) * k, height: (card.height + 2 * grow) * k)
        let shape = Path(roundedRect: rect, cornerRadius: (radius + grow) * k, style: .continuous)
        // Shear about the card's own left edge, then move it into place.
        let t = CGAffineTransform(a: 1, b: -shear, c: 0, d: 1, tx: topLeft.x, ty: topLeft.y)
        return shape.applying(t)
    }
}

/// A wait that fills a screen or a section: the loader, with an optional line under it.
struct LoadingBlock: View {
    var title: String?
    var minHeight: CGFloat = 0

    var body: some View {
        VStack(spacing: 12) {
            StackLoader()
            if let title {
                Text(title).font(.subheadline).foregroundStyle(Theme.secondary)
            }
        }
        .frame(maxWidth: .infinity, minHeight: minHeight)
        .padding(.vertical, 16)
    }
}

#Preview {
    VStack(spacing: 40) {
        StackLoader(size: 120)
        LoadingBlock(title: "Loading materials…")
    }
    .padding()
    .background(Theme.bg)
    .preferredColorScheme(.dark)
}
