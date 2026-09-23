import Foundation
import MotionKit

// Decodes the live API's read routes with the app's own models. GET only —
// spends nothing — unless --refusal-smoke is passed (see below). Prints route names only, never bodies.

func readEnv(_ path: String) -> [String: String] {
    guard let text = try? String(contentsOfFile: path, encoding: .utf8) else { return [:] }
    var out: [String: String] = [:]
    for line in text.split(separator: "\n") where !line.hasPrefix("#") {
        guard let eq = line.firstIndex(of: "=") else { continue }
        out[String(line[..<eq])] = String(line[line.index(after: eq)...])
    }
    return out
}

let envPath = CommandLine.arguments.dropFirst().first ?? ".env"
let env = readEnv(envPath)
guard let urlString = env["CONTROL_API_URL"], let url = URL(string: urlString),
      let id = env["CF_ACCESS_CLIENT_ID"], let secret = env["CF_ACCESS_CLIENT_SECRET"],
      let token = env["CONTROL_API_TOKEN"] else {
    FileHandle.standardError.write(Data("missing CONTROL_API_URL / CF_ACCESS_* / CONTROL_API_TOKEN in \(envPath)\n".utf8))
    exit(2)
}
let client = APIClient(credentials: Credentials(baseURL: url, accessClientID: id,
                                                accessClientSecret: secret, bearerToken: token))
var failed = 0

// @MainActor: `failed` is a top-level (main-actor) variable in Swift 6.
@MainActor func check(_ name: String, _ body: () async throws -> Void) async {
    do {
        try await body()
        print("ok   \(name)")
    } catch let error as APIError {
        print("FAIL \(name): \(error)")
        failed += 1
    } catch {
        print("FAIL \(name): \(error)")
        failed += 1
    }
}

