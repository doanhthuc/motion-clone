import Foundation
import Testing
@testable import MotionKit

private struct CamelCasePatchBody: Encodable, Sendable {
    let materialID: String
}

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

    @Test func postEncodesJSONAndAccepts201() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.uploadOpen, status: 201) }
        let result = try await TestSupport.client().post(
            UploadOpenResponse.self,
            body: UploadOpenRequest(fileName: "áo dài.png", size: 901),
            "v1", "uploads")
        let request = try #require(StubURLProtocol.requests.first)
        #expect(result.chunkSize == 33_554_432)
        #expect(request.httpMethod == "POST")
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
        let body = try #require(request.httpBody)
        let json = try #require(try JSONSerialization.jsonObject(with: body) as? [String: Any])
        #expect(json["file_name"] as? String == "áo dài.png")
        #expect(json["size"] as? Int == 901)
    }

    @Test func bodylessPostSendsNoInventedJSON() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.uploadComplete, status: 201) }
        _ = try await TestSupport.client().post(
            UploadCompleteResponse.self, "v1", "uploads", "abc123", "complete")
        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.httpMethod == "POST")
        #expect(request.httpBody == nil)
        #expect(request.value(forHTTPHeaderField: "Content-Type") == nil)
        #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Id") == "id-123")
    }

    @Test func patchEncodesJSONAndAuthHeaders() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.draft) }
        _ = try await TestSupport.client().patch(
            Draft.self, body: SlotPatch(role: "driver", materialID: "app/dance.mp4"),
            "v1", "draft")
        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.httpMethod == "PATCH")
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
        let data = try #require(request.httpBody)
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let slots = try #require(json["slots"] as? [String: Any])
        #expect(slots["driver"] as? String == "app/dance.mp4")
    }

    @Test func draftAssignmentPatchUsesExplicitRequestTimeout() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.draft) }

        _ = try await TestSupport.client().patch(
            Draft.self,
            body: SlotPatch(role: "driver", materialID: "app/dance.mp4"),
            timeout: 95,
            "v1", "draft")

        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.httpMethod == "PATCH")
        #expect(request.timeoutInterval == 95)
    }

    @Test func draftValidationPostUsesExplicitRequestTimeout() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.validatedDraft) }

        _ = try await TestSupport.client().post(
            DraftValidationResponse.self, timeout: 95, "v1", "draft", "validate")

        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.httpMethod == "POST")
        #expect(request.httpBody == nil)
        #expect(request.timeoutInterval == 95)
    }

    @Test func getHonorsTimeout() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.runs) }

        _ = try await TestSupport.client().get(RunsResponse.self, timeout: 120, "v1", "runs")
        let timed = try #require(StubURLProtocol.requests.first)
        #expect(timed.timeoutInterval == 120)

        StubURLProtocol.install { _ in TestSupport.json(Fixtures.runs) }
        _ = try await TestSupport.client().get(RunsResponse.self, "v1", "runs")
        let plain = try #require(StubURLProtocol.requests.first)
        #expect(plain.timeoutInterval == 30)
    }

    @Test func patchConvertsCamelCaseBodyKeysToSnakeCase() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.draft) }
        _ = try await TestSupport.client().patch(
            Draft.self, body: CamelCasePatchBody(materialID: "app/dance.mp4"), "v1", "draft")
        let data = try #require(StubURLProtocol.requests.first?.httpBody)
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        #expect(json["material_id"] as? String == "app/dance.mp4")
        #expect(json["materialID"] == nil)
    }

    @Test func patchPreservesExplicitNullSlot() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.draft) }
        _ = try await TestSupport.client().patch(
            Draft.self, body: SlotPatch(role: "outfit", materialID: nil), "v1", "draft")
        let data = try #require(StubURLProtocol.requests.first?.httpBody)
        let json = try #require(try JSONSerialization.jsonObject(with: data) as? [String: Any])
        let slots = try #require(json["slots"] as? [String: Any])
        #expect(slots["outfit"] is NSNull)
    }

    @Test func decodedDeleteAccepts200AndEncodesDigest() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.draft) }
        let draft = try await TestSupport.client().delete(
            Draft.self, "v1", "draft", "batch", "a/b c")
        #expect(draft.generation == 4)
        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.httpMethod == "DELETE")
        #expect(request.url?.absoluteString.hasSuffix("/v1/draft/batch/a%2Fb%20c") == true)
    }

    @Test func putSendsExactBinaryBodyAndAuth() async throws {
        StubURLProtocol.install { _ in TestSupport.json(#"{"received":1}"#) }
        let body = Data([0, 1, 2, 255])
        try await TestSupport.client().put(
            data: body, "v1", "uploads", "abc123", "chunks", "1")
        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.httpMethod == "PUT")
        #expect(request.httpBody == body)
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/octet-stream")
        #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Secret") == "secret-456")
    }

    @Test func deleteAccepts204AndEncodesPathSegments() async throws {
        StubURLProtocol.install { _ in (204, [:], Data()) }
        try await TestSupport.client().delete("v1", "materials", "app", "áo/dài.png")
        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.httpMethod == "DELETE")
        #expect(request.url?.absoluteString ==
                "https://api.example.test/v1/materials/app/%C3%A1o%2Fd%C3%A0i.png")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
    }

    @Test func writeMethodsMapJSONErrors() async {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.errorConflict, status: 409) }
        await #expect(throws: APIError.server(
            status: 409, code: "stale_panel", message: "the panel changed")) {
            try await TestSupport.client().delete("v1", "materials", "app", "busy.png")
        }
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
@Suite struct SpendTransportTests {
    @Test func spendPostSendsKeyAuthAndJSON() async throws {
        StubURLProtocol.install { _ in TestSupport.json(#"{"run_id":"tg-1000","outcome":"started"}"#, status: 202) }
        let raw = await TestSupport.client().spendPost(
            ["v1", "runs", "phase-a"], body: Data("{}".utf8), idempotencyKey: "KEY-1")
        #expect(raw == .http(status: 202, body: Data(#"{"run_id":"tg-1000","outcome":"started"}"#.utf8)))
        let request = try #require(StubURLProtocol.requests.first)
        #expect(request.httpMethod == "POST")
        #expect(request.url?.path == "/v1/runs/phase-a")
        #expect(request.value(forHTTPHeaderField: "Idempotency-Key") == "KEY-1")
        #expect(request.value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
        #expect(request.value(forHTTPHeaderField: "CF-Access-Client-Id") == "id-123")
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
        #expect(request.timeoutInterval == 90)
    }

    @Test func spendPostReportsTransportFailureWithoutThrowing() async {
        StubURLProtocol.install { _ in (-1, [:], Data()) }
        let raw = await TestSupport.client().spendPost(
            ["v1", "runs", "phase-a"], body: Data("{}".utf8), idempotencyKey: "K")
        guard case .transport = raw else {
            Issue.record("expected .transport, got \(raw)")
            return
        }
    }

    @Test func spendPostReturnsErrorStatusesRaw() async {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.errorConflict, status: 409) }
        let raw = await TestSupport.client().spendPost(
            ["v1", "runs", "x", "confirm"], body: Data("{}".utf8), idempotencyKey: "K")
        #expect(raw == .http(status: 409, body: Data(Fixtures.errorConflict.utf8)))
    }

    @Test func getWithQueryAppendsItems() async throws {
        StubURLProtocol.install { _ in TestSupport.json(#"{"ok":true}"#) }
        struct OK: Decodable, Sendable { let ok: Bool }
        _ = try await TestSupport.client().get(
            OK.self, query: [URLQueryItem(name: "force", value: "1")], "v1", "runs", "tg-1", "rent-panel")
        let url = try #require(StubURLProtocol.requests.first?.url)
        #expect(url.path == "/v1/runs/tg-1/rent-panel")
        #expect(url.query == "force=1")
    }
}
}
