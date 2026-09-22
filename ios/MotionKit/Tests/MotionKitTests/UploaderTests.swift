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
