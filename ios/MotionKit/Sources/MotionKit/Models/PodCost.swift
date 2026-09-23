import Foundation

/// `GET /v1/gpu/stock` (bot.py `_gpu_stock_data`). A dead runpodctl is a 502,
/// never an empty list — so an empty `gpus` is never read as "sold out".
public struct GpuStock: Decodable, Sendable, Equatable {
    /// `.env`'s GPU. Updated in place after a successful `PUT /v1/pod/gpu`.
    public internal(set) var selected: String
    public let homeDatacenter: String?
    public let gpus: [GpuStockRow]
    public let otherRegions: [GpuRegion]

    /// Where a migration can go: every datacenter the stock check lists with
    /// some stock, home excluded, in the server's best-first order. The server
    /// re-checks the destination itself (`unknown_datacenter`).
    public var destinations: [MigrationDestination] {
        var order: [String] = []
        var gpus: [String: [String]] = [:]
        for region in otherRegions
        where region.datacenter != homeDatacenter && region.stock.lowercased() != "none" {
            if gpus[region.datacenter] == nil { order.append(region.datacenter) }
            gpus[region.datacenter, default: []].append("\(region.name) (\(region.stock))")
        }
        return order.map { MigrationDestination(datacenter: $0, gpus: gpus[$0] ?? []) }
    }
}

public struct GpuHomeStock: Decodable, Sendable, Equatable {
    public let stock: String
}

public struct GpuStockRow: Decodable, Sendable, Equatable, Identifiable {
    public let gpu: String
    public let name: String
    public let usdPerHr: Double?
    /// nil when the home datacenter itself is unknown or lists no entry.
    public let home: GpuHomeStock?
    public let soldOutEverywhere: Bool

    public var id: String { gpu }

    public var summary: String {
        if soldOutEverywhere { return "Sold out everywhere" }
        let price = usdPerHr.map { "\(Format.usd($0))/h" } ?? "price unknown"
        return "\(price) · home stock \(home?.stock ?? "unknown")"
    }
}

public struct GpuRegion: Decodable, Sendable, Equatable {
    public let gpu: String
    public let name: String
    public let datacenter: String
    public let stock: String
    public let usdPerHr: Double?
}

public struct MigrationDestination: Sendable, Equatable, Identifiable {
    public let datacenter: String
    /// "RTX 5090 (High)" — each GPU stocked there, with its stock word.
    public let gpus: [String]
    public var id: String { datacenter }
}

/// `PUT /v1/pod/gpu` — one of the catalog ids `GET /v1/gpu/stock` hands out.
public struct GpuSelectionRequest: Encodable, Sendable {
    public let gpu: String
    public init(gpu: String) { self.gpu = gpu }
}

public struct GpuSelection: Decodable, Sendable, Equatable {
    public let gpu: String
    public let name: String
}

/// `GET /v1/balance[?vast=1]` (bot.py `_balance_data`). Always 200; an
/// unreadable account is `runpod: null` / `vast.usd: null` plus a reason in
/// `errors` — never a zero.
public struct Balance: Decodable, Sendable, Equatable {
    public let runpod: RunpodBalance?
    /// Present only when `?vast=1` was asked.
    public let vast: VastBalance?
    public let errors: [String]
}

public struct RunpodBalance: Decodable, Sendable, Equatable {
    public let usd: Double
    public let usdPerHr: Double
    public let runwayHours: Double
    /// Under one hour of the configured GPU.
    public let lowRunway: Bool
}

public struct VastBalance: Decodable, Sendable, Equatable {
    public let usd: Double?
}

/// `POST /v1/pod/migrate/ask` — acts on nothing, so no Idempotency-Key.
public struct MigrateAskRequest: Encodable, Sendable {
    public let toDc: String
    public init(toDc: String) { self.toDc = toDc }
}

public struct MigrateAsk: Decodable, Sendable, Equatable {
    public let toDc: String
    public let homeDatacenter: String
    /// Single use, bound to `toDc` and the volume id, in the bot's memory only.
    public let confirmToken: String
    public let expiresInSec: Double
    /// `_migrate_warning`'s text — shown verbatim, never rephrased.
    public let warning: String
}
