import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct DraftStoreTests {
    final class DraftResponses: @unchecked Sendable {
        private let lock = NSLock()
        private var values: [String]

        init(_ values: [String]) {
            self.values = values
        }

        func next() -> String {
            lock.withLock { values.removeFirst() }
        }
    }

    final class RequestGate: @unchecked Sendable {
        let release = DispatchSemaphore(value: 0)
    }

    @Test func loadFetchesCatalogAndDraft() async {
        StubURLProtocol.install { request in
            request.url?.path == "/v1/pipelines"
                ? TestSupport.json(Fixtures.pipelines)
                : TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())

        await store.load()

        #expect(store.catalog.count == 2)
        #expect(store.draft?.generation == 4)
        #expect(store.selectedPipeline?.id == "tryon-motion-enhance")
        #expect(store.loaded && store.lastSuccess != nil && !store.isStale)
    }

    @Test func everyMutationInstallsTheReturnedDraft() async {
        let responses = DraftResponses([
            Fixtures.draft,
            draftResponse(generation: 5, dropped: ["outfit"]),
            draftResponse(generation: 6),
            draftResponse(generation: 7),
            draftResponse(generation: 8),
            draftResponse(generation: 9),
            draftResponse(generation: 10),
        ])
        StubURLProtocol.install { request in
            if request.url?.path == "/v1/pipelines" {
                return TestSupport.json(Fixtures.pipelines)
            }
            return TestSupport.json(responses.next())
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()

        await store.selectPipeline("motion-enhance")
        #expect(store.draft?.generation == 5)
        #expect(store.message == "Removed incompatible slots: outfit.")
        store.dismissMessage()
        #expect(store.message == nil)
        await store.selectProvider("gemini")
        #expect(store.draft?.generation == 6)
        await store.assign(role: "driver", materialID: "app/dance.mp4")
        #expect(store.draft?.generation == 7)
        await store.addToBatch()
        #expect(store.draft?.generation == 8)
        await store.dropFromBatch("abc123def0")
        #expect(store.draft?.generation == 9)
        await store.clear()

        #expect(store.draft?.generation == 10)
        #expect(StubURLProtocol.requests.map(\.httpMethod) ==
                ["GET", "GET", "PATCH", "PATCH", "PATCH", "POST", "DELETE", "POST"])
        #expect(StubURLProtocol.requests.map { $0.url?.path } ==
                ["/v1/pipelines", "/v1/draft", "/v1/draft", "/v1/draft", "/v1/draft",
                 "/v1/draft/add-to-batch", "/v1/draft/batch/abc123def0", "/v1/draft/clear"])
        #expect(StubURLProtocol.requests.map(\.timeoutInterval) ==
                [30, 30, 30, 30, 95, 30, 30, 30])
    }

    @Test func rejectsASecondMutationWhileTheFirstWriteIsActive() async {
        let gate = RequestGate()
        StubURLProtocol.install { request in
            if request.httpMethod == "PATCH" {
                gate.release.wait()
            }
            return request.url?.path == "/v1/pipelines"
                ? TestSupport.json(Fixtures.pipelines)
                : TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()

        let first = Task { await store.selectPipeline("motion-enhance") }
        while StubURLProtocol.requests.filter({ $0.httpMethod == "PATCH" }).isEmpty {
            await Task.yield()
        }
        #expect(store.isMutating)
        await store.selectProvider("qwen-max")

        #expect(StubURLProtocol.requests.filter { $0.httpMethod == "PATCH" }.count == 1)
        #expect(store.message == "Another draft change is still in progress.")
        gate.release.signal()
        await first.value
        #expect(!store.isMutating)
    }

    @Test func validationInstallsTheNestedDraftAndEstimate() async {
        StubURLProtocol.install { request in
            switch request.url?.path {
            case "/v1/pipelines": TestSupport.json(Fixtures.pipelines)
            case "/v1/draft/validate": TestSupport.json(Fixtures.validatedDraft)
            default: TestSupport.json(Fixtures.draft)
            }
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()

        await store.validate()

        #expect(store.draft?.generation == 8)
        #expect(store.draft?.estimateMin == 48)
        #expect(store.isReady && !store.validationWasStale)
        #expect(StubURLProtocol.requests.last?.timeoutInterval == 95)
    }

    @Test func staleValidationInstallsTheNestedDraftWithoutReportingReady() async {
        let staleResponse = Fixtures.validatedDraft.replacingOccurrences(
            of: "\"stale\":false", with: "\"stale\":true")
        StubURLProtocol.install { request in
            switch request.url?.path {
            case "/v1/pipelines": TestSupport.json(Fixtures.pipelines)
            case "/v1/draft/validate": TestSupport.json(staleResponse)
            default: TestSupport.json(Fixtures.draft)
            }
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()

        await store.validate()

        #expect(store.draft?.generation == 8)
        #expect(store.validationWasStale)
        #expect(!store.isReady)
        #expect(store.message == "The draft changed during validation. Validate it again.")
    }

    @Test func authoritativeRefreshClearsAStaleValidationLatch() async throws {
        let staleResponse = Fixtures.validatedDraft.replacingOccurrences(
            of: "\"stale\":false", with: "\"stale\":true")
        let drafts = DraftResponses([
            Fixtures.draft,
            try validationDraft(validated: true),
        ])
        StubURLProtocol.install { request in
            switch request.url?.path {
            case "/v1/pipelines": TestSupport.json(Fixtures.pipelines)
            case "/v1/draft/validate": TestSupport.json(staleResponse)
            default: TestSupport.json(drafts.next())
            }
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()

        await store.validate()
        #expect(store.validationWasStale)
        #expect(!store.isReady)

        await store.refresh()

        #expect(store.draft?.validated == true)
        #expect(!store.validationWasStale)
        #expect(store.isReady)
    }

    @Test func validationConflictKeepsTheLastDraftAndShowsTheServerMessage() async {
        StubURLProtocol.install { request in
            request.url?.path == "/v1/pipelines"
                ? TestSupport.json(Fixtures.pipelines)
                : TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()
        StubURLProtocol.install { _ in
            TestSupport.json(
                #"{"error":{"code":"missing_slots","message":"Assign driver before validating."}}"#,
                status: 422)
        }

        await store.validate()

        #expect(store.draft?.generation == 4)
        #expect(store.error?.userMessage == "Assign driver before validating.")
        #expect(store.message == "Assign driver before validating.")
        #expect(StubURLProtocol.requests.map(\.httpMethod) == ["POST"])
    }

    @Test func invalidValidationReconcilesBeforeReleasingTheGateAndKeepsTheServerError() async throws {
        let gate = RequestGate()
        let drafts = DraftResponses([
            try validationDraft(validated: true),
            try validationDraft(validated: false),
        ])
        StubURLProtocol.install { request in
            switch (request.httpMethod, request.url?.path) {
            case (_, "/v1/pipelines"):
                return TestSupport.json(Fixtures.pipelines)
            case ("POST", "/v1/draft/validate"):
                return TestSupport.json(
                    #"{"error":{"code":"invalid","message":"Driver video is unreadable."}}"#,
                    status: 422)
            case ("GET", "/v1/draft"):
                let response = drafts.next()
                if StubURLProtocol.requests.filter({
                    $0.httpMethod == "GET" && $0.url?.path == "/v1/draft"
                }).count == 2 {
                    gate.release.wait()
                }
                return TestSupport.json(response)
            default:
                return TestSupport.json(#"{"error":{"code":"unexpected","message":"unexpected request"}}"#, status: 500)
            }
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()
        #expect(store.isReady)

        let validation = Task { await store.validate() }
        for _ in 0..<1_000 {
            if StubURLProtocol.requests.count == 4 { break }
            await Task.yield()
        }

        #expect(StubURLProtocol.requests.map(\.httpMethod) == ["GET", "GET", "POST", "GET"])
        #expect(store.isValidating)
        #expect(store.draft?.validated == true)
        gate.release.signal()
        await validation.value

        #expect(!store.isValidating)
        #expect(store.draft?.validated == false)
        #expect(!store.isReady)
        #expect(store.error == .server(
            status: 422, code: "invalid", message: "Driver video is unreadable."))
        // `error` keeps the raw text; only its rendering moved (spec §5).
        #expect(store.message == "This draft didn't pass validation, so it can't run yet.")
        #expect(store.error?.detailMessage == "Driver video is unreadable.")
    }

    @Test func transportFailureReconcilesBeforeValidationCompletes() async {
        TransportDuringWriteProtocol.install(failingMethod: "POST")
        let store = DraftStore(client: TransportDuringWriteProtocol.client())
        await store.load()

        let validation = Task { await store.validate() }
        for _ in 0..<1_000 {
            if TransportDuringWriteProtocol.requests.count == 4 { break }
            await Task.yield()
        }

        #expect(TransportDuringWriteProtocol.requests.map(\.httpMethod) == ["GET", "GET", "POST", "GET"])
        #expect(store.isValidating)
        TransportDuringWriteProtocol.releaseRefresh()
        await validation.value
        #expect(!store.isValidating)
        #expect(store.draft?.generation == 6)
        #expect(store.error?.isOffline == true)
        #expect(store.message == store.error?.userMessage)
    }

    @Test func failedRefreshKeepsTheLastDraftAndMarksItStale() async {
        StubURLProtocol.install { request in
            request.url?.path == "/v1/pipelines"
                ? TestSupport.json(Fixtures.pipelines)
                : TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()
        StubURLProtocol.install { _ in
            TestSupport.json(#"{"error":{"code":"offline","message":"unavailable"}}"#, status: 502)
        }

        await store.refresh()

        #expect(store.draft?.generation == 4)
        #expect(store.isStale)
        #expect(store.message == "RunPod/Vast didn't answer. Try again.")
    }

    @Test func transportFailureRefreshesBeforeTheMutationCompletes() async {
        TransportDuringWriteProtocol.install(failingMethod: "PATCH")
        let store = DraftStore(client: TransportDuringWriteProtocol.client())
        await store.load()

        let mutation = Task { await store.selectProvider("qwen-max") }
        for _ in 0..<1_000 {
            if TransportDuringWriteProtocol.requests.count == 4 { break }
            await Task.yield()
        }

        #expect(TransportDuringWriteProtocol.requests.map(\.httpMethod) == ["GET", "GET", "PATCH", "GET"])
        #expect(store.isMutating)
        TransportDuringWriteProtocol.releaseRefresh()
        await mutation.value
        #expect(!store.isMutating)
        #expect(store.draft?.generation == 6)
        #expect(store.error?.isOffline == true)
    }

    @Test func missingMaterialRefreshesTheDraftAndKeepsTheServerMessage() async {
        StubURLProtocol.install { request in
            request.url?.path == "/v1/pipelines"
                ? TestSupport.json(Fixtures.pipelines)
                : TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()
        StubURLProtocol.install { request in
            if request.httpMethod == "PATCH" {
                return TestSupport.json(
                    #"{"error":{"code":"material_missing","message":"The material was removed elsewhere."}}"#,
                    status: 404)
            }
            return TestSupport.json(draftResponse(generation: 6))
        }

        await store.assign(role: "driver", materialID: "app/dance.mp4")

        #expect(store.needsMaterialsRefresh)
        #expect(store.draft?.generation == 6)
        #expect(store.message == "The material was removed elsewhere.")
        #expect(StubURLProtocol.requests.map(\.httpMethod) == ["PATCH", "GET"])
        store.acknowledgeMaterialsRefresh()
        #expect(!store.needsMaterialsRefresh)
    }

    @Test func catalogContractErrorKeepsTheReturnedDraft() async {
        StubURLProtocol.install { request in
            if request.url?.path == "/v1/pipelines" {
                return TestSupport.json(#"{"pipelines":[]}"#)
            }
            return TestSupport.json(Fixtures.draft)
        }
        let store = DraftStore(client: TestSupport.client())

        await store.load()

        #expect(store.draft?.generation == 4)
        #expect(store.error?.userMessage ==
                "The server answered in a shape this app doesn't know (The pipeline catalog is empty.). Update the app.")
    }

    @Test func applySendsSlotsAndSeedAndReportsTheResult() async throws {
        // Verbatim from `drafts.py` `patch` (`not_local`, 422) — the copy the user sees.
        let refusal = "tryon_seed only applies to a local try-on provider (gemini or qwen-max) — switch the provider first"
        StubURLProtocol.install { request in
            switch (request.httpMethod, request.url?.path) {
            case ("GET", "/v1/pipelines"): return TestSupport.json(Fixtures.pipelines)
            case ("PATCH", _):
                let body = String(decoding: request.httpBody ?? Data(), as: UTF8.self)
                return body.contains("bad")
                    ? TestSupport.json(
                        #"{"error":{"code":"not_local","message":"\#(refusal)"}}"#, status: 422)
                    : TestSupport.json(Fixtures.draft)
            default: return TestSupport.json(Fixtures.draft)
            }
        }
        let store = DraftStore(client: TestSupport.client())
        await store.load()

        let ok = await store.apply(DraftPatch(slots: ["outfit": "app/o.png"], seed: .set("s1")))
        #expect(ok)
        let sent = try #require(StubURLProtocol.requests.last)
        #expect(sent.timeoutInterval == 95)
        let body = try #require(JSONSerialization.jsonObject(with: sent.httpBody ?? Data()) as? [String: Any])
        #expect(body["tryon_seed"] as? String == "s1")
        #expect((body["slots"] as? [String: Any])?["outfit"] as? String == "app/o.png")

        // `.keep` must omit the key outright — sending `null` here would clear a seed the
        // caller never mentioned, the one encoding mistake a cross build cannot absorb.
        let kept = await store.apply(DraftPatch(slots: ["outfit": "app/o2.png"]))
        #expect(kept)
        let keepBody = try #require(
            JSONSerialization.jsonObject(
                with: StubURLProtocol.requests.last?.httpBody ?? Data()) as? [String: Any])
        #expect(!keepBody.keys.contains("tryon_seed"))
        #expect((keepBody["slots"] as? [String: Any])?["outfit"] as? String == "app/o2.png")

        let refused = await store.apply(DraftPatch(seed: .set("bad")))
        #expect(!refused)
        #expect(store.message == refusal)
    }

}
}

private func draftResponse(generation: Int, dropped: [String] = []) -> String {
    var result = Fixtures.draft.replacingOccurrences(
        of: "\"generation\":4", with: "\"generation\":\(generation)")
    if !dropped.isEmpty {
        let encoded = dropped.map { "\"\($0)\"" }.joined(separator: ",")
        result = result.replacingOccurrences(
            of: "\"estimate_min\":null}",
            with: "\"estimate_min\":null,\"dropped\":[\(encoded)]}")
    }
    return result
}

private func validationDraft(validated: Bool) throws -> String {
    let data = Data(Fixtures.validatedDraft.utf8)
    guard let envelope = try JSONSerialization.jsonObject(with: data) as? [String: Any],
          var draft = envelope["draft"] as? [String: Any] else {
        throw APIError.decoding("invalid validation fixture")
    }
    draft["validated"] = validated
    return String(decoding: try JSONSerialization.data(withJSONObject: draft), as: UTF8.self)
}

private final class TransportDuringWriteProtocol: URLProtocol, @unchecked Sendable {
    private static let lock = NSLock()
    private static let refreshRelease = DispatchSemaphore(value: 0)
    nonisolated(unsafe) private static var recorded: [URLRequest] = []
    nonisolated(unsafe) private static var failedMethod: String?

    static func install(failingMethod: String) {
        lock.withLock {
            recorded = []
            self.failedMethod = failingMethod
        }
    }

    static var requests: [URLRequest] { lock.withLock { recorded } }

    static func releaseRefresh() {
        refreshRelease.signal()
    }

    static func client() -> APIClient {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [TransportDuringWriteProtocol.self]
        return APIClient(credentials: TestSupport.credentials, session: URLSession(configuration: configuration))
    }

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        let state = Self.lock.withLock {
            Self.recorded.append(request)
            let didFailWrite = Self.failedMethod == nil
            return (
                shouldBlockRefresh: didFailWrite && request.httpMethod == "GET" && request.url?.path == "/v1/draft",
                didFailWrite: didFailWrite)
        }
        if request.httpMethod == Self.failedMethod {
            Self.lock.withLock { Self.failedMethod = nil }
            client?.urlProtocol(self, didFailWithError: URLError(.notConnectedToInternet))
            return
        }
        if state.shouldBlockRefresh {
            Self.refreshRelease.wait()
        }
        let body = request.url?.path == "/v1/pipelines"
            ? Fixtures.pipelines
            : draftResponse(generation: state.didFailWrite ? 6 : 4)
        let response = HTTPURLResponse(
            url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1",
            headerFields: ["Content-Type": "application/json"])!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}
