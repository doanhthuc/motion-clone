# SwiftUI App Phase 5 (Pod and Cost) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the iPhone app a Pod tab — kill (with a persistent "may still be billing" banner), GPU choice (also from the rent panel), balances, and the Network Volume migration — against the live control-plane API, with no backend change.

**Architecture:** Four `@Observable @MainActor` units in `MotionKit`: `PodStore` (extended: kill + poll + banner + migration poll), `GpuStore`, `BalanceStore`, `MigrateFlow`. Kill bypasses `SpendGate` (fresh key per tap, never resent); migrate goes through the Phase 4 `SpendGate` as a new `SpendIntent.migrate`. SwiftUI views stay thin consumers; views never call `APIClient`.

**Tech Stack:** Swift 6 (strict concurrency), SwiftUI on iOS 26, swift-testing with `StubURLProtocol`, XCUITest, XcodeGen.

**Spec:** `docs/superpowers/specs/2026-09-23-swiftui-app-phase-5-design.md` (parent: `docs/superpowers/specs/2026-09-22-swiftui-app-design.md`; API: `docs/superpowers/specs/2026-09-21-vps-control-plane-api-design.md` §5.4, §5.9).

## Global Constraints

- No file under `scripts/**` changes. Nothing in this plan deploys to the VPS.
- Views never call `APIClient`; every network call lives in a `MotionKit` store.
- Kill never goes through `SpendGate` or the ledger, and is never disabled by `SpendGate`/`RunFlow.canSpend` — only by a kill already sending or polling. `PodStore` takes no gate.
- Kill: one fresh `UUID().uuidString` `Idempotency-Key` per tap; a transport failure / 5xx is **never resent** — the phone probes `GET /v1/pod` instead.
- Migrate: one UUID per tap minted only inside `SpendGate.perform`; ledger fsync'd before sending; never a new key for an old tap; Phase 4's busy/ambiguous/`outcome_unknown` rules unchanged.
- The unverified-kill banner shows for `last_kill.ok == false` with `code` `destroy_unverified` **or** `error`, until the user acknowledges that `at` (stored in `UserDefaults` key `pod.ackedKillAt`) or a later kill overwrites `last_kill`.
- Money never renders as `$0` for an unreadable balance: `runpod == null` → "Couldn't read the RunPod balance"; `vast.usd == null` → "Couldn't read the Vast credit".
- `?vast=1` is requested only from an explicit tap. `?force=1` on stock only from pull-to-refresh / Retry.
- A `502` on `GET /v1/gpu/stock` keeps the last good list (never an empty list, which would read as "sold out").
- Migrate's confirm button enables only when the typed text equals `to_dc` exactly (case-sensitive, no trimming) and before `receivedAt + expires_in_sec − 15 s`.
- Timeouts: kill 90 s; stock 60 s; balance 45 s; balance with Vast 90 s; migrate ask 90 s.
- Zero-spend gates before every commit: `make ios-test`; plus `make ios-build` for any task touching `ios/MotionApp`; `motions-studio/setup/scrub-secrets.sh --check` exits 0. Stage exact paths only. Never commit `ios/Secrets.xcconfig`, `.env` or live payloads.
- Live calls (`make ios-contract`, `make ios-refusal-smoke`, `make ios-ui-test`) are run by the controller, not implementers; `ios-refusal-smoke` needs the user's approval before each run. `make ios-ui-test` must run outside the command sandbox.
- UI smoke never taps a GPU row (rewrites the live `.env`), Kill, or Migrate.
- Commit messages in English, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings made while planning (recorded, binding on implementers)

- **Scene-active kill poll (spec §4 step 5):** not gated explicitly. The kill poll runs in an unstructured `Task` from the button; iOS suspends the process in the background, which pauses the loop. The 5-minute cap uses wall-clock `now()`, so a long background stay ends in "Still running" + **Check again**, which is the spec's cap behaviour. Cost if wrong: one extra `GET /v1/pod` after resume.
- **Balance line (spec §5):** "$12.34 · ≈ 12h 28m at $0.99/h" — the GPU name is omitted because `GET /v1/balance` does not return it. Cost if wrong: cosmetic.
- **`podRequested` (Phase 4 `outcome_unknown`) now switches to the Pod tab**, not Runs — the parent design's intent, impossible before a Pod tab existed.
- **"Kill still sends while the gate holds a pending entry"** (spec §8) is true by construction: `PodStore` has no gate dependency. No runtime test; Global Constraints forbid adding one.
- **`MigrateFlow.begin(preselect:)`** always resets to the destination list (unless a migrate is sending / awaiting recheck / replaying): reopening the sheet asks again; the server simply replaces the token.

## File structure

| File | Responsibility |
|---|---|
| `ios/MotionKit/Sources/MotionKit/Models/Pod.swift` (modify) | `PodMigration`, `PodStatus.migration` |
| `ios/MotionKit/Sources/MotionKit/Models/PodCost.swift` (create) | `GpuStock` + rows/regions/destinations, `Balance`, `GpuSelection*`, `MigrateAsk*` |
| `ios/MotionKit/Sources/MotionKit/Formatting.swift` (modify) | `Format.runway(hours:)` |
| `ios/MotionKit/Sources/MotionKit/API/APIClient.swift` (modify) | JSON `put`, `timeout:` on JSON `post` |
| `ios/MotionKit/Sources/MotionKit/Stores/PodStore.swift` (modify) | kill, kill poll, banner, migration poll |
| `ios/MotionKit/Sources/MotionKit/Stores/GpuStore.swift` (create) | stock + select |
| `ios/MotionKit/Sources/MotionKit/Stores/BalanceStore.swift` (create) | balance + Vast credit |
| `ios/MotionKit/Sources/MotionKit/Money/SpendIntent.swift` (modify) | `.migrate`, `SpendKind.migrate` |
| `ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift` (modify) | ignore migrate; `reloadPanelAfterGpuChange()` |
| `ios/MotionKit/Sources/MotionKit/Stores/MigrateFlow.swift` (create) | ask → confirm → migrate |
| `ios/MotionApp/Pod/KillViews.swift` (create) | `KillButton`, `KillNotice`, `KillBanner`, `DestructiveButtonStyle` |
| `ios/MotionApp/Pod/PodView.swift` (create) | Pod tab, `LeaseCard`, `MigrationCard`, `BalanceCard` |
| `ios/MotionApp/Pod/GpuPickerView.swift` (create) | shared GPU list |
| `ios/MotionApp/Pod/MigrateSheet.swift` (create) | migrate sheet |
| `ios/MotionApp/MotionApp.swift`, `RootView.swift`, `RunFlow/RecordingSpendGate.swift`, `Runs/RunDetailView.swift`, `Runs/RunsView.swift`, `RunFlow/RentPanelView.swift` (modify) | wiring |
| `ios/MotionKit/Sources/motion-contract/main.swift`, `Makefile` (modify) | contract + refusal smoke |
| `ios/MotionAppUITests/Phase5SmokeTests.swift` (create) | zero-spend Pod smoke |
| docs (modify) | handoff, spec status, README, CLAUDE.md/AGENTS.md |

All `swift test` commands run from `ios/MotionKit`.

---

