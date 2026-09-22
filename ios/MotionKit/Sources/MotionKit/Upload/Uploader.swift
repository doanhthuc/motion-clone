import Foundation

public enum UploadFailure: Error, Sendable, Equatable {
    case invalidLocalFile(String)
    case uploadInProgress
}

public struct ChunkPlan: Sendable, Equatable {
    public let fileSize: Int64
    public let chunkSize: Int64
    public let count: Int

    public init(fileSize: Int64, chunkSize: Int64) throws {
        guard fileSize > 0, chunkSize > 0 else {
            throw UploadFailure.invalidLocalFile("file and chunk sizes must be positive")
        }
        let quotient = fileSize / chunkSize
        let remainder = fileSize % chunkSize
        let total = quotient + (remainder == 0 ? 0 : 1)
        guard total <= Int64(Int.max) else {
            throw UploadFailure.invalidLocalFile("the file has too many chunks")
        }
        self.fileSize = fileSize
        self.chunkSize = chunkSize
        self.count = Int(total)
    }

    public func offset(of index: Int) throws -> Int64 {
        guard index >= 0, index < count else {
            throw UploadFailure.invalidLocalFile("chunk index is out of range")
        }
        let (offset, overflow) = Int64(index).multipliedReportingOverflow(by: chunkSize)
        guard !overflow else { throw UploadFailure.invalidLocalFile("chunk offset overflow") }
        return offset
    }

    public func length(of index: Int) throws -> Int64 {
        let offset = try offset(of: index)
        return min(chunkSize, fileSize - offset)
    }

    public func bytes(in indices: [Int]) throws -> Int64 {
        var total: Int64 = 0
        for index in Set(indices) {
            let (next, overflow) = total.addingReportingOverflow(try length(of: index))
            guard !overflow else { throw UploadFailure.invalidLocalFile("received bytes overflow") }
            total = next
        }
        return total
    }
}

public enum UploadPhase: Sendable, Equatable {
    case preparing, transferring, processing, complete
}

public struct UploadProgress: Sendable, Equatable {
    public let fileName: String
    public let phase: UploadPhase
    public let bytesSent: Int64
    public let totalBytes: Int64

    public init(fileName: String, phase: UploadPhase, bytesSent: Int64, totalBytes: Int64) {
        self.fileName = fileName
        self.phase = phase
        self.bytesSent = bytesSent
        self.totalBytes = totalBytes
    }
}

