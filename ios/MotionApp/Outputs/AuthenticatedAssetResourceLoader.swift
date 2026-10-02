import AVFoundation
import Foundation
import MotionKit
import UniformTypeIdentifiers

/// AVURLAsset's header dictionary is undocumented and did not produce an
/// observable authenticated Range request in the phase-1 simulator check.
/// A custom URL scheme forces AVFoundation through this delegate; each byte
/// request then uses APIClient's normal Access + bearer headers.
final class AuthenticatedAssetResourceLoader: NSObject, AVAssetResourceLoaderDelegate, @unchecked Sendable {
    private static let scheme = "motion-auth"
    private let client: APIClient
    /// API path segments, e.g. `["v1", "outputs", batch, file]`.
    private let path: [String]
    private let delegateQueue = DispatchQueue(label: "xyz.doanhthuc.motion.asset-loader")
    private let lock = NSLock()
    private var tasks: [ObjectIdentifier: Task<Void, Never>] = [:]

    init(client: APIClient, path: [String]) {
        self.client = client
        self.path = path
    }

    func makeAsset() -> AVURLAsset {
        let target = client.url(path)
        var components = URLComponents(url: target, resolvingAgainstBaseURL: false)!
        components.scheme = Self.scheme
        let asset = AVURLAsset(url: components.url!)
        asset.resourceLoader.setDelegate(self, queue: delegateQueue)
        return asset
    }

    func cancelAll() {
        let active = lock.withLock {
            defer { tasks.removeAll() }
            return Array(tasks.values)
        }
        active.forEach { $0.cancel() }
    }

    func resourceLoader(_ resourceLoader: AVAssetResourceLoader,
                        shouldWaitForLoadingOfRequestedResource loadingRequest: AVAssetResourceLoadingRequest) -> Bool {
        guard loadingRequest.request.url?.scheme == Self.scheme,
              let dataRequest = loadingRequest.dataRequest else { return false }

        let start = max(dataRequest.requestedOffset, dataRequest.currentOffset)
        let consumed = max(0, start - dataRequest.requestedOffset)
        let requestedLength = max(0, dataRequest.requestedLength - Int(consumed))
        // nil: AVPlayer wants everything to the end of the file.
        let wanted: Int64? = dataRequest.requestsAllDataToEndOfResource ? nil : Int64(requestedLength)
        if wanted == 0 {
            loadingRequest.finishLoading()
            return true
        }

        let key = ObjectIdentifier(loadingRequest)
        let box = LoadingRequestBox(loadingRequest)
        let task = Task { [weak self] in
            guard let self else { return }
            defer { _ = lock.withLock { tasks.removeValue(forKey: key) } }
            // One GET for the whole request, each piece handed to AVPlayer as it
            // lands. The first frame waits only for the first piece, and the
            // rest flows at single-GET speed. The 1 MiB-chunk loop this replaced
            // (2026-09-25 to 2026-10-02) paid one round trip per MiB: measured
            // through the tunnel from the Mac on 2026-10-02, a 1 MiB range took
            // 0.44–1.64 s while one GET for the whole 17.5 MB output ran at
            // 4.1–4.8 MB/s, against the 1.75 MB/s its 14 Mbps bitrate needs.
            do {
                for try await event in client.streamRange(from: start, length: wanted, path: path) {
                    guard !Task.isCancelled else { return }
                    switch event {
                    case let .head(contentType, totalLength, acceptsRanges):
                        if let info = box.value.contentInformationRequest {
                            info.contentType = contentType.flatMap { UTType(mimeType: $0)?.identifier }
                            if let totalLength { info.contentLength = totalLength }
                            info.isByteRangeAccessSupported = acceptsRanges
                        }
                    case let .data(data):
                        box.value.dataRequest?.respond(with: data)
                    }
                }
                guard !Task.isCancelled else { return }
                box.value.finishLoading()
            } catch {
                guard !Task.isCancelled else { return }
                box.value.finishLoading(with: error)
            }
        }
        lock.withLock { tasks[key] = task }
        return true
    }

    func resourceLoader(_ resourceLoader: AVAssetResourceLoader,
                        didCancel loadingRequest: AVAssetResourceLoadingRequest) {
        lock.withLock { tasks.removeValue(forKey: ObjectIdentifier(loadingRequest)) }?.cancel()
    }
}

private final class LoadingRequestBox: @unchecked Sendable {
    let value: AVAssetResourceLoadingRequest
    init(_ value: AVAssetResourceLoadingRequest) { self.value = value }
}
