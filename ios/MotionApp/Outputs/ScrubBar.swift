import SwiftUI

/// Hairline progress that thickens under the finger and shows the time while seeking.
struct ScrubBar: View {
    let clip: FeedClip
    @State private var dragFraction: Double?
    @ScaledMetric(relativeTo: .body) private var timeLift: CGFloat = 28

    /// Layout height inside the strip. The touch area is taller: see `touchSlop`.
    static let height: CGFloat = 24
    /// Grows the 24pt row to 44pt of touch (HIG's default control size) by reaching
    /// 10pt up onto the video and 10pt down over the counter, which isn't tappable,
    /// so the strip itself doesn't get taller.
    private static let touchSlop: CGFloat = 10

    var body: some View {
        let active = dragFraction != nil
        let fraction = dragFraction ?? clip.fraction
        Group {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(.white.opacity(0.25))
                    Capsule().fill(active ? Theme.accent : .white)
                        .frame(width: geo.size.width * fraction)
                }
                .frame(height: active ? 6 : 3)
                // The line sits on the video's bottom edge, as TikTok's does; the
                // rest of the 24pt hit area hangs down into the strip.
                .frame(maxHeight: .infinity, alignment: .top)
                .contentShape(Rectangle().inset(by: -Self.touchSlop))
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            let f = min(1, max(0, value.location.x / geo.size.width))
                            if dragFraction == nil { clip.beginScrub() }
                            dragFraction = f
                            clip.scrub(to: f)
                        }
                        .onEnded { value in
                            let f = min(1, max(0, value.location.x / geo.size.width))
                            clip.endScrub(at: f)
                            dragFraction = nil
                        }
                )
            }
            .frame(height: Self.height)
        }
        // Float the time above the bar so the strip's layout never shifts while seeking.
        .overlay(alignment: .top) {
            if active {
                Text("\(Self.clock(fraction * clip.duration)) / \(Self.clock(clip.duration))")
                    .font(.subheadline.weight(.medium).monospacedDigit()).foregroundStyle(Theme.label)
                    .shadow(color: .black.opacity(0.5), radius: 4)
                    .offset(y: -timeLift)
                    .transition(.opacity)
            }
        }
        .animation(.easeOut(duration: 0.15), value: active)
        .sensoryFeedback(.selection, trigger: active)
        .accessibilityElement()
        .accessibilityLabel("Playback position")
        .accessibilityValue("\(Self.clock(clip.time)) of \(Self.clock(clip.duration))")
        .accessibilityAdjustableAction { direction in
            guard clip.duration > 0 else { return }
            let step = 5 / clip.duration
            clip.seek(to: clip.fraction + (direction == .increment ? step : -step))
        }
    }

    private static func clock(_ seconds: Double) -> String {
        let s = Int(seconds.isFinite ? seconds.rounded(.down) : 0)
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}
