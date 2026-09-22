import Foundation
import Testing
@testable import MotionKit

@Suite struct UploaderTests {
    @Test func nonMultipleOf32MiBHasShortFinalChunk() throws {
        let plan = try ChunkPlan(fileSize: 33_554_432 + 17, chunkSize: 33_554_432)
        #expect(plan.count == 2)
        #expect(try plan.offset(of: 1) == 33_554_432)
        #expect(try plan.length(of: 0) == 33_554_432)
        #expect(try plan.length(of: 1) == 17)
        #expect(try plan.bytes(in: [0]) == 33_554_432)
        #expect(try plan.bytes(in: [0, 0, 1]) == 33_554_449)
    }

    @Test func chunkPlanRejectsInvalidGeometryAndIndices() {
        #expect(throws: UploadFailure.self) { try ChunkPlan(fileSize: 0, chunkSize: 4) }
        #expect(throws: UploadFailure.self) { try ChunkPlan(fileSize: 4, chunkSize: 0) }
        #expect(throws: UploadFailure.self) {
            let plan = try ChunkPlan(fileSize: 4, chunkSize: 4)
            _ = try plan.length(of: 1)
        }
        #expect(throws: UploadFailure.self) {
            let plan = try ChunkPlan(fileSize: 4, chunkSize: 4)
            _ = try plan.bytes(in: [-1])
        }
    }

    @Test func uploadProgressKeepsByteCountsSeparateFromPhase() {
        let progress = UploadProgress(
            fileName: "driver.mp4", phase: .transferring,
            bytesSent: 4, totalBytes: 12)
        #expect(progress.phase == .transferring)
        #expect(progress.bytesSent == 4)
        #expect(progress.totalBytes == 12)
    }
}

extension URLProtocolTests {
@Suite struct UploaderNetworkTests {
    final class LockedCounter: @unchecked Sendable {
        private let lock = NSLock()
        private var value = 0
        func increment() -> Int { lock.withLock { value += 1; return value } }
        var current: Int { lock.withLock { value } }
    }

    actor ProgressRecorder {
        var values: [UploadProgress] = []
        func append(_ value: UploadProgress) { values.append(value) }
    }

    private func source(_ contents: String = "abcdefghijkl") throws -> (URL, URL) {
        let parent = FileManager.default.temporaryDirectory.appending(component: UUID().uuidString)
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        let file = parent.appending(component: "driver.mp4")
        try Data(contents.utf8).write(to: file)
        return (parent, file)
    }

