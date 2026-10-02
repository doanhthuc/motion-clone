import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite struct ByteStreamTests {
    private func collect(_ stream: AsyncThrowingStream<ByteStreamEvent, any Error>) async throws -> [ByteStreamEvent] {
        var events: [ByteStreamEvent] = []
        for try await event in stream { events.append(event) }
        return events
    }

    @Test func sendsAuthenticatedRangeAndYieldsHeadThenBody() async throws {
        StubURLProtocol.install { request in
            #expect(request.value(forHTTPHeaderField: "Range") == "bytes=100-199")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
            #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Id") == "id-123")
            return (206, ["Content-Type": "video/mp4", "Content-Range": "bytes 100-199/1000"],
                    Data(repeating: 7, count: 100))
        }
        let events = try await collect(TestSupport.client().streamRange(
            from: 100, length: 100, path: ["v1", "outputs", "batch", "clip.mp4"]))
        #expect(events.first == .head(contentType: "video/mp4", totalLength: 1000, acceptsRanges: true))
        let body = events.dropFirst().reduce(into: Data()) { acc, e in
            if case .data(let d) = e { acc.append(d) }
        }
        #expect(body == Data(repeating: 7, count: 100))
    }

    @Test func openEndedRangeAsksToTheEnd() async throws {
        StubURLProtocol.install { request in
            #expect(request.value(forHTTPHeaderField: "Range") == "bytes=900-")
            return (206, ["Content-Range": "bytes 900-999/1000"], Data(repeating: 1, count: 100))
        }
        let events = try await collect(TestSupport.client().streamRange(
            from: 900, length: nil, path: ["v1", "outputs", "batch", "clip.mp4"]))
        #expect(events.first == .head(contentType: nil, totalLength: 1000, acceptsRanges: true))
    }

    @Test func non206ThrowsTheServerErrorWithoutYieldingBytes() async throws {
        StubURLProtocol.install { _ in
            (404, ["Content-Type": "application/json"],
             Data(#"{"error":{"code":"not_found","message":"no such output"}}"#.utf8))
        }
        var yielded: [ByteStreamEvent] = []
        do {
            for try await event in TestSupport.client().streamRange(
                from: 0, length: 10, path: ["v1", "outputs", "batch", "gone.mp4"]) {
                yielded.append(event)
            }
            Issue.record("expected a throw")
        } catch let error as APIError {
            #expect(error == .server(status: 404, code: "not_found", message: "no such output"))
        }
        #expect(yielded.isEmpty)
    }

    @Test func invalidRangeFailsWithoutARequest() async throws {
        StubURLProtocol.install { _ in (206, [:], Data()) }
        await #expect(throws: APIError.transport("invalid empty byte range")) {
            _ = try await collect(TestSupport.client().streamRange(from: 0, length: 0, path: ["v1", "x"]))
        }
        #expect(StubURLProtocol.requests.isEmpty)
    }

    /// The point of streaming: the first bytes reach the caller while the rest
    /// of the range is still on the wire. The 1 MiB-chunk loader it replaced
    /// existed because the whole-range GET before it withheld every byte until
    /// the last had arrived (2026-09-25).
    @Test func yieldsBytesBeforeTheResponseFinishes() async throws {
        let client = APIClient(credentials: TestSupport.credentials, session: TrickleURLProtocol.session())
        var iterator = client.streamRange(from: 0, length: nil, path: ["v1", "outputs", "b", "c.mp4"])
            .makeAsyncIterator()
        #expect(try await iterator.next() == .head(contentType: "video/mp4", totalLength: 8, acceptsRanges: true))
        #expect(try await iterator.next() == .data(Data([1, 2, 3, 4])))
        TrickleURLProtocol.release.signal()      // only now may the second half go out
        #expect(try await iterator.next() == .data(Data([5, 6, 7, 8])))
        #expect(try await iterator.next() == nil)
    }
}
}

/// Sends half the body, then holds the rest until the test releases it.
private final class TrickleURLProtocol: URLProtocol, @unchecked Sendable {
    static let release = DispatchSemaphore(value: 0)

    static func session() -> URLSession {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [TrickleURLProtocol.self]
        return URLSession(configuration: config)
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let response = HTTPURLResponse(url: request.url!, statusCode: 206, httpVersion: "HTTP/1.1",
                                       headerFields: ["Content-Type": "video/mp4",
                                                      "Content-Range": "bytes 0-7/8"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data([1, 2, 3, 4]))
        DispatchQueue.global().async { [self] in
            Self.release.wait()
            client?.urlProtocol(self, didLoad: Data([5, 6, 7, 8]))
            client?.urlProtocolDidFinishLoading(self)
        }
    }
    override func stopLoading() {}
}
