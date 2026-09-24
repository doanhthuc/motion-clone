import Foundation

/// `GET /v1/runs/{id}/tryon` (`AppRuns.tryon`). `index` is the run's position
/// in the manifest, as a string — the index `regen` and the image routes take.
public struct TryonPreview: Decodable, Sendable, Equatable, Identifiable {
    public let index: String
    public let run: String
    public let status: StageStatus
    public let hasImage: Bool
    public var id: String { index }
}

public struct TryonPreviews: Decodable, Sendable, Equatable {
    public let runId: String
    /// Required by `regen` and `resume`. Changes when the manifest is rewritten.
    public let runToken: String
    public let phaseARunning: Bool
    public let previews: [TryonPreview]
}

/// `GET /v1/runs/{id}/rent-panel` (`AppRuns.rent_panel` + `_rent_panel_data`).
public struct RentPanelRunpod: Decodable, Sendable, Equatable {
    public let gpu: String
    public let datacenter: String?
    public let stock: String?
    public let usdPerHr: Double?
    public let soldOut: Bool
}

public struct RentPanelVast: Decodable, Sendable, Equatable {
    public let enabled: Bool
    public let usdPerHr: Double?
    public let sessionUsd: Double?
    public let blockers: [String]
    public let canSpend: Bool
}

public struct RentPanel: Decodable, Sendable, Equatable {
    public let runId: String
    /// Must accompany `confirm`; every read replaces it.
    public let panelToken: String
    public let afterPhaseA: Bool
    public let jobs: Int
    public let estimateMin: Double
    public let runpod: RentPanelRunpod
    public let vast: RentPanelVast
}

/// `POST /v1/tryon-library` — a free file copy, not a spend.
public struct TryonKeepRequest: Encodable, Sendable {
    public let runId: String
    public let index: String
    public init(runId: String, index: String) {
        self.runId = runId
        self.index = index
    }
}

public struct TryonLibraryRecord: Decodable, Sendable, Equatable {
    public let id: String
    public let provider: String
    public let savedAt: Double
}

/// `GET /v1/tryon-library` → `{"entries": [...]}` (API spec §5.10).
public struct TryonLibraryEntry: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    /// Every non-driver input the try-on was made from (bot.py `tryon_save_info`).
    public let materialIDs: [String: String]
    public let provider: String
    public let savedAt: Double

    public init(id: String, materialIDs: [String: String], provider: String, savedAt: Double) {
        self.id = id
        self.materialIDs = materialIDs
        self.provider = provider
        self.savedAt = savedAt
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case materialIDs = "materialIds"
        case provider, savedAt
    }
}

public struct TryonLibraryResponse: Decodable, Sendable, Equatable {
    public let entries: [TryonLibraryEntry]
}

/// `{"ok": true}` — what `DELETE /v1/tryon-library/{id}` answers with status 200.
/// `APIClient.delete(_:)` accepts only 204, so the library delete needs the
/// decoding overload; `KillResult` is not reusable (it also wants at/code/message).
public struct OkResponse: Decodable, Sendable, Equatable {
    public let ok: Bool
}
