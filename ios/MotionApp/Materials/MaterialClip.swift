import AVFoundation
import MotionKit

/// A material video's player and the authenticated loader that feeds it —
/// the loader must live as long as the player, so they are held together.
@MainActor
final class MaterialClip {
    let player: AVPlayer
    private let loader: AuthenticatedAssetResourceLoader

    init(client: APIClient, path: [String]) {
        loader = AuthenticatedAssetResourceLoader(client: client, path: path)
        player = AVPlayer(playerItem: AVPlayerItem(asset: loader.makeAsset()))
    }

    func stop() {
        player.pause()
        loader.cancelAll()
    }
}
