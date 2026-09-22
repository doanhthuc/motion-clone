import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct MaterialsStoreTests {
    final class RequestGate: @unchecked Sendable {
        let release = DispatchSemaphore(value: 0)
    }

    private let list = #"{"materials":[{"id":"app/coat.png","owner":"app","name":"coat.png","bytes":901,"updated_at":1790000100,"kind":"image"},{"id":"42/driver.mp4","owner":"42","name":"driver.mp4","bytes":12,"updated_at":1790000000,"kind":"video"}]}"#

    @Test func refreshKeepsExistingRowsWhenTheNextRequestFails() async throws {
        StubURLProtocol.install { _ in TestSupport.json(list) }
        let store = MaterialsStore(client: TestSupport.client())
        await store.refresh()

        #expect(store.materials.map(\.id) == ["app/coat.png", "42/driver.mp4"])
        #expect(store.loaded && store.lastSuccess != nil && !store.isStale)

        StubURLProtocol.install { _ in (502, [:], Data("down".utf8)) }
        await store.refresh()
        #expect(store.materials.count == 2)
        #expect(store.isStale)
        #expect(store.errorMessage == "RunPod/Vast didn't answer. Try again.")
    }

    @Test func thumbnailIsAuthenticatedAndCachedByMaterialID() async throws {
        let bytes = Data([1, 2, 3])
        StubURLProtocol.install { request in
            #expect(request.url?.path == "/v1/materials/app/coat.png/thumb")
            return (200, ["Content-Type": "image/png"], bytes)
        }
        let material = try #require(try MotionJSON.decoder.decode(
            MaterialsResponse.self, from: Data(list.utf8)).materials.first)
        let store = MaterialsStore(client: TestSupport.client())

        #expect(await store.thumbnail(for: material) == bytes)
        #expect(await store.thumbnail(for: material) == bytes)
        #expect(StubURLProtocol.requests.count == 1)
        #expect(StubURLProtocol.requests[0].value(forHTTPHeaderField: "Authorization") == "Bearer bearer-789")
    }

    @Test func uploadRefreshesTheListAndRetainsItsWarning() async throws {
        let parent = FileManager.default.temporaryDirectory.appending(component: UUID().uuidString)
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: parent) }
        let file = parent.appending(component: "driver.mp4")
        try Data("abcdefghijkl".utf8).write(to: file)
        StubURLProtocol.install { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/uploads"):
                return TestSupport.json(#"{"upload_id":"abc123","chunk_size":4,"chunks_total":3}"#, status: 201)
            case ("GET", "/v1/uploads/abc123"):
                return TestSupport.json(#"{"upload_id":"abc123","file_name":"driver.mp4","size":12,"chunk_size":4,"chunks_total":3,"received":[]}"#)
            case ("PUT", _):
                return TestSupport.json(#"{"received":1}"#)
            case ("POST", "/v1/uploads/abc123/complete"):
                return TestSupport.json(#"{"material":{"id":"app/driver.mp4","owner":"app","name":"driver.mp4","bytes":12,"updated_at":1790000200,"kind":"video"},"probe":{"kind":"video","width":1080,"height":1920,"duration_s":2,"bitrate_kbps":4200,"size_bytes":12,"warning":"Low bitrate."}}"#, status: 201)
            case ("GET", "/v1/materials"):
                return TestSupport.json(#"{"materials":[{"id":"app/driver.mp4","owner":"app","name":"driver.mp4","bytes":12,"updated_at":1790000200,"kind":"video"}]}"#)
            default:
                return TestSupport.json(#"{"error":{"code":"unexpected","message":"unexpected request"}}"#, status: 500)
            }
        }
        let client = TestSupport.client()
        let uploader = Uploader(
            client: client,
            journal: UploadCheckpointJournal(root: parent.appending(component: "journal")))
        let store = MaterialsStore(client: client, uploader: uploader)

        await store.startUpload(fileURL: file, fileName: "driver.mp4")

        #expect(store.materials.map(\.id) == ["app/driver.mp4"])
        #expect(store.uploadProgress?.phase == .complete)
        #expect(store.warning(for: "app/driver.mp4") == "Low bitrate.")
        #expect(!store.isUploading && store.errorMessage == nil)
    }

    @Test func deleteHonorsOwnershipAndServerOutcomes() async throws {
        StubURLProtocol.install { request in
            if request.httpMethod == "DELETE" { return (204, [:], Data()) }
            return TestSupport.json(list)
        }
        let store = MaterialsStore(client: TestSupport.client())
        await store.refresh()
        let owned = try #require(store.materials.first)
        await store.delete(owned)
        #expect(store.materials.map(\.id) == ["42/driver.mp4"])

        let foreign = try #require(store.materials.first)
        StubURLProtocol.install { _ in (204, [:], Data()) }
        await store.delete(foreign)
        #expect(StubURLProtocol.requests.isEmpty)
        #expect(store.errorMessage == "Only materials uploaded by this app can be deleted.")
    }

    @Test func aSecondUploadIsRejectedWhileTheFirstIsActive() async throws {
        let parent = FileManager.default.temporaryDirectory.appending(component: UUID().uuidString)
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: parent) }
        let file = parent.appending(component: "driver.mp4")
        try Data("abcd".utf8).write(to: file)
        let gate = RequestGate()
        StubURLProtocol.install { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/uploads"):
                gate.release.wait()
                return TestSupport.json(
                    #"{"upload_id":"abc123","chunk_size":4,"chunks_total":1}"#, status: 201)
            case ("GET", "/v1/uploads/abc123"):
                return TestSupport.json(#"{"upload_id":"abc123","file_name":"driver.mp4","size":4,"chunk_size":4,"chunks_total":1,"received":[]}"#)
            case ("PUT", _):
                return TestSupport.json(#"{"received":1}"#)
            case ("POST", "/v1/uploads/abc123/complete"):
                return TestSupport.json(#"{"material":{"id":"app/driver.mp4","owner":"app","name":"driver.mp4","bytes":4,"updated_at":1790000200,"kind":"video"},"probe":{"kind":"video","width":null,"height":null,"duration_s":null,"bitrate_kbps":null,"size_bytes":4,"warning":""}}"#, status: 201)
            case ("GET", "/v1/materials"):
                return TestSupport.json(#"{"materials":[]}"#)
            default:
                return TestSupport.json(#"{"error":{"code":"unexpected","message":"unexpected request"}}"#, status: 500)
            }
        }
        let client = TestSupport.client()
        let store = MaterialsStore(
            client: client,
            uploader: Uploader(
                client: client,
                journal: UploadCheckpointJournal(root: parent.appending(component: "journal"))))

        let first = Task { await store.startUpload(fileURL: file, fileName: "driver.mp4") }
        while StubURLProtocol.requests.isEmpty { await Task.yield() }
        await store.startUpload(fileURL: file, fileName: "second.mp4")

        #expect(store.isUploading)
        #expect(store.errorMessage == "An upload is already in progress.")
        #expect(StubURLProtocol.requests.count == 1)
        gate.release.signal()
        await first.value
    }

    @Test func deleteConflictKeepsTheRowAndNotFoundRefreshes() async throws {
        StubURLProtocol.install { _ in TestSupport.json(list) }
        let store = MaterialsStore(client: TestSupport.client())
        await store.refresh()
        let owned = try #require(store.materials.first)

        StubURLProtocol.install { _ in
            TestSupport.json(#"{"error":{"code":"in_use","message":"Used by an active draft."}}"#, status: 409)
        }
        await store.delete(owned)
        #expect(store.materials.contains { $0.id == owned.id })
        #expect(store.errorMessage == "Used by an active draft.")

        StubURLProtocol.install { request in
            if request.httpMethod == "DELETE" {
                return TestSupport.json(#"{"error":{"code":"not_found","message":"gone"}}"#, status: 404)
            }
            return TestSupport.json(#"{"materials":[]}"#)
        }
        await store.delete(owned)
        #expect(store.materials.isEmpty)
        #expect(store.errorMessage == nil)
    }

    @Test func failedUploadCanBeExplicitlyDiscarded() async throws {
        let parent = FileManager.default.temporaryDirectory.appending(component: UUID().uuidString)
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: parent) }
        let file = parent.appending(component: "driver.mp4")
        try Data("abcd".utf8).write(to: file)
        let journal = UploadCheckpointJournal(root: parent.appending(component: "journal"))
        StubURLProtocol.install { request in
            switch (request.httpMethod, request.url?.path) {
            case ("POST", "/v1/uploads"):
                return TestSupport.json(
                    #"{"upload_id":"abc123","chunk_size":4,"chunks_total":1}"#, status: 201)
            case ("GET", "/v1/uploads/abc123"):
                return TestSupport.json(#"{"upload_id":"abc123","file_name":"driver.mp4","size":4,"chunk_size":4,"chunks_total":1,"received":[]}"#)
            default:
                return TestSupport.json(
                    #"{"error":{"code":"offline","message":"try again"}}"#, status: 500)
            }
        }
        let client = TestSupport.client()
        let store = MaterialsStore(
            client: client, uploader: Uploader(client: client, journal: journal))

        await store.startUpload(fileURL: file, fileName: "driver.mp4")
        #expect(store.hasPendingUpload)
        #expect(try journal.load() != nil)

        await store.discardPendingUpload()
        #expect(!store.hasPendingUpload)
        #expect(store.uploadProgress == nil && store.errorMessage == nil)
        #expect(try journal.load() == nil)
    }
}
}
