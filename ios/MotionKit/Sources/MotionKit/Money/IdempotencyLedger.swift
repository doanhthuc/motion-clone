import Foundation

public struct SpendLedgerEntry: Codable, Sendable, Equatable {
    public let key: String
    public let intent: SpendIntent
    /// What the user tapped, e.g. "Confirm · RTX 5090 · ~$1.40".
    public let label: String
    public let createdAt: Date

    public init(key: String, intent: SpendIntent, label: String, createdAt: Date) {
        self.key = key
        self.intent = intent
        self.label = label
        self.createdAt = createdAt
    }
}

/// The spend request the phone has sent (or is about to send) and has not
/// yet had a definitive answer for. At most one: there is one run slot.
///
/// Written — synced to disk — BEFORE the request leaves, so an app killed
/// mid-request can resend the same key on the next launch instead of
/// minting a new one (parent design §4).
public struct IdempotencyLedger: Sendable {
    public let root: URL

    public init() { self.root = Self.defaultRoot() }
    public init(root: URL) { self.root = root }

    public var fileURL: URL { root.appending(component: "pending-spend.json") }

    /// Throws when the journal exists but cannot be decoded — the caller must
    /// treat that as "an earlier spend may be in flight, key unknown".
    public func load() throws -> SpendLedgerEntry? {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .secondsSince1970
        return try decoder.decode(SpendLedgerEntry.self, from: Data(contentsOf: fileURL))
    }

    public func save(_ entry: SpendLedgerEntry) throws {
        let fm = FileManager.default
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .secondsSince1970
        let data = try encoder.encode(entry)
        let temporary = root.appending(component: ".pending-spend.\(UUID().uuidString).tmp")
        guard fm.createFile(atPath: temporary.path, contents: nil) else {
            throw CocoaError(.fileWriteUnknown)
        }
        do {
            let handle = try FileHandle(forWritingTo: temporary)
            defer { try? handle.close() }
            try handle.write(contentsOf: data)
            try handle.synchronize()
        } catch {
            try? fm.removeItem(at: temporary)
            throw error
        }
        do {
            if fm.fileExists(atPath: fileURL.path) {
                _ = try fm.replaceItemAt(fileURL, withItemAt: temporary)
            } else {
                try fm.moveItem(at: temporary, to: fileURL)
            }
        } catch {
            try? fm.removeItem(at: temporary)
            throw error
        }
    }

    public func clear() throws {
        guard FileManager.default.fileExists(atPath: fileURL.path) else { return }
        try FileManager.default.removeItem(at: fileURL)
    }

    private static func defaultRoot() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        return base.appending(path: "Motion/Spend", directoryHint: .isDirectory)
    }
}