### Task 1: Models and fixtures

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Models/Pod.swift`
- Create: `ios/MotionKit/Sources/MotionKit/Models/PodCost.swift`
- Modify: `ios/MotionKit/Sources/MotionKit/Formatting.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/Fixtures.swift`
- Modify: `ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift`, `ios/MotionKit/Tests/MotionKitTests/FormattingTests.swift`

**Interfaces:**
- Produces: `PodMigration { running, phase, toDc, startedAt, bytesCopied, totalBytes, fractionCopied }`, `PodStatus.migration: PodMigration?`, `GpuStock { selected (internal(set) var), homeDatacenter, gpus: [GpuStockRow], otherRegions: [GpuRegion], destinations: [MigrationDestination] }`, `GpuStockRow { gpu, name, usdPerHr, home: GpuHomeStock?, soldOutEverywhere, summary, id }`, `GpuRegion { gpu, name, datacenter, stock, usdPerHr }`, `MigrationDestination { datacenter, gpus: [String], id }`, `GpuSelectionRequest(gpu:)`, `GpuSelection { gpu, name }`, `Balance { runpod: RunpodBalance?, vast: VastBalance?, errors }`, `RunpodBalance { usd, usdPerHr, runwayHours, lowRunway }`, `VastBalance { usd: Double? }`, `MigrateAskRequest(toDc:)`, `MigrateAsk { toDc, homeDatacenter, confirmToken, expiresInSec, warning }`, `Format.runway(hours:) -> String`; fixtures `Fixtures.gpuStock`, `.balance`, `.balanceRunpodDown`, `.balanceVast`, `.balanceVastDown`, `.migrateAsk`, `.podMigrating`.

- [ ] **Step 1: Fix and add fixtures**

In `Fixtures.swift`, `podIdle` currently carries `"migration": {"state": "whatever"}`, which the new model rejects (a real server always sends `running`). Replace that line's migration value, and add the new fixtures after `podFailedOtherGpu`:

```swift
    static let podIdle = #"""
    {"run_id": "tg-1000", "gpu": "NVIDIA GeForce RTX 5090", "lease": null,
     "migration": {"running": false, "phase": null, "to_dc": null, "started_at": null,
                   "bytes_copied": null, "total_bytes": null},
     "kill_running": false, "last_kill": null,
     "failed_rental": {"gpu": "NVIDIA GeForce RTX 5090", "datacenter": "EU-RO-1",
                       "stock_out": true, "detail": "no stock"}}
    """#
```

```swift
    static let podMigrating = #"""
    {"run_id": "tg-1000", "gpu": "NVIDIA GeForce RTX 5090", "lease": null,
     "migration": {"running": true, "phase": "copy", "to_dc": "EU-CZ-1", "started_at": 1790000000,
                   "bytes_copied": 1000, "total_bytes": 4000},
     "kill_running": false, "last_kill": null, "failed_rental": null}
    """#

    /// `GET /v1/gpu/stock` (bot.py `_gpu_stock_data`): five GPUs in catalog order.
    static let gpuStock = #"""
    {"selected": "NVIDIA GeForce RTX 5090", "home_datacenter": "EU-RO-1",
     "gpus": [
      {"gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090", "usd_per_hr": 0.99,
       "home": {"stock": "Low"}, "sold_out_everywhere": false},
      {"gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090", "usd_per_hr": 0.69,
       "home": null, "sold_out_everywhere": false},
      {"gpu": "NVIDIA RTX PRO 4500 Blackwell", "name": "RTX PRO 4500", "usd_per_hr": null,
       "home": null, "sold_out_everywhere": true},
      {"gpu": "NVIDIA L40S", "name": "L40S", "usd_per_hr": 0.86,
       "home": {"stock": "None"}, "sold_out_everywhere": false},
      {"gpu": "NVIDIA RTX PRO 6000 Blackwell Server Edition", "name": "RTX PRO 6000",
       "usd_per_hr": 2.09, "home": {"stock": "High"}, "sold_out_everywhere": false}
     ],
     "other_regions": [
      {"gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090", "datacenter": "EU-CZ-1",
       "stock": "High", "usd_per_hr": 0.99},
      {"gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090", "datacenter": "EU-CZ-1",
       "stock": "Medium", "usd_per_hr": 0.69},
      {"gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090", "datacenter": "US-TX-3",
       "stock": "Low", "usd_per_hr": 0.69}
     ]}
    """#

    static let balance = #"""
    {"runpod": {"usd": 12.34, "usd_per_hr": 0.99, "runway_hours": 12.46, "low_runway": false},
     "errors": []}
    """#

    static let balanceRunpodDown = #"""
    {"runpod": null, "errors": ["couldn't reach runpodctl: timeout"]}
    """#

    static let balanceVast = #"""
    {"runpod": {"usd": 12.34, "usd_per_hr": 0.99, "runway_hours": 12.46, "low_runway": false},
     "vast": {"usd": 7.5}, "errors": []}
    """#

    static let balanceVastDown = #"""
    {"runpod": {"usd": 0.5, "usd_per_hr": 0.99, "runway_hours": 0.51, "low_runway": true},
     "vast": {"usd": null}, "errors": ["couldn't read the Vast credit: exit 1"]}
    """#

    static let migrateAsk = #"""
    {"to_dc": "EU-CZ-1", "home_datacenter": "EU-RO-1", "confirm_token": "tok-abc",
     "expires_in_sec": 600,
     "warning": "This copies your Network Volume to EU-CZ-1: ~2 temporary CPU pods for the duration, then deletes the current volume once the copy is verified byte-for-byte. Cannot be undone once the old volume is deleted."}
    """#
```

- [ ] **Step 2: Write the failing tests**

Append to `ModelsTests`:

```swift
    @Test func decodesPodMigration() throws {
        let migrating = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podMigrating))
        #expect(migrating.migration?.running == true)
        #expect(migrating.migration?.toDc == "EU-CZ-1")
        #expect(migrating.migration?.fractionCopied == 0.25)
        let idle = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podIdle))
        #expect(idle.migration?.running == false)
        #expect(idle.migration?.fractionCopied == nil)
        let live = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podLive))
        #expect(live.migration == nil)
    }

    @Test func decodesGpuStockAndItsDestinations() throws {
        let stock = try decoder.decode(GpuStock.self, from: Fixtures.data(Fixtures.gpuStock))
        #expect(stock.selected == "NVIDIA GeForce RTX 5090")
        #expect(stock.homeDatacenter == "EU-RO-1")
        #expect(stock.gpus.count == 5)
        #expect(stock.gpus[0].home?.stock == "Low")
        #expect(stock.gpus[2].usdPerHr == nil && stock.gpus[2].soldOutEverywhere)
        #expect(stock.destinations == [
            MigrationDestination(datacenter: "EU-CZ-1", gpus: ["RTX 5090 (High)", "RTX 4090 (Medium)"]),
            MigrationDestination(datacenter: "US-TX-3", gpus: ["RTX 4090 (Low)"]),
        ])
    }

    @Test func gpuRowSummaryNeverInventsAPriceOrStock() throws {
        let stock = try decoder.decode(GpuStock.self, from: Fixtures.data(Fixtures.gpuStock))
        #expect(stock.gpus[0].summary == "$0.99/h · home stock Low")
        #expect(stock.gpus[1].summary == "$0.69/h · home stock unknown")
        #expect(stock.gpus[2].summary == "Sold out everywhere")
    }

    @Test func decodesBalances() throws {
        let ok = try decoder.decode(Balance.self, from: Fixtures.data(Fixtures.balance))
        #expect(ok.runpod?.usd == 12.34 && ok.runpod?.runwayHours == 12.46)
        #expect(ok.vast == nil)
        let down = try decoder.decode(Balance.self, from: Fixtures.data(Fixtures.balanceRunpodDown))
        #expect(down.runpod == nil)
        #expect(down.errors == ["couldn't reach runpodctl: timeout"])
        let vastDown = try decoder.decode(Balance.self, from: Fixtures.data(Fixtures.balanceVastDown))
        #expect(vastDown.vast != nil && vastDown.vast?.usd == nil)
        #expect(vastDown.runpod?.lowRunway == true)
    }

    @Test func decodesMigrateAsk() throws {
        let ask = try decoder.decode(MigrateAsk.self, from: Fixtures.data(Fixtures.migrateAsk))
        #expect(ask.toDc == "EU-CZ-1" && ask.homeDatacenter == "EU-RO-1")
        #expect(ask.confirmToken == "tok-abc" && ask.expiresInSec == 600)
        #expect(ask.warning.contains("Cannot be undone"))
    }
```

Append to `FormattingTests` (inside its suite):

```swift
    @Test func runwayRoundsToWholeMinutes() {
        #expect(Format.runway(hours: 12.46) == "12h 28m")
        #expect(Format.runway(hours: 0.5) == "30m")
        #expect(Format.runway(hours: 0) == "0m")
        #expect(Format.runway(hours: -1) == "0m")
    }
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `swift test --filter "ModelsTests|FormattingTests"`
Expected: compile errors — `PodMigration`, `GpuStock`, `Balance`, `MigrateAsk`, `MigrationDestination`, `Format.runway` not defined.

- [ ] **Step 4: Implement**

In `Models/Pod.swift`, add above `PodStatus`:

```swift
/// `GET /v1/pod`'s `migration` (bot.py `AppPod._migration_state`), read from
/// files only. Every field but `running` stays null until volume_migrate.py
/// writes it; the phone computes elapsed from `startedAt` itself.
public struct PodMigration: Decodable, Sendable, Equatable {
    public let running: Bool
    public let phase: String?
    public let toDc: String?
    public let startedAt: Double?
    public let bytesCopied: Double?
    public let totalBytes: Double?

    /// nil until both byte counts exist — no bar is better than a made-up one.
    public var fractionCopied: Double? {
        guard let bytesCopied, let totalBytes, totalBytes > 0 else { return nil }
        return min(1, max(0, bytesCopied / totalBytes))
    }
}
```

and replace the `PodStatus` doc comment and body with:

```swift
/// `GET /v1/pod` (`AppPod.pod` in bot.py). Files and memory only on the
/// server, and taken without `BOT_LOCK`, so it answers while a kill holds it.
public struct PodStatus: Decodable, Sendable, Equatable {
    public let runId: String?
    public let gpu: String
    public let lease: PodLease?
    public let migration: PodMigration?
    public let killRunning: Bool
    public let lastKill: KillResult?
    public let failedRental: FailedRental?
}
```

Create `Models/PodCost.swift`:

```swift
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
```

In `Formatting.swift`, add inside `enum Format`:

```swift
    /// Balance runway: "12h 28m", or "45m" under an hour.
    public static func runway(hours: Double) -> String {
        let minutes = max(0, Int((hours * 60).rounded()))
        let h = minutes / 60, m = minutes % 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }
```

- [ ] **Step 5: Run the whole suite**

Run: `swift test`
Expected: all tests pass (158 existing + 6 new). If any existing test used `podIdle`'s old migration value, it only decoded `PodStatus`, which now succeeds.

- [ ] **Step 6: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Models/Pod.swift ios/MotionKit/Sources/MotionKit/Models/PodCost.swift \
  ios/MotionKit/Sources/MotionKit/Formatting.swift ios/MotionKit/Tests/MotionKitTests/Fixtures.swift \
  ios/MotionKit/Tests/MotionKitTests/ModelsTests.swift ios/MotionKit/Tests/MotionKitTests/FormattingTests.swift
git commit -m "feat(ios): decode pod migration, GPU stock, balance and migrate ask

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: APIClient — JSON `put` and a timeout on JSON `post`

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/API/APIClient.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift`

**Interfaces:**
- Consumes: `GpuSelectionRequest`, `GpuSelection`, `MigrateAskRequest`, `MigrateAsk` (Task 1).
- Produces: `APIClient.put<Response, Body>(_:body:timeout:_:) async throws(APIError) -> Response` (okStatuses `[200]`); `APIClient.post<Response, Body>(_:body:timeout: TimeInterval = 30, _:)` — the existing body overload gains a defaulted `timeout`.

- [ ] **Step 1: Write the failing tests** (inside `APIClientTests`, which already sits under `extension URLProtocolTests`)

```swift
    @Test func putSendsSnakeCaseJSONAndDecodes() async throws {
        StubURLProtocol.install { _ in TestSupport.json(#"{"gpu": "NVIDIA L40S", "name": "L40S"}"#) }
        let chosen = try await TestSupport.client().put(
            GpuSelection.self, body: GpuSelectionRequest(gpu: "NVIDIA L40S"), "v1", "pod", "gpu")
        #expect(chosen == GpuSelection(gpu: "NVIDIA L40S", name: "L40S"))
        let request = try #require(StubURLProtocol.requests.last)
        #expect(request.httpMethod == "PUT")
        #expect(request.url?.path == "/v1/pod/gpu")
        #expect(request.value(forHTTPHeaderField: "Content-Type") == "application/json")
        let body = try #require(JSONSerialization.jsonObject(with: request.httpBody ?? Data()) as? [String: String])
        #expect(body == ["gpu": "NVIDIA L40S"])
    }

    @Test func putSurfacesTheServerRefusal() async {
        StubURLProtocol.install { _ in
            TestSupport.json(#"{"error": {"code": "bad_request", "message": "gpu must be one of the catalog ids"}}"#, status: 400)
        }
        await #expect(throws: APIError.server(status: 400, code: "bad_request",
                                              message: "gpu must be one of the catalog ids")) {
            _ = try await TestSupport.client().put(
                GpuSelection.self, body: GpuSelectionRequest(gpu: "x"), "v1", "pod", "gpu")
        }
    }

    @Test func postWithBodyAcceptsATimeout() async throws {
        StubURLProtocol.install { _ in TestSupport.json(Fixtures.migrateAsk) }
        let ask = try await TestSupport.client().post(
            MigrateAsk.self, body: MigrateAskRequest(toDc: "EU-CZ-1"), timeout: 90,
            "v1", "pod", "migrate", "ask")
        #expect(ask.confirmToken == "tok-abc")
        let request = try #require(StubURLProtocol.requests.last)
        let body = try #require(JSONSerialization.jsonObject(with: request.httpBody ?? Data()) as? [String: String])
        #expect(body == ["to_dc": "EU-CZ-1"])
        #expect(request.value(forHTTPHeaderField: "Idempotency-Key") == nil)
    }
```

(`GpuSelection` is `Decodable` only; the equality above uses its internal memberwise init, available under `@testable import`.)

- [ ] **Step 2: Run to verify failure**

Run: `swift test --filter APIClientTests`
Expected: compile error — no `put(_:body:_:)`, no `timeout:` label on the body `post`.

- [ ] **Step 3: Implement**

Replace the body-taking `post` in `APIClient.swift` with:

```swift
    public func post<Response: Decodable & Sendable, Body: Encodable & Sendable>(
        _ response: Response.Type, body: Body, timeout: TimeInterval = 30, _ components: String...
    ) async throws(APIError) -> Response {
        let encoded: Data
        do {
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            encoded = try encoder.encode(body)
        } catch {
            throw .transport("couldn't encode the request: \(error.localizedDescription)")
        }
        let (data, _) = try await send(
            url(components), method: "POST", body: encoded, contentType: "application/json",
            timeout: timeout, extraHeaders: [:], okStatuses: [200, 201])
        return try decode(response, data)
    }
```

and add after `patch`:

```swift
    /// JSON PUT (`PUT /v1/pod/gpu`). Not a spend: no Idempotency-Key.
    public func put<Response: Decodable & Sendable, Body: Encodable & Sendable>(
        _ response: Response.Type, body: Body, timeout: TimeInterval = 30,
        _ components: String...
    ) async throws(APIError) -> Response {
        let encoded: Data
        do {
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            encoded = try encoder.encode(body)
        } catch {
            throw .transport("couldn't encode the request: \(error.localizedDescription)")
        }
        let (data, _) = try await send(
            url(components), method: "PUT", body: encoded, contentType: "application/json",
            timeout: timeout, extraHeaders: [:], okStatuses: [200])
        return try decode(response, data)
    }
```

- [ ] **Step 4: Run the whole suite**

Run: `swift test`
Expected: all pass (existing callers of the body `post` compile unchanged because `timeout` is defaulted and precedes the variadic).

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/API/APIClient.swift ios/MotionKit/Tests/MotionKitTests/APIClientTests.swift
git commit -m "feat(ios): JSON PUT and a timeout on JSON POST

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: PodStore — kill, its poll, the banner and the migration poll

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/PodStore.swift`
- Create: `ios/MotionKit/Tests/MotionKitTests/PodStoreTests.swift`

**Interfaces:**
- Consumes: `PodStatus`, `KillResult`, `RunStatus`, `APIClient.spendPost`, `SpendGate.Envelope` (internal, same module), `Counter` (test helper in `StoresTests.swift`).
- Produces (all `public`, `@MainActor`):
  - `init(client:defaults: UserDefaults = .standard, sleep:, now:, makeKey:)` — every new parameter defaulted, so `PodStore(client:)` still compiles.
  - `pod`, `error`, `lastSuccess`, `isStale`, `refresh()` (unchanged behaviour).
  - `enum KillState { idle, sending, killing }`, `killState`, `isKilling`, `struct Notice { text, isError }`, `killNotice: Notice?`, `killStillRunning: Bool`.
  - `showsKill(runStatus: RunStatus? = nil) -> Bool`, `kill(runID:) async`, `checkKillAgain() async`.
  - `unverifiedKill: KillResult?`, `acknowledgeUnverifiedKill()`.
  - `pollMigration() async` (loops until its task is cancelled or `sleep` throws).

- [ ] **Step 1: Write the failing tests** — create `PodStoreTests.swift`:

```swift
import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct PodStoreTests {
    /// `GET /v1/pod` answers from a queue (the last entry repeats); the kill
    /// POST answers from its own script.
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var pods: [String]
        private var kills: [(Int, [String: String], Data)]
        init(pods: [String], kills: [(Int, [String: String], Data)] = []) {
            self.pods = pods
            self.kills = kills
        }
        func setPods(_ p: [String]) { lock.withLock { pods = p } }
        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            lock.withLock {
                let path = request.url?.path ?? ""
                if request.httpMethod == "POST", path == "/v1/runs/tg-1000/kill" {
                    return kills.isEmpty ? (500, [:], Data()) : kills.removeFirst()
                }
                if path == "/v1/pod" {
                    return TestSupport.json(pods.count > 1 ? pods.removeFirst() : pods[0])
                }
                return (404, [:], Data())
            }
        }
    }

    final class Clock: @unchecked Sendable {
        private let lock = NSLock()
        private var current = Date(timeIntervalSince1970: 1_790_000_000)
        var now: Date { lock.withLock { current } }
        func advance(_ seconds: TimeInterval) { lock.withLock { current += seconds } }
    }

    static func pod(lease: Bool = false, killRunning: Bool = false, lastKill: String = "null",
                    migration: String = "null") -> String {
        let leaseJSON = lease
            ? #"{"provider": "runpod", "provisioned_at": 1789999000, "abs_max_min": 180, "quoted_usd_per_hr": 0.99, "run_id": "tg-1000"}"#
            : "null"
        return """
        {"run_id": "tg-1000", "gpu": "NVIDIA GeForce RTX 5090", "lease": \(leaseJSON),
         "migration": \(migration), "kill_running": \(killRunning), "last_kill": \(lastKill),
         "failed_rental": null}
        """
    }

    static func lastKill(at: Double, ok: Bool, code: String, message: String) -> String {
        #"{"at": \#(at), "ok": \#(ok), "code": "\#(code)", "message": "\#(message)"}"#
    }

    static let killed = lastKill(at: 1_790_000_100, ok: true, code: "killed",
                                 message: "pod destroyed and verified gone")
    static let accepted = TestSupport.json(#"{"run_id": "tg-1000", "outcome": "kill_started"}"#, status: 202)
    static let dropped: (Int, [String: String], Data) = (-1, [:], Data())

    static func refusal(_ status: Int, _ code: String, _ message: String) -> (Int, [String: String], Data) {
        TestSupport.json(#"{"error": {"code": "\#(code)", "message": "\#(message)"}}"#, status: status)
    }

    static func freshDefaults() -> UserDefaults {
        UserDefaults(suiteName: "podstore-\(UUID().uuidString)")!
    }

    private func make(_ routes: Routes, clock: Clock = Clock(), step: TimeInterval = 2,
                      defaults: UserDefaults = PodStoreTests.freshDefaults()) -> PodStore {
        StubURLProtocol.install { routes.answer($0) }
        return PodStore(client: TestSupport.client(), defaults: defaults,
                        sleep: { _ in clock.advance(step) }, now: { clock.now })
    }

    private var killPosts: [URLRequest] {
        StubURLProtocol.requests.filter { $0.httpMethod == "POST" && $0.url?.path == "/v1/runs/tg-1000/kill" }
    }

    private var podReads: Int {
        StubURLProtocol.requests.filter { $0.url?.path == "/v1/pod" }.count
    }

    // MARK: kill

    @Test func acceptedKillPollsUntilTheServerRecordsTheResult() async {
        let routes = Routes(pods: [Self.pod(lease: true)], kills: [Self.accepted])
        let store = make(routes)
        await store.refresh()
        routes.setPods([Self.pod(lease: true, killRunning: true), Self.pod(lastKill: Self.killed)])
        await store.kill(runID: "tg-1000")
        #expect(store.killState == .idle)
        #expect(store.killNotice == .init(text: "pod destroyed and verified gone", isError: false))
        #expect(killPosts.count == 1)
        #expect(killPosts.first?.value(forHTTPHeaderField: "Idempotency-Key")?.isEmpty == false)
        #expect(store.pod?.lease == nil)
    }

    @Test func killInProgressIsFollowedLikeAccepted() async {
        let routes = Routes(pods: [Self.pod(lease: true)],
                            kills: [Self.refusal(409, "kill_in_progress", "a kill is already running")])
        let store = make(routes)
        await store.refresh()
        routes.setPods([Self.pod(killRunning: true), Self.pod(lastKill: Self.killed)])
        await store.kill(runID: "tg-1000")
        #expect(store.killNotice?.text == "pod destroyed and verified gone")
        #expect(killPosts.count == 1)
    }

    @Test func nothingRunningShowsTheServerTextAndRefreshes() async {
        let routes = Routes(pods: [Self.pod()],
                            kills: [Self.refusal(409, "nothing_running", "nothing is running — there is no pod to kill")])
        let store = make(routes)
        await store.kill(runID: "tg-1000")
        #expect(store.killNotice == .init(text: "nothing is running — there is no pod to kill", isError: true))
        #expect(store.killState == .idle)
        #expect(store.pod != nil)
    }

    @Test func botBusyReenablesWithoutResending() async {
        let routes = Routes(pods: [Self.pod(lease: true)],
                            kills: [Self.refusal(503, "bot_busy", "the bot is busy")])
        let store = make(routes)
        await store.kill(runID: "tg-1000")
        #expect(store.killNotice?.text == "The bot is busy — tap Kill again.")
        #expect(store.killState == .idle)
        #expect(killPosts.count == 1)
    }

    @Test func droppedConnectionIsNeverResentAndThePodTellsWhatHappened() async {
        let routes = Routes(pods: [Self.pod(lease: true)], kills: [Self.dropped])
        let store = make(routes)
        await store.refresh()
        routes.setPods([Self.pod(lease: true, killRunning: true), Self.pod(lastKill: Self.killed)])
        await store.kill(runID: "tg-1000")
        #expect(killPosts.count == 1)
        #expect(store.killNotice?.text == "pod destroyed and verified gone")
    }

    @Test func ambiguousKillWithNoTraceSaysKillingAgainIsSafe() async {
        let routes = Routes(pods: [Self.pod(lease: true)], kills: [Self.dropped])
        let store = make(routes)
        await store.refresh()
        await store.kill(runID: "tg-1000")
        #expect(killPosts.count == 1)
        #expect(store.killNotice?.isError == true)
        #expect(store.killNotice?.text.contains("killing again is safe") == true)
        #expect(podReads == 1 + 3)          // the initial read + three probes
        #expect(store.killState == .idle)
    }

    @Test func aKillThatEndsWithoutANewResultSaysSo() async {
        let earlier = Self.lastKill(at: 1_789_000_000, ok: true, code: "killed", message: "old")
        let routes = Routes(pods: [Self.pod(lease: true, lastKill: earlier)], kills: [Self.accepted])
        let store = make(routes)
        await store.refresh()
        routes.setPods([Self.pod(lastKill: earlier)])
        await store.kill(runID: "tg-1000")
        #expect(store.killNotice == .init(text: "The kill ended without a result — check Telegram.", isError: true))
    }

    @Test func pollingStopsAtFiveMinutesAndCheckAgainFinishes() async {
        let routes = Routes(pods: [Self.pod(lease: true)], kills: [Self.accepted])
        let store = make(routes, step: 100)
        await store.refresh()
        routes.setPods([Self.pod(lease: true, killRunning: true)])
        await store.kill(runID: "tg-1000")
        #expect(store.killStillRunning)
        #expect(store.killState == .idle)
        #expect(store.killNotice?.text == "The kill is still running on the VPS.")
        routes.setPods([Self.pod(lastKill: Self.killed)])
        await store.checkKillAgain()
        #expect(!store.killStillRunning)
        #expect(store.killNotice?.text == "pod destroyed and verified gone")
    }

    @Test func killIsDrawnForALeaseOrALiveRunOnly() async {
        let routes = Routes(pods: [Self.pod()])
        let store = make(routes)
        await store.refresh()
        #expect(!store.showsKill())
        #expect(store.showsKill(runStatus: .phaseA))
        #expect(!store.showsKill(runStatus: .done))
        routes.setPods([Self.pod(lease: true)])
        await store.refresh()
        #expect(store.showsKill())
    }

    // MARK: banner

    @Test func unverifiedDestroyAndWorkerErrorRaiseTheBanner() async {
        for code in ["destroy_unverified", "error"] {
            let routes = Routes(pods: [Self.pod(lastKill: Self.lastKill(at: 5, ok: false, code: code, message: "check it"))])
            let store = make(routes)
            await store.refresh()
            #expect(store.unverifiedKill?.code == code)
        }
    }

    @Test func successfulOrHarmlessKillsRaiseNoBanner() async {
        let cases: [(Bool, String)] = [(true, "killed"), (true, "phase_a_stopped"),
                                       (false, "nothing_running"), (false, "phase_a_finished")]
        for (ok, code) in cases {
            let routes = Routes(pods: [Self.pod(lastKill: Self.lastKill(at: 5, ok: ok, code: code, message: "m"))])
            let store = make(routes)
            await store.refresh()
            #expect(store.unverifiedKill == nil)
        }
    }

    @Test func acknowledgementHidesThatKillOnlyAndSurvivesARelaunch() async {
        let defaults = Self.freshDefaults()
        let first = Self.lastKill(at: 5, ok: false, code: "destroy_unverified", message: "check it")
        let routes = Routes(pods: [Self.pod(lastKill: first)])
        let store = make(routes, defaults: defaults)
        await store.refresh()
        store.acknowledgeUnverifiedKill()
        #expect(store.unverifiedKill == nil)
        let relaunched = make(routes, defaults: defaults)
        await relaunched.refresh()
        #expect(relaunched.unverifiedKill == nil)
        routes.setPods([Self.pod(lastKill: Self.lastKill(at: 9, ok: false, code: "destroy_unverified", message: "again"))])
        await relaunched.refresh()
        #expect(relaunched.unverifiedKill?.at == 9)
    }

    // MARK: migration poll

    @Test func migrationPollRefreshesOnlyWhileAMigrationRuns() async {
        let running = #"{"running": true, "phase": "copy", "to_dc": "EU-CZ-1", "started_at": 1790000000, "bytes_copied": 1, "total_bytes": 4}"#
        let routes = Routes(pods: [Self.pod(migration: running), Self.pod()])
        StubURLProtocol.install { routes.answer($0) }
        let sleeps = Counter()
        let store = PodStore(client: TestSupport.client(), defaults: Self.freshDefaults(),
                             sleep: { _ in if sleeps.increment() > 3 { throw CancellationError() } })
        await store.refresh()
        await store.pollMigration()
        #expect(podReads == 2)
    }
}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `swift test --filter PodStoreTests`
Expected: compile errors — no `defaults:`/`sleep:`/`now:` init, no `kill`, `killNotice`, `unverifiedKill`, `pollMigration`.

- [ ] **Step 3: Implement** — replace `PodStore.swift` entirely:

```swift
import Foundation
import Observation

/// `GET /v1/pod`, the kill and the unverified-kill banner (Phase 5 design §4).
///
/// Kill deliberately bypasses `SpendGate`: the server makes a repeated kill
/// harmless (`kill_in_progress`, `nothing_running`, and a live re-check under
/// `BOT_LOCK`), and a kill must never wait behind an unanswered confirm —
/// that is exactly when a pod may be billing. So each tap mints its own key,
/// nothing is journalled, and an ambiguous answer is never resent: the pod
/// status says what happened.
@MainActor @Observable
public final class PodStore {
    public enum KillState: Equatable, Sendable { case idle, sending, killing }

    public struct Notice: Equatable, Sendable {
        public let text: String
        public let isError: Bool
    }

    public private(set) var pod: PodStatus?
    public private(set) var error: APIError?
    public private(set) var lastSuccess: Date?
    public private(set) var killState: KillState = .idle
    public private(set) var killNotice: Notice?
    /// The 5-minute poll cap was reached with the kill still running.
    public private(set) var killStillRunning = false

    /// `last_kill.at` the user said they checked by hand (design §4).
    static let ackKey = "pod.ackedKillAt"
    static let killPollInterval: Duration = .seconds(2)
    /// The server's kill takes up to ~210 s (30 s drain wait + 180 s destroy).
    static let killPollCap: TimeInterval = 300
    static let ambiguousProbes = 3
    /// `GET /v1/pod` is files only; a migration takes ~25–30 min.
    static let migrationPollInterval: Duration = .seconds(10)
    /// A worker that raised mid-destroy may have left a pod billing too.
    static let unverifiedCodes: Set<String> = ["destroy_unverified", "error"]

    private let client: APIClient
    private let defaults: UserDefaults
    private let sleep: @Sendable (Duration) async throws -> Void
    private let now: @Sendable () -> Date
    private let makeKey: @Sendable () -> String
    private var ackedKillAt: Double?
    /// `last_kill.at` before the current kill was sent — the server's own
    /// clock, so a changed value means a new result without any skew.
    private var killBaseline: Double?

    public init(client: APIClient, defaults: UserDefaults = .standard,
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
                now: @escaping @Sendable () -> Date = { Date() },
                makeKey: @escaping @Sendable () -> String = { UUID().uuidString }) {
        self.client = client
        self.defaults = defaults
        self.sleep = sleep
        self.now = now
        self.makeKey = makeKey
        ackedKillAt = defaults.object(forKey: Self.ackKey) as? Double
    }

    public var isStale: Bool { pod != nil && error != nil }
    public var isKilling: Bool { killState != .idle }

    /// `GET /v1/pod` is files and memory only on the server — cheap to call.
    public func refresh() async {
        do {
            pod = try await client.get(PodStatus.self, "v1", "pod")
            error = nil
            lastSuccess = .now
        } catch {
            self.error = error
        }
    }

    // MARK: kill

    /// Whether to draw Kill. The server decides (`nothing_running`); this only
    /// draws the button.
    public func showsKill(runStatus: RunStatus? = nil) -> Bool {
        guard let pod, pod.runId != nil else { return false }
        return pod.lease != nil || pod.killRunning || runStatus?.isLive == true
    }

    public func kill(runID: String) async {
        guard killState == .idle else { return }
        killState = .sending
        killNotice = nil
        killStillRunning = false
        killBaseline = pod?.lastKill?.at
        let raw = await client.spendPost(["v1", "runs", runID, "kill"], body: Data("{}".utf8),
                                         idempotencyKey: makeKey(), timeout: 90)
        switch Self.classifyKill(raw) {
        case .started:
            killState = .killing
            await followKill()
        case let .refused(code, text):
            killState = .idle
            killNotice = Notice(text: text, isError: true)
            if code == "nothing_running" { await refresh() }
        case .busy:
            killState = .idle
            killNotice = Notice(text: "The bot is busy — tap Kill again.", isError: true)
        case .ambiguous(let detail):
            await probeAfterAmbiguousKill(detail)
        }
    }

    /// After the 5-minute cap: one read, then either finish or keep following.
    public func checkKillAgain() async {
        guard killState == .idle, killStillRunning else { return }
        await refresh()
        guard error == nil, let pod else { return }
        killStillRunning = false
        if pod.killRunning {
            killState = .killing
            await followKill()
        } else {
            finishKill()
        }
    }

    /// Never resends: probes the pod three times, 2 s apart.
    private func probeAfterAmbiguousKill(_ detail: String) async {
        for _ in 0..<Self.ambiguousProbes {
            do { try await sleep(Self.killPollInterval) } catch { break }
            await refresh()
            guard error == nil, let pod else { continue }
            if pod.killRunning {
                killState = .killing
                await followKill()
                return
            }
            if pod.lastKill?.at != killBaseline {
                finishKill()
                return
            }
        }
        killState = .idle
        killNotice = Notice(text: "Couldn't tell whether the kill started (\(detail)) — killing again is safe.",
                            isError: true)
    }

    private func followKill() async {
        let deadline = now().addingTimeInterval(Self.killPollCap)
        while true {
            await refresh()
            if error == nil, let pod, !pod.killRunning {
                finishKill()
                return
            }
            if now() >= deadline {
                killState = .idle
                killStillRunning = true
                killNotice = Notice(text: "The kill is still running on the VPS.", isError: false)
                return
            }
            do { try await sleep(Self.killPollInterval) } catch {
                killState = .idle
                return
            }
        }
    }

    /// An unchanged `last_kill.at` means the bot restarted mid-kill: the
    /// thread died and wrote nothing.
    private func finishKill() {
        killState = .idle
        if let kill = pod?.lastKill, kill.at != killBaseline {
            killNotice = Notice(text: kill.message, isError: !kill.ok)
        } else {
            killNotice = Notice(text: "The kill ended without a result — check Telegram.", isError: true)
        }
    }

    enum KillAnswer: Equatable {
        case started, busy
        case refused(code: String, text: String)
        case ambiguous(String)
    }

    static func classifyKill(_ raw: RawSpendResponse) -> KillAnswer {
        switch raw {
        case .transport(let detail):
            return .ambiguous(detail)
        case let .http(status, body):
            if (200..<300).contains(status) { return .started }
            if let envelope = try? MotionJSON.decoder.decode(SpendGate.Envelope.self, from: body) {
                let code = envelope.error.code
                if status == 503 && code == "bot_busy" { return .busy }
                if code == "kill_in_progress" { return .started }
                if status >= 500 { return .ambiguous("HTTP \(status) \(code)") }
                return .refused(code: code, text: APIError.server(
                    status: status, code: code, message: envelope.error.message).userMessage)
            }
            if status >= 500 { return .ambiguous("HTTP \(status) without an API answer") }
            if status == 403 {
                return .refused(code: "access_denied", text: APIError.accessDenied(status: 403).userMessage)
            }
            return .refused(code: "http_\(status)", text: "HTTP \(status)")
        }
    }

    // MARK: banner

    /// A kill that could not verify the destroy, not yet acknowledged. The
    /// server clears the lease either way (`_do_kill`), so "no lease" proves
    /// nothing; only the user or a later successful kill clears this.
    public var unverifiedKill: KillResult? {
        guard let kill = pod?.lastKill, !kill.ok, Self.unverifiedCodes.contains(kill.code),
              kill.at != ackedKillAt else { return nil }
        return kill
    }

    public func acknowledgeUnverifiedKill() {
        guard let kill = unverifiedKill else { return }
        ackedKillAt = kill.at
        defaults.set(kill.at, forKey: Self.ackKey)
    }

    // MARK: migration

    /// Every 10 s while a migration runs; the caller's task scopes it to a
    /// visible, active Pod tab.
    public func pollMigration() async {
        while !Task.isCancelled {
            do { try await sleep(Self.migrationPollInterval) } catch { return }
            if pod?.migration?.running == true { await refresh() }
        }
    }
}
```

- [ ] **Step 4: Run the whole suite**

Run: `swift test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/PodStore.swift ios/MotionKit/Tests/MotionKitTests/PodStoreTests.swift
git commit -m "feat(ios): kill from the phone with a verified-destroy banner

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: GpuStore and BalanceStore

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Stores/GpuStore.swift`
- Create: `ios/MotionKit/Sources/MotionKit/Stores/BalanceStore.swift`
- Create: `ios/MotionKit/Tests/MotionKitTests/GpuAndBalanceStoreTests.swift`

**Interfaces:**
- Consumes: `GpuStock`, `GpuSelectionRequest`, `GpuSelection`, `Balance`, `VastBalance`, `Format.runway` (Task 1); `APIClient.put` (Task 2); `PodStore.refresh()` (Task 3).
- Produces:
  - `GpuStore(client:pod:)`: `stock`, `error`, `isLoading`, `selecting: String?`, `message`, `isStale`, `load(force: Bool = false) async`, `@discardableResult select(_ gpu: String, whileSpending: Bool) async -> Bool`.
  - `BalanceStore(client:)`: `balance`, `vast: VastBalance?`, `error`, `isLoading`, `isLoadingVast`, `load() async`, `loadVast() async`, `runpodLine: String?`, `vastLine: String?`.

- [ ] **Step 1: Write the failing tests** — create `GpuAndBalanceStoreTests.swift`:

```swift
import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct GpuAndBalanceStoreTests {
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _stock = TestSupport.json(Fixtures.gpuStock)
        private var _put = TestSupport.json(#"{"gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090"}"#)
        private var _balance = Fixtures.balance
        private var _vast = Fixtures.balanceVast
        var stock: (Int, [String: String], Data) {
            get { lock.withLock { _stock } } set { lock.withLock { _stock = newValue } }
        }
        var put: (Int, [String: String], Data) {
            get { lock.withLock { _put } } set { lock.withLock { _put = newValue } }
        }
        var balance: String { get { lock.withLock { _balance } } set { lock.withLock { _balance = newValue } } }
        var vast: String { get { lock.withLock { _vast } } set { lock.withLock { _vast = newValue } } }

        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            switch (request.httpMethod ?? "GET", request.url?.path ?? "") {
            case ("GET", "/v1/gpu/stock"): return stock
            case ("PUT", "/v1/pod/gpu"): return put
            case ("GET", "/v1/pod"): return TestSupport.json(Fixtures.podIdle)
            case ("GET", "/v1/balance"):
                return TestSupport.json(request.url?.query == "vast=1" ? vast : balance)
            default: return (404, [:], Data())
            }
        }
    }

    private func install(_ routes: Routes) { StubURLProtocol.install { routes.answer($0) } }

    private func gpuStore(_ routes: Routes) -> (GpuStore, PodStore) {
        install(routes)
        let pod = PodStore(client: TestSupport.client())
        return (GpuStore(client: TestSupport.client(), pod: pod), pod)
    }

    // MARK: GPU

    @Test func stockLoadsCachedAndForceAsksForFresh() async {
        let (store, _) = gpuStore(Routes())
        await store.load()
        #expect(store.stock?.gpus.count == 5)
        #expect(StubURLProtocol.requests.last?.url?.query == nil)
        await store.load(force: true)
        #expect(StubURLProtocol.requests.last?.url?.query == "force=1")
    }

    @Test func unreachableRunpodctlKeepsTheLastStock() async {
        let routes = Routes()
        let (store, _) = gpuStore(routes)
        await store.load()
        routes.stock = TestSupport.json(
            #"{"error": {"code": "upstream_unavailable", "message": "couldn't reach runpodctl: timeout"}}"#, status: 502)
        await store.load(force: true)
        #expect(store.stock?.gpus.count == 5)
        #expect(store.isStale)
    }

    @Test func selectPutsTheCatalogIdThenRefreshesThePod() async throws {
        let (store, pod) = gpuStore(Routes())
        await store.load()
        let changed = await store.select("NVIDIA GeForce RTX 4090", whileSpending: false)
        #expect(changed)
        #expect(store.stock?.selected == "NVIDIA GeForce RTX 4090")
        let requests = StubURLProtocol.requests
        let putIndex = try #require(requests.firstIndex { $0.httpMethod == "PUT" })
        let body = try #require(JSONSerialization.jsonObject(with: requests[putIndex].httpBody ?? Data()) as? [String: String])
        #expect(body == ["gpu": "NVIDIA GeForce RTX 4090"])
        let podIndex = try #require(requests.lastIndex { $0.url?.path == "/v1/pod" })
        #expect(podIndex > putIndex)
        #expect(pod.pod != nil)
    }

    @Test func selectIsRefusedWhileASpendIsInFlight() async {
        let (store, _) = gpuStore(Routes())
        await store.load()
        let changed = await store.select("NVIDIA GeForce RTX 4090", whileSpending: true)
        #expect(!changed)
        #expect(store.message?.contains("in flight") == true)
        #expect(!StubURLProtocol.requests.contains { $0.httpMethod == "PUT" })
    }

    @Test func selectingTheCurrentGpuSendsNothing() async {
        let (store, _) = gpuStore(Routes())
        await store.load()
        let changed = await store.select("NVIDIA GeForce RTX 5090", whileSpending: false)
        #expect(!changed)
        #expect(!StubURLProtocol.requests.contains { $0.httpMethod == "PUT" })
    }

    @Test func busyBotKeepsTheSelectionAndSaysSo() async {
        let routes = Routes()
        routes.put = TestSupport.json(#"{"error": {"code": "bot_busy", "message": "the bot is busy"}}"#, status: 503)
        let (store, _) = gpuStore(routes)
        await store.load()
        let changed = await store.select("NVIDIA GeForce RTX 4090", whileSpending: false)
        #expect(!changed)
        #expect(store.message == "The bot is busy. Try again in a moment.")
        #expect(store.stock?.selected == "NVIDIA GeForce RTX 5090")
    }

    // MARK: balance

    @Test func balanceNeverAsksForVastUnlessTapped() async {
        install(Routes())
        let store = BalanceStore(client: TestSupport.client())
        #expect(store.runpodLine == nil)
        await store.load()
        #expect(StubURLProtocol.requests.last?.url?.query == nil)
        #expect(store.runpodLine == "$12.34 · ≈ 12h 28m at $0.99/h")
        #expect(store.vastLine == nil)
    }

    @Test func unreadableRunpodIsNeverZero() async {
        let routes = Routes()
        routes.balance = Fixtures.balanceRunpodDown
        install(routes)
        let store = BalanceStore(client: TestSupport.client())
        await store.load()
        #expect(store.runpodLine == "Couldn't read the RunPod balance")
        #expect(store.balance?.errors == ["couldn't reach runpodctl: timeout"])
    }

    @Test func vastCreditOnlyOnRequestAndKeptAcrossReloads() async {
        install(Routes())
        let store = BalanceStore(client: TestSupport.client())
        await store.loadVast()
        #expect(StubURLProtocol.requests.last?.url?.query == "vast=1")
        #expect(store.vastLine == "$7.50")
        await store.load()
        #expect(store.vastLine == "$7.50")
    }

    @Test func unreadableVastIsNeverZero() async {
        let routes = Routes()
        routes.vast = Fixtures.balanceVastDown
        install(routes)
        let store = BalanceStore(client: TestSupport.client())
        await store.loadVast()
        #expect(store.vastLine == "Couldn't read the Vast credit")
        #expect(store.balance?.runpod?.lowRunway == true)
    }
}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `swift test --filter GpuAndBalanceStoreTests`
Expected: compile errors — `GpuStore`, `BalanceStore` undefined.

- [ ] **Step 3: Implement** — create `GpuStore.swift`:

```swift
import Foundation
import Observation

/// `GET /v1/gpu/stock` and `PUT /v1/pod/gpu` (Phase 5 design §5). Shared by
/// the Pod tab, the rent panel's Change GPU sheet and the migrate sheet.
@MainActor @Observable
public final class GpuStore {
    public private(set) var stock: GpuStock?
    public private(set) var error: APIError?
    public private(set) var isLoading = false
    /// The catalog id being PUT, while it is in flight.
    public private(set) var selecting: String?
    public private(set) var message: String?

    private let client: APIClient
    private let pod: PodStore

    public init(client: APIClient, pod: PodStore) {
        self.client = client
        self.pod = pod
    }

    public var isStale: Bool { stock != nil && error != nil }

    /// `force` only from pull-to-refresh or Retry: uncached it is a runpodctl
    /// round trip (~30 s worst case). A 502 keeps the last good list.
    public func load(force: Bool = false) async {
        isLoading = true
        defer { isLoading = false }
        do {
            let fresh = force
                ? try await client.get(GpuStock.self, query: [URLQueryItem(name: "force", value: "1")],
                                       timeout: 60, "v1", "gpu", "stock")
                : try await client.get(GpuStock.self, timeout: 60, "v1", "gpu", "stock")
            stock = fresh
            error = nil
        } catch {
            self.error = error
        }
    }

    /// True when the server changed `.env`'s GPU. Not a spend (no key), but
    /// refused while one is in flight: the confirm being sent was priced for
    /// the current card.
    @discardableResult
    public func select(_ gpu: String, whileSpending spending: Bool) async -> Bool {
        guard selecting == nil, gpu != stock?.selected else { return false }
        guard !spending else {
            message = "A spend request is in flight — wait for its answer before changing the GPU."
            return false
        }
        selecting = gpu
        message = nil
        defer { selecting = nil }
        do {
            let chosen = try await client.put(GpuSelection.self, body: GpuSelectionRequest(gpu: gpu),
                                              "v1", "pod", "gpu")
            stock?.selected = chosen.gpu
            await pod.refresh()
            return true
        } catch {
            message = error.userMessage
            return false
        }
    }
}
```

Create `BalanceStore.swift`:

```swift
import Foundation
import Observation

/// `GET /v1/balance[?vast=1]` (Phase 5 design §5). The Vast credit is its own
/// ~30 s subprocess on the server, so it is read only on an explicit tap.
@MainActor @Observable
public final class BalanceStore {
    public private(set) var balance: Balance?
    /// The last Vast credit read, kept across plain reloads (which omit it).
    public private(set) var vast: VastBalance?
    public private(set) var error: APIError?
    public private(set) var isLoading = false
    public private(set) var isLoadingVast = false

    private let client: APIClient

    public init(client: APIClient) { self.client = client }

    public func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            balance = try await client.get(Balance.self, timeout: 45, "v1", "balance")
            error = nil
        } catch {
            self.error = error
        }
    }

    public func loadVast() async {
        isLoadingVast = true
        defer { isLoadingVast = false }
        do {
            let fresh = try await client.get(Balance.self, query: [URLQueryItem(name: "vast", value: "1")],
                                             timeout: 90, "v1", "balance")
            balance = fresh
            vast = fresh.vast
            error = nil
        } catch {
            self.error = error
        }
    }

    /// nil until a read succeeded. An unreadable account is never "$0".
    public var runpodLine: String? {
        guard let balance else { return nil }
        guard let runpod = balance.runpod else { return "Couldn't read the RunPod balance" }
        return "\(Format.usd(runpod.usd)) · ≈ \(Format.runway(hours: runpod.runwayHours)) at \(Format.usd(runpod.usdPerHr))/h"
    }

    public var vastLine: String? {
        guard let vast else { return nil }
        return vast.usd.map(Format.usd) ?? "Couldn't read the Vast credit"
    }
}
```

- [ ] **Step 4: Run the whole suite**

Run: `swift test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/GpuStore.swift ios/MotionKit/Sources/MotionKit/Stores/BalanceStore.swift \
  ios/MotionKit/Tests/MotionKitTests/GpuAndBalanceStoreTests.swift
git commit -m "feat(ios): GPU stock and choice, RunPod and Vast balances

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `SpendIntent.migrate` and RunFlow's side of it

**Files:**
- Modify: `ios/MotionKit/Sources/MotionKit/Money/SpendIntent.swift`
- Modify: `ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift`
- Test: `ios/MotionKit/Tests/MotionKitTests/SpendIntentTests.swift`, `IdempotencyLedgerTests.swift`, `RunFlowTests.swift`

**Interfaces:**
- Produces: `SpendKind.migrate`; `SpendIntent.migrate(toDc: String, confirmToken: String)` (path `["v1","pod","migrate"]`, body `{to_dc, confirm_token}`, `runID == nil`); `RunFlow.reloadPanelAfterGpuChange() async`. `RunFlow.replayPendingOnce()` and `recheck()` leave a pending `.migrate` alone.

- [ ] **Step 1: Write the failing tests**

`SpendIntentTests`:

```swift
    @Test func migrateCarriesDestinationAndTokenAndNoRun() throws {
        let intent = SpendIntent.migrate(toDc: "EU-CZ-1", confirmToken: "tok-abc")
        #expect(intent.path == ["v1", "pod", "migrate"])
        #expect(intent.kind == .migrate)
        #expect(intent.runID == nil)
        let body = try object(intent)
        #expect(body["to_dc"] as? String == "EU-CZ-1")
        #expect(body["confirm_token"] as? String == "tok-abc")
        #expect(body.keys.count == 2)
    }
```

`IdempotencyLedgerTests`:

```swift
    @Test func migrateEntryRoundTrips() throws {
        let l = ledger()
        let migrate = SpendLedgerEntry(key: "K9", intent: .migrate(toDc: "EU-CZ-1", confirmToken: "tok-abc"),
                                       label: "Migrate volume to EU-CZ-1",
                                       createdAt: Date(timeIntervalSince1970: 1_790_000_000))
        try l.save(migrate)
        #expect(try l.load() == migrate)
    }
```

`RunFlowTests`:

```swift
    @Test func aPendingMigrateIsLeftToMigrateFlow() async {
        let entry = SpendLedgerEntry(key: "K1", intent: .migrate(toDc: "EU-CZ-1", confirmToken: "t"),
                                     label: "Migrate volume to EU-CZ-1", createdAt: .now)
        let gate = FakeSpendGate(pending: entry, replay: .accepted(runID: nil, outcome: "started"))
        let flow = make(Routes(), gate: gate)
        await flow.replayPendingOnce()
        #expect(await gate.replays == 0)
        #expect(flow.pendingNotice == nil)
        #expect(flow.phase == .loading)
    }

    @Test func gpuChangeClearsTheQuoteAndRereadsThePanel() async {
        let routes = Routes()
        routes.setPanels([Fixtures.rentPanel, Fixtures.rentPanelFresh])
        let gate = FakeSpendGate([.accepted(runID: "tg-1000", outcome: "started")])
        let flow = make(routes, gate: gate)
        await flow.start(.newJob)
        await flow.continueToRent()
        #expect(flow.panel?.panelToken == "1790000000123.4.9")
        await flow.reloadPanelAfterGpuChange()
        #expect(flow.panel?.panelToken == "1790000000999.1.10")
        #expect(await gate.intents.isEmpty)          // a GPU change never confirms by itself
        await flow.confirm()
        guard case let .confirm(_, _, token, _, _)? = await gate.intents.first else {
            Issue.record("no confirm was sent")
            return
        }
        #expect(token == "1790000000999.1.10")
    }
```

- [ ] **Step 2: Run to verify failure**

Run: `swift test --filter "SpendIntentTests|IdempotencyLedgerTests|RunFlowTests"`
Expected: compile errors — no `.migrate`, no `reloadPanelAfterGpuChange`.

- [ ] **Step 3: Implement**

`SpendIntent.swift`: add `migrate` to `SpendKind` (`case phaseA, regen, confirm, resume, migrate`), and to `SpendIntent`:

```swift
    /// `POST /v1/pod/migrate`. Not a GPU spend, but the most destructive call
    /// in the API (the source volume is deleted once the copy verifies), so it
    /// takes the same one-key-per-tap journal as every spend.
    case migrate(toDc: String, confirmToken: String)
```

Extend each switch:

```swift
        case .migrate: .migrate                                    // in `kind`
        case .phaseA, .migrate: nil                                // in `runID` (replace `case .phaseA: nil`)
        case .migrate: ["v1", "pod", "migrate"]                    // in `path`
        case let .migrate(toDc, confirmToken):                     // in `body()`
            object["to_dc"] = toDc
            object["confirm_token"] = confirmToken
```

Update the doc comment on `SpendIntent` to "One money- or data-destroying call…" and on `SpendGate` ("The only sender of `phase-a`, `regen`, `confirm`, `resume` and `migrate`.").

`RunFlowStore.swift`:

In `replayPendingOnce()`, after `guard let entry = await gate.pending() else { return }`:

```swift
        // A pending migrate belongs to MigrateFlow; AppModel routes it there.
        guard entry.intent.kind != .migrate else { return }
```

In `recheck()`, after the `guard let intent = …` line:

```swift
        guard intent.kind != .migrate else {
            message = "The pending request is a volume migration — check it from the Pod tab."
            return
        }
```

In `apply(_:intent:)`'s `.accepted` inner `switch kind`, add:

```swift
            case .migrate:
                break   // never sent by RunFlow
```

Add after `canConfirm`:

```swift
    /// After `PUT /v1/pod/gpu`: the old quote and `panel_token` priced another
    /// card, so both go before the re-read — Confirm reappears only with the
    /// new price and needs a new tap. The pod is re-read too, so Retry rental
    /// names the new card.
    public func reloadPanelAfterGpuChange() async {
        panel = nil
        await refreshPod()
        await loadPanel(force: false)
    }
```

- [ ] **Step 4: Run the whole suite**

Run: `swift test`
Expected: all pass. The compiler flags any other exhaustive `switch` over `SpendKind`; there is none outside the three edited here.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Money/SpendIntent.swift ios/MotionKit/Sources/MotionKit/Money/SpendGate.swift \
  ios/MotionKit/Sources/MotionKit/Stores/RunFlowStore.swift ios/MotionKit/Tests/MotionKitTests/SpendIntentTests.swift \
  ios/MotionKit/Tests/MotionKitTests/IdempotencyLedgerTests.swift ios/MotionKit/Tests/MotionKitTests/RunFlowTests.swift
git commit -m "feat(ios): migrate as a journalled intent; re-read the panel after a GPU change

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: MigrateFlow

**Files:**
- Create: `ios/MotionKit/Sources/MotionKit/Stores/MigrateFlow.swift`
- Create: `ios/MotionKit/Tests/MotionKitTests/MigrateFlowTests.swift`

**Interfaces:**
- Consumes: `MigrateAsk`, `MigrateAskRequest` (Task 1), `APIClient.post(_:body:timeout:_:)` (Task 2), `PodStore.refresh()` (Task 3), `SpendIntent.migrate` (Task 5), `SpendSending`, `FakeSpendGate`.
- Produces: `MigrateFlow(client:gate:pod:now:)` with `enum Step { choose, asking(toDc:), confirm(MigrateAsk), started(toDc:), outcomeUnknown }`, `step`, `destination`, `message`, `inFlightLabel`, `retryNote`, `needsRecheck`, `pendingNotice`, `typed` (settable), `isSending`, `currentAsk`, `secondsLeft(at:) -> Int`, `canMigrate(at:) -> Bool`, `static blocker(pod:runStatus:) -> String?`, `begin(preselect:)`, `ask(toDc:) async`, `migrate() async`, `recheck() async`, `replayPendingOnce() async`.

- [ ] **Step 1: Write the failing tests** — create `MigrateFlowTests.swift`:

```swift
import Foundation
import Testing
@testable import MotionKit

extension URLProtocolTests {
@Suite @MainActor struct MigrateFlowTests {
    final class Routes: @unchecked Sendable {
        private let lock = NSLock()
        private var _ask = TestSupport.json(Fixtures.migrateAsk)
        var ask: (Int, [String: String], Data) {
            get { lock.withLock { _ask } } set { lock.withLock { _ask = newValue } }
        }
        func answer(_ request: URLRequest) -> (Int, [String: String], Data) {
            switch request.url?.path ?? "" {
            case "/v1/pod/migrate/ask": return ask
            case "/v1/pod": return TestSupport.json(Fixtures.podIdle)
            default: return (404, [:], Data())
            }
        }
    }

    final class Clock: @unchecked Sendable {
        private let lock = NSLock()
        private var current = Date(timeIntervalSince1970: 1_790_000_000)
        var now: Date { lock.withLock { current } }
        func advance(_ seconds: TimeInterval) { lock.withLock { current += seconds } }
    }

    private func make(_ routes: Routes = Routes(), gate: any SpendSending = FakeSpendGate(),
                      clock: Clock = Clock()) -> MigrateFlow {
        StubURLProtocol.install { routes.answer($0) }
        let client = TestSupport.client()
        return MigrateFlow(client: client, gate: gate, pod: PodStore(client: client), now: { clock.now })
    }

    private func askedAndTyped(_ gate: FakeSpendGate, clock: Clock = Clock()) async -> MigrateFlow {
        let flow = make(gate: gate, clock: clock)
        await flow.ask(toDc: "EU-CZ-1")
        flow.typed = "EU-CZ-1"
        return flow
    }

    @Test func askShowsTheWarningAndStartsTheCountdown() async throws {
        let clock = Clock()
        let flow = make(clock: clock)
        await flow.ask(toDc: "EU-CZ-1")
        #expect(flow.currentAsk?.confirmToken == "tok-abc")
        #expect(flow.secondsLeft(at: clock.now) == 585)       // 600 − the 15 s margin
        let post = try #require(StubURLProtocol.requests.last { $0.url?.path == "/v1/pod/migrate/ask" })
        #expect(post.value(forHTTPHeaderField: "Idempotency-Key") == nil)
        let body = try #require(JSONSerialization.jsonObject(with: post.httpBody ?? Data()) as? [String: String])
        #expect(body == ["to_dc": "EU-CZ-1"])
    }

    @Test func askRefusalShowsTheServerText() async {
        let routes = Routes()
        routes.ask = TestSupport.json(
            #"{"error": {"code": "run_active", "message": "a pod is live — stop it before moving the volume"}}"#, status: 409)
        let flow = make(routes)
        await flow.ask(toDc: "EU-CZ-1")
        #expect(flow.message == "a pod is live — stop it before moving the volume")
        #expect(flow.step == .choose)
        #expect(flow.destination == "EU-CZ-1")
    }

    @Test func typedConfirmationMustMatchExactly() async {
        let clock = Clock()
        let flow = make(clock: clock)
        await flow.ask(toDc: "EU-CZ-1")
        flow.typed = "eu-cz-1"
        #expect(!flow.canMigrate(at: clock.now))
        flow.typed = "EU-CZ-1 "
        #expect(!flow.canMigrate(at: clock.now))
        flow.typed = "EU-CZ-1"
        #expect(flow.canMigrate(at: clock.now))
    }

    @Test func anExpiredConfirmationSendsNothing() async {
        let clock = Clock()
        let gate = FakeSpendGate([.accepted(runID: nil, outcome: "started")])
        let flow = await askedAndTyped(gate, clock: clock)
        clock.advance(585)
        #expect(!flow.canMigrate(at: clock.now))
        #expect(flow.secondsLeft(at: clock.now) == 0)
        await flow.migrate()
        #expect(await gate.intents.isEmpty)
    }

    @Test func migrateSendsOneIntentThroughTheGate() async {
        let gate = FakeSpendGate([.accepted(runID: nil, outcome: "started")])
        let flow = await askedAndTyped(gate)
        await flow.migrate()
        #expect(await gate.intents == [.migrate(toDc: "EU-CZ-1", confirmToken: "tok-abc")])
        #expect(await gate.labels == ["Migrate volume to EU-CZ-1"])
        #expect(flow.step == .started(toDc: "EU-CZ-1"))
        #expect(StubURLProtocol.requests.contains { $0.url?.path == "/v1/pod" })
    }

    @Test func aStaleTokenGoesBackToAsk() async {
        let gate = FakeSpendGate([.refused(status: 409, code: "bad_confirm_token",
                                           message: "that confirmation is no longer valid — ask again",
                                           panelToken: nil)])
        let flow = await askedAndTyped(gate)
        await flow.migrate()
        #expect(flow.step == .choose)
        #expect(flow.destination == "EU-CZ-1")
        #expect(flow.typed == "")
        #expect(flow.message == "That confirmation expired — ask again.")
    }

    @Test func outcomeUnknownIsNeverRetried() async {
        let gate = FakeSpendGate([.outcomeUnknown])
        let flow = await askedAndTyped(gate)
        await flow.migrate()
        #expect(flow.step == .outcomeUnknown)
        #expect(await gate.intents.count == 1)
        #expect(await gate.rechecks == 0)
        #expect(flow.message?.contains("Couldn't tell whether the migration started") == true)
    }

    @Test func unreachableIsCheckedWithTheSavedKey() async {
        let gate = FakeSpendGate([.unreachable(detail: "timed out"), .accepted(runID: nil, outcome: "started")])
        let flow = await askedAndTyped(gate)
        await flow.migrate()
        #expect(flow.needsRecheck)
        #expect(!flow.canMigrate(at: .now))
        await flow.recheck()
        #expect(await gate.rechecks == 1)
        #expect(await gate.intents.count == 1)
        #expect(flow.step == .started(toDc: "EU-CZ-1"))
        #expect(!flow.needsRecheck)
    }

    @Test func launchReplayResolvesAPendingMigrateOnce() async {
        let entry = SpendLedgerEntry(key: "K1", intent: .migrate(toDc: "EU-CZ-1", confirmToken: "tok-abc"),
                                     label: "Migrate volume to EU-CZ-1", createdAt: .now)
        let gate = FakeSpendGate(pending: entry, replay: .accepted(runID: nil, outcome: "started"))
        let flow = make(gate: gate)
        await flow.replayPendingOnce()
        await flow.replayPendingOnce()
        #expect(await gate.replays == 1)
        #expect(flow.step == .started(toDc: "EU-CZ-1"))
        #expect(flow.pendingNotice == nil)
    }

    @Test func blockerNamesWhyAMigrationCannotStart() throws {
        let decoder = MotionJSON.decoder
        let idle = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podIdle))
        let live = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podLive))
        let migrating = try decoder.decode(PodStatus.self, from: Fixtures.data(Fixtures.podMigrating))
        #expect(MigrateFlow.blocker(pod: idle, runStatus: nil) == nil)
        #expect(MigrateFlow.blocker(pod: idle, runStatus: .done) == nil)
        #expect(MigrateFlow.blocker(pod: idle, runStatus: .phaseA)?.contains("run is busy") == true)
        #expect(MigrateFlow.blocker(pod: live, runStatus: nil)?.contains("pod is live") == true)
        #expect(MigrateFlow.blocker(pod: migrating, runStatus: nil)?.contains("already running") == true)
    }

    @Test func beginPreselectsAndResets() async {
        let flow = make()
        await flow.ask(toDc: "EU-CZ-1")
        flow.begin(preselect: "US-TX-3")
        #expect(flow.step == .choose)
        #expect(flow.destination == "US-TX-3")
        #expect(flow.currentAsk == nil)
    }
}
}
```

- [ ] **Step 2: Run to verify failure**

Run: `swift test --filter MigrateFlowTests`
Expected: compile error — `MigrateFlow` undefined.

- [ ] **Step 3: Implement** — create `MigrateFlow.swift`:

```swift
import Foundation
import Observation

/// The Network Volume migration (Phase 5 design §6): ask → typed confirmation
/// → migrate. `ask` acts on nothing, so it is a plain POST. `migrate` deletes
/// the source volume once the copy verifies, so it goes through `SpendGate`
/// like every spend: one key per tap, journalled before it leaves, resent only
/// with that same key, never re-minted.
@MainActor @Observable
public final class MigrateFlow {
    public enum Step: Equatable, Sendable {
        case choose
        case asking(toDc: String)
        case confirm(MigrateAsk)
        case started(toDc: String)
        case outcomeUnknown
    }

    public private(set) var step: Step = .choose
    /// The destination last asked about, or preselected from a GPU row.
    public private(set) var destination: String?
    public private(set) var message: String?
    public private(set) var inFlightLabel: String?
    public private(set) var retryNote: String?
    /// The migrate got no definitive answer; "Check again" resends its key.
    public private(set) var needsRecheck = false
    /// "Checking the earlier Migrate…" while the launch replay is outstanding.
    public private(set) var pendingNotice: String?
    /// Migrate enables only on an exact, case-sensitive match with `to_dc`.
    public var typed = ""

    /// The server's 10-minute clock started before its answer reached the phone.
    static let expiryMargin: TimeInterval = 15

    private let client: APIClient
    private let gate: any SpendSending
    private let pod: PodStore
    private let now: @Sendable () -> Date
    private var deadline: Date?
    private var didReplay = false

    public init(client: APIClient, gate: any SpendSending, pod: PodStore,
                now: @escaping @Sendable () -> Date = { Date() }) {
        self.client = client
        self.gate = gate
        self.pod = pod
        self.now = now
    }

    public var isSending: Bool { inFlightLabel != nil }

    public var currentAsk: MigrateAsk? {
        if case let .confirm(ask) = step { return ask }
        return nil
    }

    public func secondsLeft(at date: Date) -> Int {
        guard let deadline else { return 0 }
        return max(0, Int(deadline.timeIntervalSince(date).rounded(.down)))
    }

    public func canMigrate(at date: Date) -> Bool {
        guard let ask = currentAsk, let deadline else { return false }
        return typed == ask.toDc && date < deadline
            && !isSending && !needsRecheck && pendingNotice == nil
    }

    /// Why a migration can't start, or nil. The server re-checks every one of
    /// these under `BOT_LOCK` (`_migrate_blocked`); this only explains.
    public static func blocker(pod: PodStatus?, runStatus: RunStatus?) -> String? {
        if pod?.migration?.running == true { return "A volume migration is already running." }
        if pod?.lease != nil { return "A pod is live — kill it before moving the volume." }
        if runStatus?.isLive == true { return "The run is busy — wait for it before moving the volume." }
        return nil
    }

    /// Opening the sheet. An unanswered migrate is never reset: it must be
    /// resolved through Check again.
    public func begin(preselect: String?) {
        guard !isSending, !needsRecheck, pendingNotice == nil else { return }
        step = .choose
        destination = preselect
        typed = ""
        message = nil
        deadline = nil
    }

    public func ask(toDc: String) async {
        guard !isSending else { return }
        if case .asking = step { return }
        destination = toDc
        step = .asking(toDc: toDc)
        message = nil
        typed = ""
        deadline = nil
        do {
            let answer = try await client.post(MigrateAsk.self, body: MigrateAskRequest(toDc: toDc),
                                               timeout: 90, "v1", "pod", "migrate", "ask")
            deadline = now().addingTimeInterval(answer.expiresInSec - Self.expiryMargin)
            step = .confirm(answer)
        } catch {
            message = error.userMessage
            step = .choose
        }
    }

    public func migrate() async {
        guard canMigrate(at: now()), let ask = currentAsk else { return }
        let label = "Migrate volume to \(ask.toDc)"
        inFlightLabel = label
        retryNote = nil
        message = nil
        let result = await gate.perform(.migrate(toDc: ask.toDc, confirmToken: ask.confirmToken),
                                        label: label) { [weak self] attempt, reason in
            Task { @MainActor in self?.noteRetry(attempt, reason) }
        }
        inFlightLabel = nil
        retryNote = nil
        await apply(result, toDc: ask.toDc)
    }

    /// Resends the saved request with its saved key. Never mints a new one.
    public func recheck() async {
        guard needsRecheck, !isSending else { return }
        let saved = await gate.pending()
        var toDc = destination ?? ""
        if case let .migrate(dc, _)? = saved?.intent { toDc = dc }
        inFlightLabel = "Checking the earlier migrate…"
        message = nil
        let result = await gate.recheck { [weak self] attempt, reason in
            Task { @MainActor in self?.noteRetry(attempt, reason) }
        }
        inFlightLabel = nil
        retryNote = nil
        if case let .notSent(reason) = result {
            message = reason
            if await gate.pending() == nil { needsRecheck = false }
            return
        }
        await apply(result, toDc: toDc)
    }

    /// Once per launch, and only for a pending migrate (AppModel routes it).
    public func replayPendingOnce() async {
        guard !didReplay else { return }
        didReplay = true
        guard let entry = await gate.pending(), case let .migrate(toDc, _) = entry.intent else { return }
        pendingNotice = "Checking the earlier \(entry.label)…"
        let result = await gate.replayPending()
        pendingNotice = nil
        guard let result else { return }
        await apply(result, toDc: toDc)
    }

    private func noteRetry(_ attempt: Int, _ reason: SpendRetryReason) {
        guard inFlightLabel != nil else { return }
        retryNote = reason == .botBusy
            ? "The bot is busy — retry \(attempt) of 3"
            : "No answer — retry \(attempt) of 2 with the same request"
    }

    func apply(_ result: SpendResult, toDc: String) async {
        switch result {
        case .accepted:
            needsRecheck = false
            deadline = nil
            step = .started(toDc: toDc)
            await pod.refresh()
        case let .refused(status, code, text, _):
            needsRecheck = false
            if code == "bad_confirm_token" {
                step = .choose
                destination = toDc
                typed = ""
                deadline = nil
                message = "That confirmation expired — ask again."
            } else {
                message = APIError.server(status: status, code: code, message: text).userMessage
            }
        case .outcomeUnknown:
            needsRecheck = false
            step = .outcomeUnknown
            message = "Couldn't tell whether the migration started — check Pod or Telegram before trying again."
            await pod.refresh()
        case let .busy(attempts):
            needsRecheck = false
            message = "The bot stayed busy after \(attempts) attempts. Nothing was recorded — tap again when ready."
        case let .unreachable(detail):
            needsRecheck = true
            message = "No answer from the VPS (\(detail)). The request is saved — Check again resends it without migrating twice."
        case let .expired(label, createdAt):
            needsRecheck = false
            let when = createdAt.map { $0.formatted(date: .omitted, time: .shortened) } ?? "earlier"
            message = "\(label) from \(when) couldn't be verified. Check Pod before trying again."
        case let .notSent(reason):
            message = reason
        }
    }
}
```

- [ ] **Step 4: Run the whole suite**

Run: `swift test`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/MotionKit/Stores/MigrateFlow.swift ios/MotionKit/Tests/MotionKitTests/MigrateFlowTests.swift
git commit -m "feat(ios): volume migration with a typed, expiring confirmation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: App wiring, Pod tab, kill and GPU views

**Files:**
- Create: `ios/MotionApp/Pod/KillViews.swift`, `ios/MotionApp/Pod/PodView.swift`, `ios/MotionApp/Pod/GpuPickerView.swift`
- Modify: `ios/MotionApp/MotionApp.swift`, `ios/MotionApp/RootView.swift`, `ios/MotionApp/RunFlow/RecordingSpendGate.swift`, `ios/MotionApp/Runs/RunDetailView.swift`, `ios/MotionApp/Runs/RunsView.swift`

**Interfaces:**
- Consumes: every store from Tasks 3–6.
- Produces: `AppTab.pod`; `AppModel.gpu`, `.balance`, `.migrate`, `.migrateSheet: MigrateRequest?`, `.recordedSpends`, `static isUITestRecording`; `struct MigrateRequest: Identifiable, Equatable { id, destination: String? }`; `GpuPickerView(store:spending:hasLease:onMigrate:onSelected:)`; `DestructiveButtonStyle`. The Pod tab already sets `model.migrateSheet`; nothing presents it until Task 8 adds `MigrateSheet` to `RootView`, so in between those taps do nothing — the build is complete either way.

No unit tests (SwiftUI views); the gate is `make ios-build`, and `make ios-test` stays green.

- [ ] **Step 1: `RecordingSpendGate` counts what it records**

```swift
actor RecordingSpendGate: SpendSending {
    private(set) var intents: [SpendIntent] = []
    private let onRecord: @Sendable (Int) -> Void

    init(onRecord: @escaping @Sendable (Int) -> Void = { _ in }) {
        self.onRecord = onRecord
    }

    func perform(_ intent: SpendIntent, label: String, onRetry: @escaping SpendRetryHandler) async -> SpendResult {
        intents.append(intent)
        onRecord(intents.count)
        return .notSent(reason: "UI test build — spend recorded, not sent.")
    }
    // recheck / replayPending / pending unchanged
```

- [ ] **Step 2: `AppModel`** — in `MotionApp.swift`:

```swift
enum AppTab: Hashable { case runs, materials, newJob, outputs, pod }

/// Opens the migrate sheet (RootView presents it over every tab).
struct MigrateRequest: Identifiable, Equatable {
    let id = UUID()
    let destination: String?
}
```

Add properties to `AppModel`:

```swift
    private(set) var gpu: GpuStore?
    private(set) var balance: BalanceStore?
    private(set) var migrate: MigrateFlow?
    var migrateSheet: MigrateRequest?
    /// UI-test builds only: how many spend taps the recording gate swallowed.
    private(set) var recordedSpends = 0
    private var spendGate: (any SpendSending)?
    static let isUITestRecording = ProcessInfo.processInfo.arguments.contains("-UITestRecordingSpendGate")
```

Replace `reconnect()` and `replayPendingSpend()`:

```swift
    /// Refuses to rebuild while a spend or migrate is outstanding — replacing
    /// the store would strand its result on an unobserved instance.
    @discardableResult
    func reconnect() -> Bool {
        if runFlow?.isSpending == true || runFlow?.pendingNotice != nil { return false }
        if migrate?.isSending == true || migrate?.pendingNotice != nil { return false }
        materialResumeTask?.cancel()
        materialResumeTask = nil
        guard let credentials = vault.load() else {
            client = nil; runs = nil; pod = nil; materials = nil; draft = nil; outputs = nil; runFlow = nil
            gpu = nil; balance = nil; migrate = nil; spendGate = nil
            return true
        }
        let client = APIClient(credentials: credentials)
        self.client = client
        runs = RunsStore(client: client)
        let pod = PodStore(client: client)
        self.pod = pod
        materials = MaterialsStore(client: client)
        draft = DraftStore(client: client)
        outputs = OutputsStore(client: client)
        gpu = GpuStore(client: client, pod: pod)
        balance = BalanceStore(client: client)
        let gate: any SpendSending = Self.isUITestRecording
            ? RecordingSpendGate { [weak self] count in
                Task { @MainActor in self?.recordedSpends = count }
            }
            : SpendGate(client: client)
        spendGate = gate
        runFlow = RunFlow(client: client, gate: gate)
        migrate = MigrateFlow(client: client, gate: gate, pod: pod)
        replayTask = nil
        replayPendingSpend()
        resumeMaterialsUpload()
        return true
    }

    /// Once per launch (each flow guards it): resend an interrupted spend with
    /// its original key. The one ledger entry belongs to whichever flow sent it.
    func replayPendingSpend() {
        guard replayTask == nil, let runFlow, let migrate, let spendGate else { return }
        replayTask = Task {
            if await spendGate.pending()?.intent.kind == .migrate {
                await migrate.replayPendingOnce()
            } else {
                await runFlow.replayPendingOnce()
            }
        }
    }
```

- [ ] **Step 3: Kill views** — create `ios/MotionApp/Pod/KillViews.swift`:

```swift
import SwiftUI
import MotionKit

struct DestructiveButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.red)
            .frame(maxWidth: .infinity).padding(.vertical, 13)
            .background(Theme.redDim.opacity(configuration.isPressed ? 0.6 : 1), in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.redLine))
    }
}

/// Red, behind a confirmation dialog. Never disabled by a pending spend —
/// only by a kill already sending or polling (Phase 5 design §4).
struct KillButton: View {
    let pod: PodStore
    let runID: String
    let hasLease: Bool
    @State private var confirming = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { confirming = true } label: {
                HStack(spacing: 8) {
                    if pod.isKilling { ProgressView().controlSize(.small).tint(Theme.red) }
                    Text(label)
                }
            }
            .buttonStyle(DestructiveButtonStyle())
            .disabled(pod.isKilling)
            .accessibilityIdentifier("pod.kill")
            .confirmationDialog(hasLease ? "Destroy the pod now?" : "Stop the try-on phase?",
                                isPresented: $confirming, titleVisibility: .visible) {
                Button(hasLease ? "Destroy the pod" : "Stop the try-on phase", role: .destructive) {
                    Task { await pod.kill(runID: runID) }
                }
            } message: {
                Text(hasLease ? "Jobs in progress are lost; finished outputs stay."
                              : "Nothing was rented; Gemini calls already made are not refunded.")
            }
            KillNotice(pod: pod)
        }
    }

    private var label: String {
        switch pod.killState {
        case .idle: hasLease ? "Kill · destroy the pod" : "Kill · stop the try-on phase"
        case .sending: "Sending kill…"
        case .killing: "Killing… (up to ~3 min)"
        }
    }
}

/// The last kill's outcome, also shown after Kill itself disappears.
struct KillNotice: View {
    let pod: PodStore
    var body: some View {
        if let notice = pod.killNotice {
            Text(notice.text).font(Theme.sans(13))
                .foregroundStyle(notice.isError ? Theme.red : Theme.ink2)
        }
        if pod.killStillRunning {
            Button("Check again") { Task { await pod.checkKillAgain() } }
                .buttonStyle(SecondaryButtonStyle())
        }
    }
}

/// On every tab until acknowledged or overwritten by a later successful kill.
struct KillBanner: View {
    let pod: PodStore
    @State private var confirming = false

    var body: some View {
        if let kill = pod.unverifiedKill {
            VStack(alignment: .leading, spacing: 8) {
                Label("Pod may still be billing — check RunPod", systemImage: "exclamationmark.octagon.fill")
                    .font(Theme.sans(14, .semibold)).foregroundStyle(Theme.red)
                Text(kill.message).font(Theme.sans(12)).foregroundStyle(Theme.ink1)
                Button("I checked — the pod is gone") { confirming = true }
                    .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
            }
            .padding(12).frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.redDim, in: .rect(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.redLine))
            .padding(.horizontal, 16)
            .accessibilityIdentifier("pod.unverifiedKill")
            .confirmationDialog("Only confirm after checking the RunPod console (or runpodctl get pod).",
                                isPresented: $confirming, titleVisibility: .visible) {
                Button("The pod is gone — hide this", role: .destructive) { pod.acknowledgeUnverifiedKill() }
            }
        }
    }
}
```

- [ ] **Step 4: GPU picker** — create `ios/MotionApp/Pod/GpuPickerView.swift`:

```swift
import SwiftUI
import MotionKit

/// The five GPUs from `GET /v1/gpu/stock`. Selecting is free (no dialog);
/// the rent panel passes `onSelected` to re-read its quote.
struct GpuPickerView: View {
    let store: GpuStore
    let spending: Bool
    let hasLease: Bool
    var onMigrate: ((String) -> Void)? = nil
    var onSelected: (() async -> Void)? = nil
    @State private var expanded: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let error = store.error {
                if store.stock == nil {
                    ErrorBanner(error: error) { await store.load() }
                } else {
                    HStack {
                        Text("Couldn't reach runpodctl — showing the last list.")
                            .font(Theme.sans(12)).foregroundStyle(Theme.amber)
                        Spacer()
                        Button("Retry") { Task { await store.load(force: true) } }
                            .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                    }
                }
            }
            if let stock = store.stock {
                ForEach(stock.gpus) { row in rowView(row, stock: stock) }
            } else if store.isLoading {
                ProgressView("Reading stock…").frame(maxWidth: .infinity).padding(.vertical, 20)
            }
            if hasLease {
                Text("A change applies to the next rental.").font(Theme.sans(12)).foregroundStyle(Theme.ink3)
            }
            if spending {
                Text("A spend request is in flight — the GPU can't change until it's answered.")
                    .font(Theme.sans(12)).foregroundStyle(Theme.ink3)
            }
            if let message = store.message {
                Text(message).font(Theme.sans(12)).foregroundStyle(Theme.amber)
            }
        }
        .opacity(store.isStale ? 0.6 : 1)
    }

    private func rowView(_ row: GpuStockRow, stock: GpuStock) -> some View {
        let selected = row.gpu == stock.selected
        let regions = stock.otherRegions.filter { $0.gpu == row.gpu }
        return VStack(alignment: .leading, spacing: 8) {
            Button {
                Task {
                    if await store.select(row.gpu, whileSpending: spending) { await onSelected?() }
                }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                        .foregroundStyle(selected ? Theme.lime : Theme.ink3)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(row.name).font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink)
                        Text(row.summary).font(Theme.mono(11))
                            .foregroundStyle(row.soldOutEverywhere ? Theme.amber : Theme.ink2)
                    }
                    Spacer()
                    if store.selecting == row.gpu { ProgressView().controlSize(.small) }
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .disabled(selected || store.selecting != nil || spending)
            .accessibilityIdentifier("gpu.row.\(row.gpu)")
            if !regions.isEmpty {
                Button(expanded == row.gpu ? "Hide other regions" : "Other regions (\(regions.count))") {
                    expanded = expanded == row.gpu ? nil : row.gpu
                }
                .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                if expanded == row.gpu {
                    ForEach(regions, id: \.datacenter) { region in
                        HStack {
                            Text("\(region.datacenter) · stock \(region.stock)"
                                 + (region.usdPerHr.map { " · \(Format.usd($0))/h" } ?? ""))
                                .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                            Spacer()
                            if let onMigrate {
                                Button("Migrate to \(region.datacenter) →") { onMigrate(region.datacenter) }
                                    .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                            }
                        }
                    }
                }
            }
        }
        .padding(14)
        .card(border: selected ? Theme.limeLine : Theme.line)
    }
}
```

- [ ] **Step 5: Pod tab** — create `ios/MotionApp/Pod/PodView.swift`:

```swift
import SwiftUI
import MotionKit

struct PodView: View {
    let pod: PodStore
    let gpu: GpuStore
    let balance: BalanceStore
    let flow: RunFlow
    let runs: RunsStore
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Pod").font(Theme.sans(33, .bold)).foregroundStyle(Theme.ink)
                if let error = pod.error, pod.pod == nil {
                    ErrorBanner(error: error) { await pod.refresh() }
                }
                leaseSection
                if let migration = pod.pod?.migration, migration.running {
                    MigrationCard(migration: migration)
                }
                SectionLabel(text: "Balance")
                BalanceCard(store: balance)
                SectionLabel(text: "GPU")
                GpuPickerView(store: gpu, spending: flow.isSpending, hasLease: pod.pod?.lease != nil,
                              onMigrate: { model.migrateSheet = MigrateRequest(destination: $0) })
                Button("Move volume…") { model.migrateSheet = MigrateRequest(destination: nil) }
                    .buttonStyle(SecondaryButtonStyle())
                    .accessibilityIdentifier("pod.moveVolume")
            }
            .padding(.horizontal, 20).padding(.bottom, 30)
        }
        .background(Theme.bg)
        .refreshable {
            async let a: Void = pod.refresh()
            async let b: Void = balance.load()
            async let c: Void = gpu.load(force: true)
            _ = await (a, b, c)
        }
        .task {
            async let a: Void = pod.refresh()
            async let b: Void = balance.load()
            if gpu.stock == nil { await gpu.load() }
            _ = await (a, b)
        }
        // Scoped to a visible Pod tab in an active scene; cancelled otherwise.
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await pod.pollMigration()
        }
    }

    @ViewBuilder private var leaseSection: some View {
        if let status = pod.pod {
            if let lease = status.lease {
                LeaseCard(gpu: status.gpu, lease: lease)
            } else {
                Text("No pod running").font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink2)
                    .padding(16).frame(maxWidth: .infinity, alignment: .leading).card()
                    .accessibilityIdentifier("pod.none")
            }
            if pod.showsKill(runStatus: runs.live?.status), let runID = status.runId {
                KillButton(pod: pod, runID: runID, hasLease: status.lease != nil)
            } else {
                KillNotice(pod: pod)
            }
        } else if pod.error == nil {
            ProgressView().frame(maxWidth: .infinity).padding(.vertical, 20)
        }
    }
}

struct LeaseCard: View {
    let gpu: String
    let lease: PodLease

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { ctx in
            let elapsed = ctx.date.timeIntervalSince1970 - lease.provisionedAt
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 8) {
                    PulseDot()
                    // The .env GPU names a RunPod card; a Vast lease has its own.
                    Text(lease.provider == "runpod" ? "RunPod · \(gpu)" : lease.provider)
                        .font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink)
                }
                Text(Format.clock(elapsed)).font(Theme.mono(36, .semibold)).foregroundStyle(Theme.ink)
                if let cost = CostEstimate.usd(elapsed: elapsed, ratePerHour: lease.quotedUsdPerHr) {
                    Text("≈ \(Format.usd(cost)) — a quote, not the invoice")
                        .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                }
            }
        }
        .padding(18).frame(maxWidth: .infinity, alignment: .leading)
        .card(radius: 20, border: Theme.limeLine)
    }
}

struct MigrationCard: View {
    let migration: PodMigration

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                PulseDot(color: Theme.amber)
                Text("Moving the volume" + (migration.toDc.map { " to \($0)" } ?? ""))
                    .font(Theme.sans(15, .semibold)).foregroundStyle(Theme.ink)
            }
            if let phase = migration.phase {
                Text(phase).font(Theme.mono(11)).foregroundStyle(Theme.ink2)
            }
            if let started = migration.startedAt {
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    Text(Format.clock(ctx.date.timeIntervalSince1970 - started))
                        .font(Theme.mono(13)).foregroundStyle(Theme.ink1)
                }
            }
            if let fraction = migration.fractionCopied {
                ProgressView(value: fraction).tint(Theme.amber)
            }
            Text("Progress is also posted in Telegram. A migration can't be cancelled.")
                .font(Theme.sans(12)).foregroundStyle(Theme.ink3)
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .card(border: Theme.amber.opacity(0.4))
    }
}

struct BalanceCard: View {
    let store: BalanceStore

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let line = store.runpodLine {
                let low = store.balance?.runpod?.lowRunway == true
                Text("RunPod").font(Theme.mono(11)).foregroundStyle(Theme.ink3)
                Text(line).font(Theme.mono(15, .semibold)).foregroundStyle(low ? Theme.amber : Theme.ink)
                if low {
                    Text("Under 1 h of runway — top up before renting.")
                        .font(Theme.sans(12)).foregroundStyle(Theme.amber)
                }
            } else if let error = store.error {
                ErrorBanner(error: error) { await store.load() }
            } else {
                ProgressView().frame(maxWidth: .infinity)
            }
            if let vast = store.vastLine {
                Text("Vast").font(Theme.mono(11)).foregroundStyle(Theme.ink3)
                Text(vast).font(Theme.mono(15, .semibold)).foregroundStyle(Theme.ink)
            }
            ForEach(store.balance?.errors ?? [], id: \.self) { text in
                Text(text).font(Theme.sans(12)).foregroundStyle(Theme.amber)
            }
            Button { Task { await store.loadVast() } } label: {
                HStack(spacing: 8) {
                    if store.isLoadingVast { ProgressView().controlSize(.small) }
                    Text(store.isLoadingVast ? "Reading the Vast credit (~30 s)…" : "Check Vast credit")
                }
            }
            .buttonStyle(SecondaryButtonStyle())
            .disabled(store.isLoadingVast)
            .accessibilityIdentifier("pod.checkVast")
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading).card()
    }
}
```

- [ ] **Step 6: Root and run detail**

`RootView.swift` — extend the `if let` with `let gpu = model.gpu, let balance = model.balance, let migrate = model.migrate`; add the fifth tab after Output:

```swift
                    Tab("Pod", systemImage: "cpu", value: AppTab.pod) {
                        NavigationStack { PodView(pod: pod, gpu: gpu, balance: balance, flow: flow, runs: runs) }
                    }
```

Replace `.safeAreaInset(edge: .top) { SpendBanner(flow: flow) }` with:

```swift
                .safeAreaInset(edge: .top) {
                    VStack(spacing: 8) {
                        KillBanner(pod: pod)
                        SpendBanner(flow: flow, migrate: migrate)
                    }
                }
```

In the `podRequested` handler change `model.selectedTab = .runs` to `model.selectedTab = .pod` (ruling above). After it add:

```swift
                .overlay(alignment: .bottomLeading) {
                    if AppModel.isUITestRecording {
                        Color.clear.frame(width: 1, height: 1)
                            .accessibilityElement()
                            .accessibilityLabel("\(model.recordedSpends)")
                            .accessibilityIdentifier("uitest.recordedSpends")
                    }
                }
```

In `.onChange(of: scenePhase)`'s `.active` branch add `if let pod = model.pod { Task { await pod.refresh() } }` (the banner must appear after an app kill).

Replace `SpendBanner`:

```swift
/// Visible on every tab while a spend or migrate is outstanding or re-checked.
struct SpendBanner: View {
    let flow: RunFlow
    let migrate: MigrateFlow
    var body: some View {
        if let text = flow.pendingNotice ?? migrate.pendingNotice
            ?? (flow.inFlightLabel ?? migrate.inFlightLabel).map({ "Sending: \($0)" }) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small).tint(Theme.lime)
                VStack(alignment: .leading, spacing: 2) {
                    Text(text).font(Theme.sans(13, .semibold)).foregroundStyle(Theme.ink1)
                    if let note = flow.retryNote ?? migrate.retryNote {
                        Text(note).font(Theme.mono(11)).foregroundStyle(Theme.amber)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .card(border: Theme.limeLine)
            .padding(.horizontal, 16)
        }
    }
}
```

`RunDetailView.swift` — add `let pod: PodStore` after `let flow: RunFlow`. After the Retry-rental card block (inside `if let d = store.detail`), add:

```swift
                    if d.id == pod.pod?.runId, pod.showsKill(runStatus: d.status), let runID = pod.pod?.runId {
                        KillButton(pod: pod, runID: runID, hasLease: pod.pod?.lease != nil)
                    }
```

and change `.task { await flow.refreshPod() }` to:

```swift
        .task {
            async let a: Void = flow.refreshPod()
            async let b: Void = pod.refresh()
            _ = await (a, b)
        }
```

`RunsView.swift` — the destination becomes `RunDetailView(store: RunDetailStore(client: client, runID: id), flow: flow, pod: pod)`.

- [ ] **Step 7: Build and test**

Run: `make ios-build` (from the repo root) — Expected: `** BUILD SUCCEEDED **` (quiet mode prints nothing on success; exit 0).
Run: `make ios-test` — Expected: all pass.

- [ ] **Step 8: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/Pod/KillViews.swift ios/MotionApp/Pod/PodView.swift ios/MotionApp/Pod/GpuPickerView.swift \
  ios/MotionApp/MotionApp.swift ios/MotionApp/RootView.swift ios/MotionApp/RunFlow/RecordingSpendGate.swift \
  ios/MotionApp/Runs/RunDetailView.swift ios/MotionApp/Runs/RunsView.swift
git commit -m "feat(ios): Pod tab with kill, balances and GPU choice

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Migrate sheet and the rent panel's Change GPU / Migrate

**Files:**
- Create: `ios/MotionApp/Pod/MigrateSheet.swift`
- Modify: `ios/MotionApp/RootView.swift`, `ios/MotionApp/RunFlow/RentPanelView.swift`

**Interfaces:**
- Consumes: `MigrateFlow` (Task 6), `GpuStore`, `PodStore`, `RunFlow.reloadPanelAfterGpuChange()` (Task 5), `GpuPickerView`, `DestructiveButtonStyle`, `MigrateRequest`, `AppModel.migrateSheet`/`.gpu` (Task 7).
- Produces: `MigrateSheet(request:flow:gpu:pod:runStatus:)`; accessibility ids `migrate.dest.<dc>`, `migrate.blocked`, `migrate.warning`, `migrate.typed`, `migrate.confirm`, `runflow.changeGpu`, `runflow.migrate`.

- [ ] **Step 1: Create `MigrateSheet.swift`**

```swift
import SwiftUI
import MotionKit

/// ask → typed confirmation → migrate (Phase 5 design §6). The confirm button
/// enables only on an exact match with `to_dc`, before the token expires.
struct MigrateSheet: View {
    let request: MigrateRequest
    let flow: MigrateFlow
    let gpu: GpuStore
    let pod: PodStore
    let runStatus: RunStatus?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        @Bindable var flow = flow
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    content(typed: $flow.typed)
                    if flow.needsRecheck {
                        Button("Check again") { Task { await flow.recheck() } }
                            .buttonStyle(SecondaryButtonStyle())
                            .disabled(flow.isSending)
                    }
                    if flow.isSending {
                        HStack(spacing: 8) {
                            ProgressView()
                            VStack(alignment: .leading, spacing: 2) {
                                Text(flow.inFlightLabel ?? "").font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                                if let note = flow.retryNote {
                                    Text(note).font(Theme.mono(11)).foregroundStyle(Theme.amber)
                                }
                            }
                        }
                    }
                    if let message = flow.message {
                        Text(message).font(Theme.sans(13)).foregroundStyle(Theme.amber)
                    }
                }
                .padding(20)
            }
            .background(Theme.bg)
            .navigationTitle("Move volume")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }.disabled(flow.isSending)
                }
            }
            .task {
                flow.begin(preselect: request.destination)
                if gpu.stock == nil { await gpu.load() }
            }
        }
        .interactiveDismissDisabled(flow.isSending)
    }

    @ViewBuilder private func content(typed: Binding<String>) -> some View {
        switch flow.step {
        case .choose:
            chooser
        case .asking(let dc):
            ProgressView("Checking \(dc)…").frame(maxWidth: .infinity).padding(.top, 30)
        case .confirm(let ask):
            confirmation(ask, typed: typed)
        case .started(let dc):
            Label("Migration to \(dc) started", systemImage: "checkmark.circle")
                .font(Theme.sans(15, .semibold)).foregroundStyle(Theme.lime)
            Text("Progress shows on the Pod tab and in Telegram.")
                .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
            Button("Done") { dismiss() }.buttonStyle(PrimaryButtonStyle())
        case .outcomeUnknown:
            Button("Done") { dismiss() }.buttonStyle(SecondaryButtonStyle())
        }
    }

    @ViewBuilder private var chooser: some View {
        let blocker = MigrateFlow.blocker(pod: pod.pod, runStatus: runStatus)
        Text("Copies the Network Volume (models, Postgres, MinIO) to another datacenter, then deletes the current one once the copy verifies. About 25–30 minutes, on two temporary CPU pods.")
            .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
        if let blocker {
            Text(blocker).font(Theme.sans(13, .semibold)).foregroundStyle(Theme.red)
                .accessibilityIdentifier("migrate.blocked")
        }
        if let stock = gpu.stock {
            let destinations = stock.destinations
            if destinations.isEmpty {
                Text("No other datacenter has a GPU in stock right now.")
                    .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
            }
            ForEach(destinations) { destination in
                Button { Task { await flow.ask(toDc: destination.datacenter) } } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(destination.datacenter).font(Theme.mono(15, .semibold)).foregroundStyle(Theme.ink)
                        Text(destination.gpus.joined(separator: " · "))
                            .font(Theme.mono(11)).foregroundStyle(Theme.ink2)
                    }
                    .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                    .card(border: destination.datacenter == flow.destination ? Theme.limeLine : Theme.line)
                }
                .buttonStyle(.plain)
                .disabled(blocker != nil || flow.isSending || flow.needsRecheck || flow.pendingNotice != nil)
                .accessibilityIdentifier("migrate.dest.\(destination.datacenter)")
            }
        } else if let error = gpu.error {
            ErrorBanner(error: error) { await gpu.load() }
        } else {
            ProgressView("Reading stock…").frame(maxWidth: .infinity)
        }
    }

    private func confirmation(_ ask: MigrateAsk, typed: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("\(ask.homeDatacenter) → \(ask.toDc)").font(Theme.mono(18, .semibold)).foregroundStyle(Theme.ink)
            Text(ask.warning).font(Theme.sans(14)).foregroundStyle(Theme.red)
                .accessibilityIdentifier("migrate.warning")
            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                let left = flow.secondsLeft(at: ctx.date)
                VStack(alignment: .leading, spacing: 12) {
                    Text(left > 0 ? "Confirmation valid for \(Format.clock(Double(left)))"
                                  : "This confirmation expired.")
                        .font(Theme.mono(11)).foregroundStyle(left > 0 ? Theme.ink2 : Theme.amber)
                    TextField("Type \(ask.toDc) to confirm", text: typed)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(Theme.mono(15)).padding(12).card()
                        .accessibilityIdentifier("migrate.typed")
                    Button("Migrate and delete the old volume") { Task { await flow.migrate() } }
                        .buttonStyle(DestructiveButtonStyle())
                        .disabled(!flow.canMigrate(at: ctx.date))
                        .accessibilityIdentifier("migrate.confirm")
                    if left == 0 {
                        Button("Expired — ask again") { Task { await flow.ask(toDc: ask.toDc) } }
                            .buttonStyle(SecondaryButtonStyle())
                    }
                }
            }
        }
    }
}
```

- [ ] **Step 2: Present it from `RootView`** — after the `.onChange(of: flow.podRequested)` modifier:

```swift
                .sheet(item: $model.migrateSheet) { request in
                    MigrateSheet(request: request, flow: migrate, gpu: gpu, pod: pod,
                                 runStatus: runs.live?.status)
                }
```

- [ ] **Step 3: Rent panel** — in `RentPanelView`:

Add properties:

```swift
    @Environment(AppModel.self) private var model
    @State private var changingGpu = false
```

After `vastRow(panel.vast)` insert:

```swift
                HStack(spacing: 10) {
                    Button("Change GPU") { changingGpu = true }
                        .buttonStyle(SecondaryButtonStyle())
                        .disabled(flow.isSpending)
                        .accessibilityIdentifier("runflow.changeGpu")
                    if panel.runpod.soldOut {
                        Button("Migrate →") {
                            model.selectedTab = .pod
                            model.migrateSheet = MigrateRequest(destination: nil)
                        }
                        .buttonStyle(SecondaryButtonStyle())
                        .accessibilityIdentifier("runflow.migrate")
                    }
                }
```

Attach to the outer `VStack`:

```swift
        .sheet(isPresented: $changingGpu) {
            if let gpu = model.gpu {
                NavigationStack {
                    ScrollView {
                        GpuPickerView(store: gpu, spending: flow.isSpending, hasLease: false,
                                      onSelected: {
                                          changingGpu = false
                                          await flow.reloadPanelAfterGpuChange()
                                      })
                        .padding(20)
                    }
                    .background(Theme.bg)
                    .navigationTitle("Change GPU")
                    .navigationBarTitleDisplayMode(.inline)
                    .task { if gpu.stock == nil { await gpu.load() } }
                }
            }
        }
```

- [ ] **Step 4: Build and test**

Run: `make ios-build` — Expected: exit 0.
Run: `make ios-test` — Expected: all pass.

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionApp/Pod/MigrateSheet.swift ios/MotionApp/RootView.swift ios/MotionApp/RunFlow/RentPanelView.swift
git commit -m "feat(ios): migrate sheet; change GPU and migrate from the rent panel

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Contract and refusal smoke

**Files:**
- Modify: `ios/MotionKit/Sources/motion-contract/main.swift`
- Modify: `Makefile` (the `ios-refusal-smoke` help text only)

**Interfaces:**
- Consumes: `GpuStock`, `Balance`, `PodStatus.migration` (Task 1), `SpendIntent.migrate` (Task 5), `TryonPreviews`.

- [ ] **Step 1: Contract reads** — after the `GET /v1/pod` check in `main.swift`, add:

```swift
// Cached stock (no ?force=1); the migration block is decoded inside PodStatus.
await check("GET /v1/gpu/stock") {
    _ = try await client.get(GpuStock.self, timeout: 60, "v1", "gpu", "stock")
}
// No ?vast=1: the Vast credit is a ~30 s subprocess and is opt-in.
await check("GET /v1/balance") {
    _ = try await client.get(Balance.self, timeout: 45, "v1", "balance")
}
```

- [ ] **Step 2: Refusal smoke** — update the file's header comment to "…unless --refusal-smoke is passed (see below)." (unchanged) and the block comment above `if CommandLine.arguments.contains("--refusal-smoke")` to:

```swift
// --refusal-smoke: spend and destroy calls that must each be refused before
// anything acts: confirm/regen/resume with bogus tokens (bot.py
// AppRuns.confirm / _regen_tryon / AppPod.resume), migrate with a bogus
// confirm_token (AppPod.migrate: _migrate_blocked passes with no lease, then
// _migrate_token_stale refuses), and kill with nothing running
// (AppPod.kill → nothing_running). Kill is sent only when Phase A is not
// running — a live Phase A would really be stopped. phase-a is never sent: it
// has no token to refuse on.
```

After the `for (name, intent, expected) in cases { … }` loop and before `if await noLease("after") == nil`, insert:

```swift
    func describe(_ raw: RawSpendResponse) -> String {
        switch raw {
        case .http(let status, _): "HTTP \(status)"
        case .transport(let detail): "transport: \(detail)"
        }
    }
    let tryon = try? await client.get(TryonPreviews.self, "v1", "runs", runID, "tryon")
    if let tryon, !tryon.phaseARunning, !pod.killRunning {
        let raw = await client.spendPost(["v1", "runs", runID, "kill"], body: Data("{}".utf8),
                                         idempotencyKey: UUID().uuidString)
        if case let .http(409, body) = raw,
           String(decoding: body, as: UTF8.self).contains("\"nothing_running\"") {
            print("ok   idle kill → 409 nothing_running")
        } else {
            print("FAIL idle kill: \(describe(raw))")
            smokeFailed += 1
        }
    } else {
        print("skip idle kill (Phase A or a kill is running, or the try-on read failed)")
    }
    if pod.migration?.running == true {
        print("skip migrate (a migration is running)")
    } else {
        let result = await gate.perform(.migrate(toDc: "refusal-smoke", confirmToken: bogus),
                                        label: "refusal smoke: migrate") { _, _ in }
        if case .refused(409, "bad_confirm_token", _, _) = result {
            print("ok   migrate bogus confirm_token → 409 bad_confirm_token")
        } else {
            print("FAIL migrate bogus confirm_token: \(result)")
            smokeFailed += 1
        }
    }
```

(`result` for a `.refused` carries the server's message text, which is plain and never a body dump — acceptable to print, as the existing cases already do.)

- [ ] **Step 3: Makefile help text**

```make
ios-refusal-smoke: ## Live zero-spend check: bogus-token confirm/regen/resume/migrate and an idle kill must be refused (no pod)
```

- [ ] **Step 4: Build**

Run: `cd ios/MotionKit && swift build --product motion-contract`
Expected: `Build complete!`. Do **not** run `make ios-contract` or `make ios-refusal-smoke` here — the controller runs them (the smoke needs the user's approval).

- [ ] **Step 5: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionKit/Sources/motion-contract/main.swift Makefile
git commit -m "feat(ios): contract reads for stock and balance; refused kill and migrate in the smoke

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Zero-spend Pod UI smoke

**Files:**
- Create: `ios/MotionAppUITests/Phase5SmokeTests.swift`

**Interfaces:**
- Consumes: accessibility ids from Tasks 7–8 (`pod.checkVast`, `gpu.row.*`, `pod.none`, `pod.moveVolume`, `migrate.dest.*`, `migrate.blocked`, `migrate.confirm`, `migrate.warning`, `uitest.recordedSpends`) and `Phase4Draft.revealButton` / `Phase4Draft.waitUntil` (in `Phase4SmokeTests.swift`).

- [ ] **Step 1: Write the test**

```swift
import XCTest

/// Zero-spend: launched with -UITestRecordingSpendGate. Reads the Pod tab,
/// then opens the migrate flow as far as the typed confirmation — `ask` acts
/// on nothing and its token lapses in 10 minutes. Never taps a GPU row (that
/// rewrites the live .env), Kill, or Migrate.
final class Phase5SmokeTests: XCTestCase {
    @MainActor
    func testPodTabRendersAndMigrateStaysLocked() throws {
        let app = XCUIApplication()
        app.launchArguments.append("-UITestRecordingSpendGate")
        app.launch()
        app.tabBars.buttons["Pod"].tap()

        XCTAssertTrue(app.buttons["pod.checkVast"].waitForExistence(timeout: 60), "balance card renders")
        let rows = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "gpu.row."))
        XCTAssertTrue(Phase4Draft.waitUntil(timeout: 90) { rows.count == 5 }, "five GPU rows")

        guard app.descendants(matching: .any)["pod.none"].waitForExistence(timeout: 10) else {
            throw XCTSkip("A pod is live — the migrate half of this smoke runs only with nothing rented.")
        }
        Phase4Draft.revealButton("pod.moveVolume", in: app).tap()

        if app.descendants(matching: .any)["migrate.blocked"].waitForExistence(timeout: 5) {
            throw XCTSkip("The run is busy — migrate is blocked on the phone, as designed.")
        }
        let destination = app.buttons.matching(
            NSPredicate(format: "identifier BEGINSWITH %@", "migrate.dest.")).firstMatch
        guard destination.waitForExistence(timeout: 60) else {
            throw XCTSkip("No other datacenter has stock right now.")
        }
        destination.tap()

        let confirm = app.buttons["migrate.confirm"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 120), "ask returned a confirmation")
        XCTAssertFalse(confirm.isEnabled, "Migrate stays locked until the destination is typed")
        XCTAssertTrue(app.descendants(matching: .any)["migrate.warning"].exists)

        app.buttons["Close"].tap()
        XCTAssertEqual(app.descendants(matching: .any)["uitest.recordedSpends"].label, "0",
                       "the recording gate saw no spend")
    }
}
```

- [ ] **Step 2: Build the test target**

Run: `make ios-gen && xcodebuild -project ios/MotionApp.xcodeproj -scheme MotionApp -destination 'generic/platform=iOS Simulator' -quiet build-for-testing`
Expected: exit 0. Do **not** run `make ios-ui-test` here — the controller runs it outside the sandbox.

- [ ] **Step 3: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add ios/MotionAppUITests/Phase5SmokeTests.swift
git commit -m "test(ios): zero-spend Pod tab and migrate-lock smoke

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Live gates and the handoff docs (controller)

**Files:**
- Modify: `docs/superpowers/swiftui-app-progress.md`, `docs/superpowers/specs/2026-09-23-swiftui-app-phase-5-design.md` (status line), `docs/superpowers/specs/2026-09-22-swiftui-app-design.md` (§5 row 5), `ios/README.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Run the gates and record the exact results**

```bash
make ios-test
make ios-build
make ios-contract          # live GETs, free
make ios-ui-test           # outside the command sandbox
motions-studio/setup/scrub-secrets.sh --check
```

Ask the user before `make ios-refusal-smoke`; run it only after a yes. Record every result verbatim (test counts, which UI tests passed or skipped and why).

- [ ] **Step 2: Update the docs**

- Phase 5 spec status: `Date: 2026-09-23 · Status: implemented; <refusal smoke result>; a real kill, GPU change and migration not yet exercised`.
- Parent design §5 row 5: "Pod & cost — kill, GPU choice, balance, migrate — implemented per [Phase 5 design](2026-09-23-swiftui-app-phase-5-design.md)".
- Progress handoff: baseline SHA (the Phase 5 merge), the phase table row 5 = Complete in code with its evidence, the new file map entries (`Pod/`, `GpuStore`, `BalanceStore`, `MigrateFlow`), the new test count, the gate list with Phase 5 additions, "Known incomplete work" (no real kill, GPU change or migration), and "Next work": the one real spend test (Phase A → confirm → **Kill from the app** → `runpodctl billing` for the cost), then Phase 6.
- `ios/README.md`: a Phase 5 section (Pod tab, the unverified-kill banner and its acknowledgement, what the smoke taps and never taps).
- `CLAUDE.md` and `AGENTS.md` (keep identical): the `ios-refusal-smoke` line becomes `# live, zero-spend: bogus-token confirm/regen/resume/migrate and an idle kill must 409 (asks first)`.

- [ ] **Step 3: Commit**

```bash
motions-studio/setup/scrub-secrets.sh --check
git add docs/superpowers/swiftui-app-progress.md docs/superpowers/specs/2026-09-23-swiftui-app-phase-5-design.md \
  docs/superpowers/specs/2026-09-22-swiftui-app-design.md ios/README.md CLAUDE.md AGENTS.md
git commit -m "docs(ios): record phase 5 and its gates

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