public actor Uploader {
    public typealias ProgressHandler = @Sendable (UploadProgress) async -> Void

    private let client: APIClient
    private let journal: UploadCheckpointJournal
    private var isActive = false

    public init(client: APIClient, journal: UploadCheckpointJournal = UploadCheckpointJournal()) {
        self.client = client
        self.journal = journal
    }

    public func hasPendingUpload() -> Bool {
        (try? journal.load()) != nil
    }

    public func clearPendingUpload() throws {
        try journal.clear()
    }

    public func start(fileURL: URL, fileName: String,
                      progress: ProgressHandler) async throws -> UploadCompleteResponse {
        try beginOperation()
        defer { isActive = false }
        guard (try journal.load()) == nil else { throw UploadFailure.uploadInProgress }
        let size = try Self.regularFileSize(fileURL)
        await progress(UploadProgress(
            fileName: fileName, phase: .preparing, bytesSent: 0, totalBytes: size))
        let localName = try journal.stageSource(from: fileURL, fileName: fileName)
        let opened = try await client.post(
            UploadOpenResponse.self, body: UploadOpenRequest(fileName: fileName, size: size),
            "v1", "uploads")
        let plan = try ChunkPlan(fileSize: size, chunkSize: opened.chunkSize)
        guard opened.chunksTotal == plan.count else {
            throw UploadFailure.invalidLocalFile("server returned inconsistent chunk geometry")
        }
        let checkpoint = UploadCheckpoint(
            uploadId: opened.uploadId, fileName: fileName,
            fileSize: size, localFileName: localName)
        try journal.save(checkpoint)
        return try await transfer(checkpoint, progress: progress, allowIncompleteRetry: true)
    }

    public func resume(progress: ProgressHandler) async throws -> UploadCompleteResponse? {
        try beginOperation()
        defer { isActive = false }
        guard let checkpoint = try journal.load() else { return nil }
        return try await transfer(checkpoint, progress: progress, allowIncompleteRetry: true)
    }

    private func transfer(_ checkpoint: UploadCheckpoint, progress: ProgressHandler,
                          allowIncompleteRetry: Bool) async throws -> UploadCompleteResponse {
        let fileURL = journal.sourceURL(for: checkpoint)
        guard try Self.regularFileSize(fileURL) == checkpoint.fileSize else {
            throw UploadFailure.invalidLocalFile("the saved upload file changed; select it again")
        }
        let status: UploadStatus
        do {
            status = try await client.get(
                UploadStatus.self, "v1", "uploads", checkpoint.uploadId)
        } catch let error {
            if case .server(status: 404, _, _) = error {
                try? journal.clear()
                throw UploadFailure.invalidLocalFile(
                    "The saved upload expired; select the file again.")
            }
            throw error
        }
        if let material = status.material, let probe = status.probe {
            let completed = UploadCompleteResponse(material: material, probe: probe)
            await progress(UploadProgress(
                fileName: checkpoint.fileName, phase: .complete,
                bytesSent: checkpoint.fileSize, totalBytes: checkpoint.fileSize))
            try journal.clear()
            return completed
        }
        guard status.uploadId == checkpoint.uploadId,
              status.size == checkpoint.fileSize else {
            throw UploadFailure.invalidLocalFile("server upload metadata does not match the saved file")
        }
        let plan = try ChunkPlan(fileSize: checkpoint.fileSize, chunkSize: status.chunkSize)
        guard status.chunksTotal == plan.count else {
            throw UploadFailure.invalidLocalFile("server returned inconsistent chunk geometry")
        }
        let received = Set(status.received)
        var sent = try plan.bytes(in: Array(received))
        await progress(UploadProgress(
            fileName: checkpoint.fileName, phase: .transferring,
            bytesSent: sent, totalBytes: checkpoint.fileSize))
        for index in 0..<plan.count where !received.contains(index) {
            let data = try Self.readChunk(fileURL, plan: plan, index: index)
            try await client.put(
                data: data, "v1", "uploads", checkpoint.uploadId, "chunks", String(index))
            sent += Int64(data.count)
            await progress(UploadProgress(
                fileName: checkpoint.fileName, phase: .transferring,
                bytesSent: sent, totalBytes: checkpoint.fileSize))
        }
        await progress(UploadProgress(
            fileName: checkpoint.fileName, phase: .processing,
            bytesSent: checkpoint.fileSize, totalBytes: checkpoint.fileSize))
        do {
            let completed = try await client.post(
                UploadCompleteResponse.self, "v1", "uploads", checkpoint.uploadId, "complete")
            await progress(UploadProgress(
                fileName: checkpoint.fileName, phase: .complete,
                bytesSent: checkpoint.fileSize, totalBytes: checkpoint.fileSize))
            try journal.clear()
            return completed
        } catch let error as APIError {
            if allowIncompleteRetry,
               case .server(status: 409, code: "incomplete", _) = error {
                return try await transfer(
                    checkpoint, progress: progress, allowIncompleteRetry: false)
            }
            throw error
        }
    }

    private static func regularFileSize(_ url: URL) throws -> Int64 {
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
        guard values.isRegularFile == true, let size = values.fileSize, size > 0 else {
            throw UploadFailure.invalidLocalFile("select a non-empty regular file")
        }
        return Int64(size)
    }

    private func beginOperation() throws {
        guard !isActive else { throw UploadFailure.uploadInProgress }
        isActive = true
    }

    private static func readChunk(_ url: URL, plan: ChunkPlan, index: Int) throws -> Data {
        let length = try plan.length(of: index)
        guard length <= Int64(Int.max) else {
            throw UploadFailure.invalidLocalFile("chunk is too large for this device")
        }
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        try handle.seek(toOffset: UInt64(try plan.offset(of: index)))
        let data = try handle.read(upToCount: Int(length)) ?? Data()
        guard data.count == Int(length) else {
            throw UploadFailure.invalidLocalFile("the upload file ended before the chunk was read")
        }
        return data
    }
}
