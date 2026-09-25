import AVFoundation
import SwiftUI

/// AVPlayerLayer without AVKit's controls.
struct PlayerSurface: UIViewRepresentable {
    let player: AVPlayer
    let videoSize: CGSize

    final class LayerView: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
        var videoSize: CGSize = .zero { didSet { if videoSize != oldValue { setNeedsLayout() } } }

        /// Fill when that costs at most 15% of an edge (a 9:16 clip here loses ~10%),
        /// otherwise fit: a landscape clip filled into a portrait area would lose ~70%.
        override func layoutSubviews() {
            super.layoutSubviews()
            guard videoSize.width > 0, videoSize.height > 0, bounds.height > 0 else { return }
            let area = bounds.width / bounds.height
            let video = videoSize.width / videoSize.height
            let crop = 1 - min(area, video) / max(area, video)
            playerLayer.videoGravity = crop <= 0.15 ? .resizeAspectFill : .resizeAspect
        }
    }

    func makeUIView(context: Context) -> LayerView {
        let view = LayerView()
        view.backgroundColor = .black
        view.clipsToBounds = true
        view.playerLayer.videoGravity = .resizeAspect
        view.playerLayer.player = player
        return view
    }

    func updateUIView(_ view: LayerView, context: Context) {
        if view.playerLayer.player !== player { view.playerLayer.player = player }
        view.videoSize = videoSize
    }
}

/// A clip's picture with its state drawn on top: a spinner while it buffers,
/// a play glyph while the viewer has it paused. Shared by the Outputs feed and
/// the material preview so the two players look and behave the same.
struct ClipSurface: View {
    let clip: FeedClip
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            PlayerSurface(player: clip.player, videoSize: clip.videoSize)
            if clip.buffering && !clip.userPaused { ProgressView().tint(.white) }
            Image(systemName: "play.fill")
                .font(.system(size: 56))
                .foregroundStyle(.white.opacity(0.85))
                .shadow(color: .black.opacity(0.4), radius: 8)
                .opacity(clip.userPaused ? 1 : 0)
                .scaleEffect(clip.userPaused || reduceMotion ? 1 : 1.3)
                .animation(reduceMotion ? .easeOut(duration: 0.15) : .spring(duration: 0.25),
                           value: clip.userPaused)
                .allowsHitTesting(false)
        }
    }
}