// --refusal-smoke: spend and destroy calls that must each be refused before
// anything acts: confirm/regen/resume with bogus tokens (bot.py
// AppRuns.confirm / _regen_tryon / AppPod.resume), migrate with a bogus
// confirm_token (AppPod.migrate: _migrate_blocked passes with no lease, then
// _migrate_token_stale refuses), and kill with nothing running
// (AppPod.kill → nothing_running). Kill is sent only when a fresh read taken
// right before it shows nothing live: a live Phase A, a running kill, a live
// run or any lease would really be stopped and destroyed. The "before" lease
// check is not enough on its own: drain.py writes the lease only after
// provisioning, so a drain that is still renting has no lease yet while the
// server's `_something_live()` is already true. phase-a is never sent: it has
// no token to refuse on.
if CommandLine.arguments.contains("--refusal-smoke") {
    let ledgerRoot = FileManager.default.temporaryDirectory.appending(component: "motion-refusal-\(UUID().uuidString)")
    let gate = SpendGate(client: client, ledger: IdempotencyLedger(root: ledgerRoot))
    func noLease(_ when: String) async -> PodStatus? {
        guard let pod = try? await client.get(PodStatus.self, "v1", "pod") else {
            print("FAIL GET /v1/pod \(when)"); return nil
        }
        guard pod.lease == nil else {
            print("FAIL a pod is leased \(when) — refusing to continue"); return nil
        }
        print("ok   no lease \(when)")
        return pod
    }
    guard let pod = await noLease("before"), let runID = pod.runId else { exit(1) }
    let bogus = "refusal-smoke-\(UUID().uuidString)"
    let cases: [(String, SpendIntent, String)] = [
        ("confirm stale panel_token", .confirm(runID: runID, provider: .runpod, panelToken: bogus,
                                               gpu: pod.gpu, tryon: nil), "stale_panel"),
        ("regen stale run_token", .regen(runID: runID, index: "0", runToken: bogus, guidance: []), "stale_panel"),
        ("resume stale run_token", .resume(runID: runID, provider: .runpod, runToken: bogus, gpu: pod.gpu), "stale_run"),
    ]
    var smokeFailed = 0
    for (name, intent, expected) in cases {
        let result = await gate.perform(intent, label: "refusal smoke: \(name)") { _, _ in }
        if case .refused(409, expected, _, _) = result {
            print("ok   \(name) → 409 \(expected)")
        } else {
            print("FAIL \(name): \(result)")
            smokeFailed += 1
        }
    }
    func describe(_ raw: RawSpendResponse) -> String {
        switch raw {
        case .http(let status, _): "HTTP \(status)"
        case .transport(let detail): "transport: \(detail)"
        }
    }
    /// Why the kill must not be sent, or nil. Every read must succeed; a run
    /// with no journal (404) is the only error that means "not live".
    func idleKillBlocker() async -> String? {
        guard let fresh = try? await client.get(PodStatus.self, "v1", "pod") else {
            return "GET /v1/pod failed"
        }
        guard let tryon = try? await client.get(TryonPreviews.self, "v1", "runs", runID, "tryon") else {
            return "the try-on read failed"
        }
        var runLive = false
        do {
            runLive = try await client.get(RunDetail.self, "v1", "runs", runID).status.isLive
        } catch APIError.server(status: 404, _, _) {
            runLive = false
        } catch {
            return "GET /v1/runs/\(runID) failed: \(error)"
        }
        if fresh.lease != nil { return "a pod is leased" }
        if fresh.killRunning { return "a kill is running" }
        if tryon.phaseARunning { return "Phase A is running" }
        if runLive { return "the run is live" }
        return nil
    }
    if let blocker = await idleKillBlocker() {
        print("skip idle kill (\(blocker))")
    } else {
        let raw = await client.spendPost(["v1", "runs", runID, "kill"], body: Data("{}".utf8),
                                         idempotencyKey: UUID().uuidString)
        if case let .http(409, body) = raw,
           String(decoding: body, as: UTF8.self).contains("\"nothing_running\"") {
            print("ok   idle kill → 409 nothing_running")
        } else {
            print("FAIL idle kill: \(describe(raw))")
            smokeFailed += 1
        }
    }
    let beforeMigrate = try? await client.get(PodStatus.self, "v1", "pod")
    if beforeMigrate == nil {
        print("skip migrate (GET /v1/pod failed)")
    } else if beforeMigrate?.migration?.running == true {
        print("skip migrate (a migration is running)")
    } else if beforeMigrate?.lease != nil {
        print("skip migrate (a pod is leased)")
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
    if await noLease("after") == nil { smokeFailed += 1 }
    try? FileManager.default.removeItem(at: ledgerRoot)
    exit(smokeFailed == 0 ? 0 : 1)
}

await check("GET /v1/health") { _ = try await client.health() }
await check("GET /v1/pipelines") {
    _ = try await client.get(PipelineCatalogResponse.self, "v1", "pipelines")
}
await check("GET /v1/draft") {
    _ = try await client.get(Draft.self, "v1", "draft")
}
var newestRun: String?
await check("GET /v1/runs") { newestRun = try await client.get(RunsResponse.self, "v1", "runs").runs.first?.id }
if let newestRun {
    await check("GET /v1/runs/{newest}") { _ = try await client.get(RunDetail.self, "v1", "runs", newestRun) }
} else {
    print("skip GET /v1/runs/{id} (no runs on the server)")
}
var slot: PodStatus?
await check("GET /v1/pod") { slot = try await client.get(PodStatus.self, "v1", "pod") }
// Cached stock (no ?force=1); the migration block is decoded inside PodStatus.
await check("GET /v1/gpu/stock") {
    _ = try await client.get(GpuStock.self, timeout: 60, "v1", "gpu", "stock")
}
// No ?vast=1: the Vast credit is a ~30 s subprocess and is opt-in.
await check("GET /v1/balance") {
    _ = try await client.get(Balance.self, timeout: 45, "v1", "balance")
}
if let runID = slot?.runId {
    await check("GET /v1/runs/{slot}/tryon") {
        _ = try await client.get(TryonPreviews.self, "v1", "runs", runID, "tryon")
    }
    // Cached stock (no ?force=1); the Vast quote inside can be slow.
    await check("GET /v1/runs/{slot}/rent-panel") {
        _ = try await client.get(RentPanel.self, timeout: 120, "v1", "runs", runID, "rent-panel")
    }
} else {
    print("skip GET /v1/runs/{slot}/tryon and rent-panel (no run_id on /v1/pod)")
}
await check("GET /v1/materials") {
    _ = try await client.get(MaterialsResponse.self, "v1", "materials")
}
var newestVideo: (batch: String, file: String)?
await check("GET /v1/outputs") {
    let batches = try await client.get(OutputsResponse.self, "v1", "outputs").outputs
    for batch in batches {
        if let file = batch.files.first(where: \.isVideo) {
            newestVideo = (batch.batch, file.name)
            break
        }
    }
}
if let newestVideo {
    await check("GET /v1/outputs/{batch}/{video} Range") {
        let response = try await client.byteRange(
            from: 0, length: 1024, "v1", "outputs", newestVideo.batch, newestVideo.file)
        guard response.acceptsRanges, response.totalLength != nil, !response.data.isEmpty else {
            throw ContractError.invalidRangeResponse
        }
    }
} else {
    print("skip GET /v1/outputs/{batch}/{video} Range (no videos on the server)")
}
exit(failed == 0 ? 0 : 1)

enum ContractError: Error {
    case invalidRangeResponse
}
