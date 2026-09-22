import AVFAudio
import Darwin

@main
struct PlaybackAudioSessionTestMain {
    static func main() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.soloAmbient)

        try PlaybackAudioSession.configure()

        guard session.category == .playback else {
            fputs("FAIL: expected playback audio category, got \(session.category.rawValue)\n", stderr)
            exit(1)
        }
        print("PASS: playback audio category")
    }
}
