import MotionKit
import SwiftUI

/// Something the AI is still making, drawn from what it is made of: a dimmed
/// source picture filling the page, a band of light sweeping down it while
/// work is happening, an optional inset (the outfit going onto the person),
/// and a status in the corner. Shared by the try-on carousel and run detail so
/// "generating" looks the same wherever it shows.
struct WorkCanvas<Status: View>: View {
    let backdrop: UIImage?
    var inset: UIImage? = nil
    let animating: Bool
    @ViewBuilder let status: Status
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            // `Color.clear`-style sizing: the fill image must not grow the stack.
            Theme.surface
                .overlay {
                    if let backdrop {
                        Image(uiImage: backdrop).resizable().scaledToFill()
                            .blur(radius: animating ? 6 : 14)
                            .saturation(0.4)
                            .opacity(0.55)
                    }
                }
                .clipped()
            if animating && !reduceMotion { ScanBand() }
            VStack {
                Spacer()
                HStack(alignment: .bottom, spacing: 12) {
                    if let inset {
                        Image(uiImage: inset).resizable().scaledToFill()
                            .frame(width: 64, height: 86)
                            .clipShape(.rect(cornerRadius: Theme.Radius.small))
                            .overlay(RoundedRectangle(cornerRadius: Theme.Radius.small)
                                .strokeBorder(.white.opacity(0.35), lineWidth: 1))
                            .shadow(color: .black.opacity(0.4), radius: 8, y: 4)
                            .accessibilityHidden(true)
                    }
                    status
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 16)
                .padding(.bottom, 64)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .clipShape(.rect(cornerRadius: 22))
        .accessibilityElement(children: .combine)
    }
}

/// "✦ Dressing…" over a detail line: the corner status of a `WorkCanvas`.
struct WorkStatus<Detail: View>: View {
    let title: String
    let active: Bool
    var symbol = "sparkles"
    var tint: Color? = nil
    @ViewBuilder let detail: Detail
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: symbol)
                    .symbolEffect(.pulse, options: .repeating, isActive: active && !reduceMotion)
                    .foregroundStyle(tint ?? (active ? Theme.accent : Theme.secondary))
                Text(title).font(.headline)
            }
            detail.font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
        }
    }
}

/// A soft accent band that sweeps top to bottom every 2.2 s.
struct ScanBand: View {
    var body: some View {
        GeometryReader { geo in
            TimelineView(.animation) { ctx in
                let t = ctx.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 2.2) / 2.2
                let band = geo.size.height * 0.28
                LinearGradient(colors: [Theme.accent.opacity(0), Theme.accent.opacity(0.28),
                                        .white.opacity(0.35), Theme.accent.opacity(0.28), Theme.accent.opacity(0)],
                               startPoint: .top, endPoint: .bottom)
                    .frame(height: band)
                    .offset(y: -band + (geo.size.height + band) * t)
                    .blendMode(.plusLighter)
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// One dot per page, colored by its state; the current one stretches.
struct StatusDots: View {
    struct Item: Identifiable {
        let id: String
        let status: StageStatus
    }

    let items: [Item]
    /// "Look" or "Job" — what VoiceOver counts.
    let noun: String
    let selection: String?
    let select: (String) -> Void

    var body: some View {
        HStack(spacing: 6) {
            ForEach(items) { item in
                Capsule()
                    .fill(color(item.status))
                    .frame(width: item.id == selection ? 22 : 7, height: 7)
                    .opacity(item.id == selection ? 1 : 0.6)
                    .onTapGesture { select(item.id) }
            }
        }
        .animation(.snappy, value: selection)
        .accessibilityElement()
        .accessibilityLabel("\(noun) \((items.firstIndex { $0.id == selection } ?? 0) + 1) of \(items.count)")
    }

    private func color(_ status: StageStatus) -> Color {
        switch status {
        case .done: Theme.accent
        case .error: Theme.danger
        case .running: Theme.label
        case .pending, .unknown: Theme.tertiary
        }
    }
}

/// Icon over word on a gray tile: one slot in a page's action bar.
struct ActionLabel: View {
    let title: String
    let systemImage: String
    var tint: Color = Theme.label
    @Environment(\.isEnabled) private var isEnabled

    var body: some View {
        VStack(spacing: 5) {
            Image(systemName: systemImage).font(.system(size: 19, weight: .medium))
                .contentTransition(.symbolEffect(.replace))
            Text(title).font(.caption.weight(.medium))
        }
        .foregroundStyle(isEnabled ? tint : Theme.tertiary)
        .frame(maxWidth: .infinity, minHeight: 58)
        .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
        .contentShape(.rect)
    }
}

struct ActionButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.94 : 1)
            .animation(.snappy(duration: 0.15), value: configuration.isPressed)
    }
}

/// The name and state along the bottom of a page, over a dark fade.
struct PageCaption: View {
    let title: String
    var subtitle: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.subheadline.weight(.semibold))
                .lineLimit(1).truncationMode(.middle)
            if let subtitle {
                Text(subtitle).font(.footnote).foregroundStyle(.white.opacity(0.75))
            }
        }
        .foregroundStyle(.white)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16).padding(.top, 36).padding(.bottom, 14)
        .background(LinearGradient(colors: [.clear, .black.opacity(0.7)], startPoint: .top, endPoint: .bottom))
        .allowsHitTesting(false)
    }
}
