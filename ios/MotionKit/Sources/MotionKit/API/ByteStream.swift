import Foundation

/// What `APIClient.streamRange` yields: the response's metadata once, as soon
/// as the headers land, then the body in whatever pieces the network delivers.
public enum ByteStreamEvent: Sendable, Equatable {
    case head(contentType: String?, totalLength: Int64?, acceptsRanges: Bool)
    case data(Data)
}

/// The per-task delegate behind `streamRange`. URLSession calls one task's
/// delegate methods serially, so the error-body state needs no lock.
final class ByteStreamDelegate: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    typealias Continuation = AsyncThrowingStream<ByteStreamEvent, any Error>.Continuation

    private let continuation: Continuation
    /// Set when the answer isn't a 206: the body is then an error envelope,
    /// collected (capped) for `APIClient.error`, not handed out as video bytes.
    private var errorStatus: Int?
    private var errorBody = Data()

    init(_ continuation: Continuation) {
        self.continuation = continuation
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse) async -> URLSession.ResponseDisposition {
        guard let http = response as? HTTPURLResponse else {
            continuation.finish(throwing: APIError.transport("not an HTTP response"))
            return .cancel
        }
        guard http.statusCode == 206 else {
            errorStatus = http.statusCode
            return .allow
        }
        continuation.yield(.head(
            contentType: http.value(forHTTPHeaderField: "Content-Type"),
            totalLength: http.value(forHTTPHeaderField: "Content-Range").flatMap(APIClient.totalLength),
            acceptsRanges: true))
        return .allow
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        if errorStatus != nil {
            if errorBody.count < 4096 { errorBody.append(data) }
        } else {
            continuation.yield(.data(data))
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: (any Error)?) {
        if let error {
            continuation.finish(throwing: APIError.transport(error.localizedDescription))
        } else if let errorStatus {
            continuation.finish(throwing: APIClient.error(status: errorStatus, body: errorBody))
        } else {
            continuation.finish()
        }
    }
}
