import Foundation

public enum UploadFailure: Error, Sendable, Equatable {
    case invalidLocalFile(String)
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
