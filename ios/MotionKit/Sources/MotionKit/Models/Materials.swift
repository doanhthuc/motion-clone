import Foundation

public enum MaterialKind: String, Decodable, Sendable, Hashable {
    case image, video, other, unknown

    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = MaterialKind(rawValue: raw) ?? .unknown
    }
}

public struct Material: Decodable, Sendable, Hashable, Identifiable {
    public let id: String
    public let owner: String
    public let name: String
    public let bytes: Int64
    public let updatedAt: Double
    public let kind: MaterialKind

    public init(id: String, owner: String, name: String, bytes: Int64,
                updatedAt: Double, kind: MaterialKind) {
        self.id = id
        self.owner = owner
        self.name = name
        self.bytes = bytes
        self.updatedAt = updatedAt
        self.kind = kind
    }

    public var canDelete: Bool { owner == "app" }
}

public struct MaterialsResponse: Decodable, Sendable, Equatable {
    public let materials: [Material]
}

public struct UploadOpenRequest: Encodable, Sendable, Equatable {
    public let fileName: String
    public let size: Int64

    public init(fileName: String, size: Int64) {
        self.fileName = fileName
        self.size = size
    }
}

public struct UploadOpenResponse: Decodable, Sendable, Equatable {
    public let uploadId: String
    public let chunkSize: Int64
    public let chunksTotal: Int
}

public struct MaterialProbe: Decodable, Sendable, Equatable {
    public let kind: String
    public let width: Int?
    public let height: Int?
    public let durationS: Double?
    public let bitrateKbps: Int?
    public let sizeBytes: Int64
    public let warning: String

    private enum CodingKeys: String, CodingKey {
        case kind, width, height, durationS, bitrateKbps, sizeBytes, warning
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        kind = try values.decode(String.self, forKey: .kind)
        width = try values.decodeIfPresent(Int.self, forKey: .width)
        height = try values.decodeIfPresent(Int.self, forKey: .height)
        durationS = try values.decodeIfPresent(Double.self, forKey: .durationS)
        bitrateKbps = try values.decodeIfPresent(Int.self, forKey: .bitrateKbps)
        sizeBytes = try values.decode(Int64.self, forKey: .sizeBytes)
        warning = try values.decodeIfPresent(String.self, forKey: .warning) ?? ""
    }
}

public struct UploadStatus: Decodable, Sendable, Equatable {
    public let uploadId: String
    public let fileName: String
    public let size: Int64
    public let chunkSize: Int64
    public let chunksTotal: Int
    public let received: [Int]
    public let material: Material?
    public let probe: MaterialProbe?

}

/// `POST /v1/materials/link` — the server downloads the video and answers
/// with an `UploadCompleteResponse`, the same shape an upload completes with.
public struct LinkImportRequest: Encodable, Sendable, Equatable {
    public let url: String

    public init(url: String) {
        self.url = url
    }
}

/// The links the server will fetch, mirroring `scripts/tgbot/tiktok.py`'s
/// `URL_RE`: www./m. for the site, vm./vt. for the app's two share-link shapes.
/// Checked here too so a copied caption can be trimmed to its link, and the
/// Import button can stay disabled for anything the server would refuse.
public enum TikTokLink {
    public static func find(in text: String) -> String? {
        guard let match = text.firstMatch(of: pattern) else { return nil }
        return String(match.output)
    }

    nonisolated(unsafe) private static let pattern =
        /https?:\/\/(?:www\.|m\.|vm\.|vt\.)?tiktok\.com\/\S+/.ignoresCase()
}

public struct UploadCompleteResponse: Decodable, Sendable, Equatable {
    public let material: Material
    public let probe: MaterialProbe

    public init(material: Material, probe: MaterialProbe) {
        self.material = material
        self.probe = probe
    }
}
