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
    /// One round trip through the tunnel: 1 MiB is ~0.3 s from the Mac, small
    /// enough that the first frame never waits for more than one of them.
    private static let chunk: Int64 = 1 << 20

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
            // Answered in `Self.chunk`-sized pieces, each handed to AVPlayer as
            // it lands. Before 2026-09-25 a to-the-end request was one GET for
            // the whole remainder, and AVPlayer saw no byte until all of it had
            // arrived: a 20.7 MB output took 2.0–2.3 s from the Mac against
            // 0.28–0.32 s for its first MiB (measured through the tunnel), and
            // far longer on a phone's link — playback waited for the download.
            var offset = start
            var remaining = wanted
            var total: Int64?
            do {
                while !Task.isCancelled {
                    let size = remaining.map { min($0, Self.chunk) } ?? Self.chunk
                    let response = try await client.byteRange(
                        from: offset, length: Int(size), path: path)
                    guard !Task.isCancelled else { return }
                    if total == nil {
                        total = response.totalLength
                        if let info = box.value.contentInformationRequest {
                            info.contentType = response.contentType.flatMap { UTType(mimeType: $0)?.identifier }
                            if let totalLength = response.totalLength { info.contentLength = totalLength }
                            info.isByteRangeAccessSupported = response.acceptsRanges
                        }
                    }
                    box.value.dataRequest?.respond(with: response.data)
                    offset += Int64(response.data.count)
                    remaining = remaining.map { $0 - Int64(response.data.count) }
                    let atEnd = total.map { offset >= $0 } ?? true
                    if response.data.isEmpty || atEnd || (remaining ?? 1) <= 0 { break }
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
