import Foundation
import Testing
@testable import MotionKit

@Suite struct UploadCheckpointTests {
    @Test func journalSurvivesANewInstanceAndClearsItsDirectory() throws {
        let parent = FileManager.default.temporaryDirectory.appending(component: UUID().uuidString)
        let root = parent.appending(component: "current")
        defer { try? FileManager.default.removeItem(at: parent) }
        let checkpoint = UploadCheckpoint(
            uploadId: "abc123", fileName: "driver.mp4", fileSize: 12,
            localFileName: "source.mp4")

        let first = UploadCheckpointJournal(root: root)
        try first.save(checkpoint)
        let source = first.sourceURL(for: checkpoint)
        try Data("abcdefghijkl".utf8).write(to: source)

        let recreated = UploadCheckpointJournal(root: root)
        #expect(try recreated.load() == checkpoint)
        #expect(recreated.sourceURL(for: checkpoint) == source)
        #expect(source.deletingLastPathComponent().standardizedFileURL.path ==
                root.standardizedFileURL.path)

        try recreated.clear()
        #expect(try recreated.load() == nil)
        #expect(!FileManager.default.fileExists(atPath: root.path))
    }

    @Test func savingAgainAtomicallyReplacesTheCheckpoint() throws {
        let root = FileManager.default.temporaryDirectory.appending(component: UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let journal = UploadCheckpointJournal(root: root)
        try journal.save(UploadCheckpoint(
            uploadId: "first", fileName: "a.png", fileSize: 1, localFileName: "source.png"))
        let second = UploadCheckpoint(
            uploadId: "second", fileName: "b.mov", fileSize: 2, localFileName: "source.mov")
        try journal.save(second)
        #expect(try journal.load() == second)
    }
}
