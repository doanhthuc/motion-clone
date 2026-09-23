import Foundation
import MotionKit

// Decodes every phase-1 route of the live API with the app's own models.
// GET only — spends nothing. Prints route names only, never bodies.

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
await check("GET /v1/pod") { _ = try await client.get(PodStatus.self, "v1", "pod") }
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
