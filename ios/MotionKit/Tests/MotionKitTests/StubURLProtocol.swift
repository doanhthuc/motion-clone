import Foundation
import Testing
@testable import MotionKit

/// Every suite using StubURLProtocol lives under this serialized parent because
/// the URL loading system requires the protocol handler to be process-global.
@Suite(.serialized) struct URLProtocolTests {}

/// Answers every request from a handler; records requests. Global state, so
/// every suite that uses it is `.serialized` and calls `install` first.
final class StubURLProtocol: URLProtocol, @unchecked Sendable {
    typealias Handler = @Sendable (URLRequest) -> (Int, [String: String], Data)
    private static let lock = NSLock()
    nonisolated(unsafe) private static var handler: Handler = { _ in (500, [:], Data()) }
    nonisolated(unsafe) private static var recorded: [URLRequest] = []

    static func install(_ h: @escaping Handler) {
        lock.withLock { handler = h; recorded = [] }
    }
    static var requests: [URLRequest] { lock.withLock { recorded } }

    static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        return URLSession(configuration: config)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var captured = request
        if captured.httpBody == nil, let stream = captured.httpBodyStream {
            stream.open()
            defer { stream.close() }
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 16 * 1024)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                data.append(buffer, count: count)
            }
            captured.httpBodyStream = nil
            captured.httpBody = data
        }
        let h = Self.lock.withLock { Self.recorded.append(captured); return Self.handler }
        let (status, headers, body) = h(captured)
        let response = HTTPURLResponse(url: request.url!, statusCode: status,
                                       httpVersion: "HTTP/1.1", headerFields: headers)!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: body)
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

enum TestSupport {
    static let credentials = Credentials(
        baseURL: URL(string: "https://api.example.test")!,
        accessClientID: "id-123", accessClientSecret: "secret-456", bearerToken: "bearer-789")

    static func client() -> APIClient {
        APIClient(credentials: credentials, session: StubURLProtocol.session())
    }

    static func json(_ s: String, status: Int = 200, etag: String? = nil) -> (Int, [String: String], Data) {
        var headers = ["Content-Type": "application/json"]
        if let etag { headers["ETag"] = etag }
        return (status, headers, Data(s.utf8))
    }
}
