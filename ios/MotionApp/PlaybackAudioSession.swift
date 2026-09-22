import AVFAudio

enum PlaybackAudioSession {
    static func configure() throws {
        try AVAudioSession.sharedInstance().setCategory(.playback, mode: .moviePlayback)
    }
}