    private func response(_ request: URLRequest, received: [Int] = [0])
        -> (Int, [String: String], Data) {
        let path = request.url?.path ?? ""
        if request.httpMethod == "POST", path == "/v1/uploads" {
            return TestSupport.json(
                #"{"upload_id":"abc123","chunk_size":4,"chunks_total":3}"#, status: 201)
        }
        if request.httpMethod == "GET", path == "/v1/uploads/abc123" {
            let got = received.map(String.init).joined(separator: ",")
            return TestSupport.json(
                #"{"upload_id":"abc123","file_name":"driver.mp4","size":12,"chunk_size":4,"chunks_total":3,"received":[\#(got)]}"#)
        }
        if request.httpMethod == "PUT" { return TestSupport.json(#"{"received":1}"#) }
        if request.httpMethod == "POST", path.hasSuffix("/complete") {
            return TestSupport.json(#"{"material":{"id":"app/driver.mp4","owner":"app","name":"driver.mp4","bytes":12,"updated_at":1790000200,"kind":"video"},"probe":{"kind":"video","width":null,"height":null,"duration_s":null,"bitrate_kbps":null,"size_bytes":12,"warning":""}}"#, status: 201)
        }
        return TestSupport.json(#"{"error":{"code":"not_found","message":"unexpected"}}"#, status: 404)
    }

    @Test func startSendsOnlyMissingChunksThenCompletes() async throws {
        let (parent, file) = try source()
        defer { try? FileManager.default.removeItem(at: parent) }
        let journalRoot = parent.appending(component: "journal")
        let journal = UploadCheckpointJournal(root: journalRoot)
        StubURLProtocol.install { request in
            if request.httpMethod == "PUT" {
                #expect(FileManager.default.fileExists(
                    atPath: journalRoot.appending(component: "checkpoint.json").path))
            }
            return response(request)
        }
        let recorder = ProgressRecorder()
        let result = try await Uploader(client: TestSupport.client(), journal: journal).start(
            fileURL: file, fileName: "driver.mp4") { value in await recorder.append(value) }

        #expect(result.material.id == "app/driver.mp4")
        let requests = StubURLProtocol.requests
        #expect(requests.map { "\($0.httpMethod ?? "") \($0.url?.path ?? "")" } == [
            "POST /v1/uploads", "GET /v1/uploads/abc123",
            "PUT /v1/uploads/abc123/chunks/1", "PUT /v1/uploads/abc123/chunks/2",
            "POST /v1/uploads/abc123/complete",
        ])
        #expect(requests[2].httpBody == Data("efgh".utf8))
        #expect(requests[3].httpBody == Data("ijkl".utf8))
        let progress = await recorder.values
        #expect(progress.contains { $0.phase == .transferring && $0.bytesSent == 4 })
        #expect(progress.contains { $0.phase == .processing })
        #expect(progress.last?.phase == .complete)
        #expect(try journal.load() == nil)
    }

    @Test func recreatedUploaderResumesCheckpointAfterChunkFailure() async throws {
        let (parent, file) = try source()
        defer { try? FileManager.default.removeItem(at: parent) }
        let journal = UploadCheckpointJournal(root: parent.appending(component: "journal"))
        StubURLProtocol.install { request in
            if request.httpMethod == "PUT" {
                return TestSupport.json(
                    #"{"error":{"code":"offline","message":"try again"}}"#, status: 500)
            }
            return response(request, received: [])
        }
        await #expect(throws: APIError.self) {
            _ = try await Uploader(client: TestSupport.client(), journal: journal).start(
                fileURL: file, fileName: "driver.mp4") { _ in }
        }
        #expect(try journal.load()?.uploadId == "abc123")

        StubURLProtocol.install { request in response(request, received: [0]) }
        let result = try await Uploader(client: TestSupport.client(), journal: journal).resume { _ in }
        #expect(result?.material.name == "driver.mp4")
        #expect(!StubURLProtocol.requests.contains { $0.url?.path.hasSuffix("/chunks/0") == true })
        #expect(try journal.load() == nil)
    }

    @Test func resumeRefusesChangedFileBeforeNetwork() async throws {
        let (parent, file) = try source("abcd")
        defer { try? FileManager.default.removeItem(at: parent) }
        let journal = UploadCheckpointJournal(root: parent.appending(component: "journal"))
        try FileManager.default.createDirectory(at: journal.root, withIntermediateDirectories: true)
        let checkpoint = UploadCheckpoint(
            uploadId: "abc123", fileName: "driver.mp4", fileSize: 12,
            localFileName: "source.mp4")
        try journal.save(checkpoint)
        try FileManager.default.copyItem(at: file, to: journal.sourceURL(for: checkpoint))
        StubURLProtocol.install { _ in TestSupport.json("{}") }

        await #expect(throws: UploadFailure.self) {
            _ = try await Uploader(client: TestSupport.client(), journal: journal).resume { _ in }
        }
        #expect(StubURLProtocol.requests.isEmpty)
    }

    @Test func uploadStatus404ClearsExpiredCheckpoint() async throws {
        let (parent, file) = try source()
        defer { try? FileManager.default.removeItem(at: parent) }
        let journal = UploadCheckpointJournal(root: parent.appending(component: "journal"))
        StubURLProtocol.install { request in
            if request.httpMethod == "GET" {
                return TestSupport.json(
                    #"{"error":{"code":"not_found","message":"no such upload"}}"#, status: 404)
            }
            return response(request, received: [])
        }
        await #expect(throws: APIError.self) {
            _ = try await Uploader(client: TestSupport.client(), journal: journal).start(
                fileURL: file, fileName: "driver.mp4") { _ in }
        }
        #expect(try journal.load() == nil)
    }

    @Test func incompleteCompletionGetsOneStatusAndMissingChunkRetry() async throws {
        let (parent, file) = try source()
        defer { try? FileManager.default.removeItem(at: parent) }
        let counter = LockedCounter()
        StubURLProtocol.install { request in
            if request.httpMethod == "POST", request.url?.path.hasSuffix("/complete") == true,
               counter.increment() == 1 {
                return TestSupport.json(
                    #"{"error":{"code":"incomplete","message":"check status"}}"#, status: 409)
            }
            return response(request, received: [0, 1])
        }
        let journal = UploadCheckpointJournal(root: parent.appending(component: "journal"))
        let result = try await Uploader(client: TestSupport.client(), journal: journal).start(
            fileURL: file, fileName: "driver.mp4") { _ in }
        #expect(result.material.name == "driver.mp4")
        #expect(counter.current == 2)
        #expect(StubURLProtocol.requests.filter { $0.httpMethod == "GET" }.count == 2)
    }
}
}
