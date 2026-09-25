import AVFoundation
import MotionKit
import Observation
import UIKit

/// One looping video in the outputs feed. Owns its player, its authenticated
/// byte loader and the clock the scrub bar reads.
@MainActor @Observable
final class FeedClip {
    let player: AVPlayer
    private(set) var time: Double = 0
    private(set) var duration: Double = 0
    private(set) var buffering = true
    private(set) var videoSize: CGSize = .zero
    /// Paused by the viewer's tap, as opposed to paused because the page is off screen.
    private(set) var userPaused = false
    private(set) var scrubbing = false

    @ObservationIgnored private let loader: AuthenticatedAssetResourceLoader
    @ObservationIgnored private var timeToken: Any?
    @ObservationIgnored private var statusToken: NSKeyValueObservation?
    @ObservationIgnored private var loopTask: Task<Void, Never>?

    var fraction: Double { duration > 0 ? min(1, max(0, time / duration)) : 0 }

    init(client: APIClient, batch: String, fileName: String) {
        loader = AuthenticatedAssetResourceLoader(client: client, path: ["v1", "outputs", batch, fileName])
        let item = AVPlayerItem(asset: loader.makeAsset())
        player = AVPlayer(playerItem: item)
        // Loop by seeking back at the end rather than with AVPlayerLooper: the
        // looper plays copies of the item, and each copy would re-fetch its
        // bytes through the loader over the tunnel.
        player.actionAtItemEnd = .none
        timeToken = player.addPeriodicTimeObserver(
            forInterval: CMTime(value: 1, timescale: 30), queue: .main
        ) { [weak self] t in
            MainActor.assumeIsolated { self?.tick(t) }
        }
        statusToken = player.observe(\.timeControlStatus, options: [.initial, .new]) { [weak self] p, _ in
            let waiting = p.timeControlStatus == .waitingToPlayAtSpecifiedRate
            Task { @MainActor in self?.buffering = waiting }
        }
        loopTask = Task { [weak player] in
            for await _ in NotificationCenter.default.notifications(
                named: AVPlayerItem.didPlayToEndTimeNotification, object: item) {
                await player?.seek(to: .zero)
            }
        }
    }

    private func tick(_ t: CMTime) {
        if !scrubbing, t.seconds.isFinite { time = t.seconds }
        if let d = player.currentItem?.duration.seconds, d.isFinite, d > 0 { duration = d }
        if let size = player.currentItem?.presentationSize, size != videoSize { videoSize = size }
    }

    /// The page scrolled into view: start playing, even if it was tapped paused before,
    /// unless Settings › Accessibility › Motion › Auto-Play Video Previews is off. Then
    /// the page lands paused and the play icon says so.
    func activate() {
        userPaused = !UIAccessibility.isVideoAutoplayEnabled
        if !userPaused { player.play() }
    }

    /// The page scrolled away: stop and rewind, so coming back starts from the top.
    func deactivate() {
        player.pause()
        player.seek(to: .zero)
        time = 0
    }

    func togglePause() {
        userPaused.toggle()
        userPaused ? player.pause() : player.play()
    }

    /// App went to the background or the feed was covered; resume later unless the viewer paused.
    func suspend() { player.pause() }
    func resume() { if !userPaused { player.play() } }

    func beginScrub() {
        scrubbing = true
        player.pause()
    }

    func scrub(to fraction: Double) {
        guard duration > 0 else { return }
        time = fraction * duration
        // Loose tolerance while dragging keeps seeks cheap; the exact seek happens on release.
        player.seek(to: CMTime(seconds: time, preferredTimescale: 600),
                    toleranceBefore: CMTime(value: 1, timescale: 10), toleranceAfter: CMTime(value: 1, timescale: 10))
    }

    func endScrub(at fraction: Double) {
        guard duration > 0 else { scrubbing = false; return }
        time = fraction * duration
        player.seek(to: CMTime(seconds: time, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero) {
            [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.scrubbing = false
                self.resume()
            }
        }
    }

    /// A jump without a drag, for VoiceOver's adjustable action. Leaves the
    /// play/pause state as it was.
    func seek(to fraction: Double) {
        guard duration > 0 else { return }
        time = min(1, max(0, fraction)) * duration
        player.seek(to: CMTime(seconds: time, preferredTimescale: 600), toleranceBefore: .zero, toleranceAfter: .zero)
    }

    func teardown() {
        player.pause()
        if let timeToken { player.removeTimeObserver(timeToken) }
        timeToken = nil
        statusToken?.invalidate()
        loopTask?.cancel()
        loader.cancelAll()
        player.replaceCurrentItem(with: nil)
    }
}

/// Keeps players only for the page on screen and its two neighbours, so a
/// swipe lands on a video that has already started buffering while a long
/// batch never holds more than three players and three loaders.
@MainActor @Observable
final class FeedPlayback {
    private(set) var clips: [String: FeedClip] = [:]
    @ObservationIgnored private let client: APIClient
    @ObservationIgnored private let batch: String
    private var focused: String?

    init(client: APIClient, batch: String) {
        self.client = client
        self.batch = batch
    }

    func focus(_ id: String?, in files: [OutputFile]) {
        guard let id, let i = files.firstIndex(where: { $0.id == id }) else { return }
        let window = files[max(0, i - 1)...min(files.count - 1, i + 1)]
        let keep = Set(window.filter(\.isVideo).map(\.id))
        for key in clips.keys where !keep.contains(key) {
            clips.removeValue(forKey: key)?.teardown()
        }
        for key in keep where clips[key] == nil {
            clips[key] = FeedClip(client: client, batch: batch, fileName: key)
        }
        focused = id
        for (key, clip) in clips {
            key == id ? clip.activate() : clip.deactivate()
        }
    }

    var current: FeedClip? { focused.flatMap { clips[$0] } }

    func stopAll() {
        clips.values.forEach { $0.teardown() }
        clips.removeAll()
        focused = nil
    }
}
