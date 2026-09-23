import Foundation
import Testing
@testable import MotionKit

@Suite struct IdempotencyLedgerTests {
    private func ledger() -> IdempotencyLedger {
        IdempotencyLedger(root: FileManager.default.temporaryDirectory
            .appending(component: "ledger-\(UUID().uuidString)"))
    }

    private let entry = SpendLedgerEntry(
        key: "K1", intent: .phaseA, label: "Try-on preview · 1 job",
        createdAt: Date(timeIntervalSince1970: 1_790_000_000))

    @Test func emptyLedgerLoadsNil() throws {
        #expect(try ledger().load() == nil)
    }

    @Test func saveThenLoadRoundTrips() throws {
        let l = ledger()
        try l.save(entry)
        #expect(try l.load() == entry)
    }

    @Test func saveReplacesTheSingleEntry() throws {
        let l = ledger()
        try l.save(entry)
        let second = SpendLedgerEntry(key: "K2", intent: .phaseA, label: "x", createdAt: .now)
        try l.save(second)
        #expect(try l.load()?.key == "K2")
        let files = try FileManager.default.contentsOfDirectory(atPath: l.root.path)
        #expect(files == ["pending-spend.json"])
    }

    @Test func clearRemovesTheEntry() throws {
        let l = ledger()
        try l.save(entry)
        try l.clear()
        #expect(try l.load() == nil)
        try l.clear()   // idempotent
    }

    @Test func migrateEntryRoundTrips() throws {
        let l = ledger()
        let migrate = SpendLedgerEntry(key: "K9", intent: .migrate(toDc: "EU-CZ-1", confirmToken: "tok-abc"),
                                       label: "Migrate volume to EU-CZ-1",
                                       createdAt: Date(timeIntervalSince1970: 1_790_000_000))
        try l.save(migrate)
        #expect(try l.load() == migrate)
    }

    @Test func corruptJournalThrows() throws {
        let l = ledger()
        try FileManager.default.createDirectory(at: l.root, withIntermediateDirectories: true)
        try Data("{not json".utf8).write(to: l.fileURL)
        #expect(throws: (any Error).self) { try l.load() }
    }
}
