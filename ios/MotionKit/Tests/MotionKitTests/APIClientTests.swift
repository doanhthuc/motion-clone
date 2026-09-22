import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite struct APIClientTests {
    @Test func sendsAllAuthHeadersAndUserAgent() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.runs) }
        _ = try await TestSupport.client().get(RunsResponse.self, "v1", "runs")
        let r = try #require(StubURLProtocol.requests.first)
        #expect(r.url?.absoluteString == "https://api.example.test/v1/runs")
        #expect(r.httpMethod == "GET")
        #expect(r.value(forHTTPHeaderField: "CF-Access-Client-Id") == "id-123")
        #expect(r.value(forHTTPHeaderField: "CF-Access-Client-Secret") == "secret-456")
        #expect(r.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
        #expect(r.value(forHTTPHeaderField: "User-Agent") == "MotionApp/1 (iOS)")
    }

    @Test func encodesEachComponentAsOneSegment() {
        let url = TestSupport.client().url("v1", "outputs", "batch 1", "áo/dài.mp4")
        #expect(url.absoluteString == "https://api.example.test/v1/outputs/batch%201/%C3%A1o%2Fd%C3%A0i.mp4")
    }

    @Test func mapsJSONErrorEnvelope() async {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.errorConflict, status: 409) }
        await #expect(throws: APIError.server(status: 409, code: "stale_panel", message: "the panel changed")) {
            _ = try await TestSupport.client().get(RunsResponse.self, "v1", "runs")
        }
    }

    @Test func mapsCloudflareHTML403ToAccessDenied() async {
        StubURLProtocol.install { _ in (403, ["Content-Type": "text/html"], Data(Fixtures.cloudflareHTML.utf8)) }
        await #expect(throws: APIError.accessDenied(status: 403)) {
            _ = try await TestSupport.client().get(RunsResponse.self, "v1", "runs")
        }
    }

    @Test func nonJSONNon403IsServerErrorWithHTTPCode() async {
        StubURLProtocol.install { _ in (502, [:], Data("Bad gateway".utf8)) }
        await #expect(throws: APIError.server(status: 502, code: "http_502", message: "Bad gateway")) {
            _ = try await TestSupport.client().get(RunsResponse.self, "v1", "runs")
        }
    }

    @Test func undecodableBodyIsDecodingError() async {
        StubURLProtocol.install { _ in TestSupport.json(#"{"nope": 1}"#) }
        await #expect {
            _ = try await TestSupport.client().get(RunsResponse.self, "v1", "runs")
        } throws: { error in
            if case .decoding = error as? APIError { return true }
            return false
        }
    }

    @Test func etagRoundTripReturnsNilOn304() async throws {
        // Cloudflare turns a strong ETag weak when it compresses; the client
        // must still send it back and treat 304 as "unchanged".
        StubURLProtocol.install { req in
            if req.value(forHTTPHeaderField: "If-None-Match") == #"W/"abc""# {
                return (304, ["ETag": #"W/"abc""#], Data())
            }
            return TestSupport.json(Fixtures.runDetail, etag: #"W/"abc""#)
        }
        let client = TestSupport.client()
        let first = try await client.getIfChanged(RunDetail.self, "v1", "runs", "tg-1000")
        #expect(first?.id == "tg-1000")
        let second = try await client.getIfChanged(RunDetail.self, "v1", "runs", "tg-1000")
        #expect(second == nil)
        #expect(StubURLProtocol.requests.count == 2)
        #expect(StubURLProtocol.requests[0].value(forHTTPHeaderField: "If-None-Match") == nil)
    }

    @Test func healthMeasuresLatency() async throws {
        StubURLProtocol.install { _ in TestSupport.json(#"{"ok": true}"#) }
        let latency = try await TestSupport.client().health()
        #expect(latency >= .zero)
        #expect(StubURLProtocol.requests.first?.url?.path == "/v1/health")
    }

    @Test func downloadKeepsExtension() async throws {
        StubURLProtocol.install { _ in (200, ["Content-Type": "video/mp4"], Data([0, 1, 2])) }
        let file = try await TestSupport.client().download("v1", "outputs", "b", "clip.mp4")
        #expect(file.pathExtension == "mp4")
        #expect(try Data(contentsOf: file) == Data([0, 1, 2]))
    }

    @Test func byteRangeSendsAuthenticatedRangeAndParses206Metadata() async throws {
        StubURLProtocol.install { request in
            #expect(request.value(forHTTPHeaderField: "Range") == "bytes=100-199")
            #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
            return (206, ["Content-Type": "video/mp4", "Accept-Ranges": "bytes",
                          "Content-Range": "bytes 100-199/1000"], Data(repeating: 7, count: 100))
        }
        let response = try await TestSupport.client().byteRange(
            from: 100, length: 100, "v1", "outputs", "batch", "clip.mp4")
        #expect(response.data == Data(repeating: 7, count: 100))
        #expect(response.contentType == "video/mp4")
        #expect(response.totalLength == 1000)
        #expect(response.acceptsRanges)
    }

    @Test func byteRangeCanRequestToEnd() async throws {
        StubURLProtocol.install { request in
            #expect(request.value(forHTTPHeaderField: "Range") == "bytes=900-")
            return (206, ["Content-Range": "bytes 900-999/1000"], Data(repeating: 1, count: 100))
        }
        let response = try await TestSupport.client().byteRange(
            from: 900, length: nil, "v1", "outputs", "batch", "clip.mp4")
        #expect(response.totalLength == 1000)
        #expect(response.acceptsRanges)
    }

    @Test func userMessages() {
        #expect(APIError.server(status: 401, code: "unauthorized", message: "x").userMessage
                == "Bearer token rejected — check Settings.")
        #expect(APIError.accessDenied(status: 403).userMessage
                == "Cloudflare Access rejected the request — check the service token in Settings.")
        #expect(APIError.server(status: 409, code: "c", message: "Server text.").userMessage == "Server text.")
        #expect(APIError.server(status: 502, code: "upstream_unavailable", message: "x").userMessage
                == "RunPod/Vast didn't answer. Try again.")
        #expect(APIError.server(status: 500, code: "internal", message: "x").userMessage
                == "Server error (internal).")
        #expect(APIError.transport("offline").isOffline)
    }
}
}
