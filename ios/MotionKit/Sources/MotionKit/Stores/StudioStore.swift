import Foundation
import Observation

/// Image Studio state: the project list for the sidebar, the open project,
/// and the composer (spec 2026-09-26). Generations are sent with an
/// Idempotency-Key and resent with the SAME key after a transport failure,
/// so a dropped connection never bills twice. Not routed through `SpendGate`:
/// that gate serialises pod spends and keeps a ledger across launches, while
/// a Studio send costs cents and its outcome is visible in the project itself.
@MainActor @Observable
public final class StudioStore {
    public static let pollInterval: Duration = .seconds(3)
    static let sendAttempts = 3

    public private(set) var catalog: StudioCatalog?
    public private(set) var projects: [StudioProjectSummary] = []
    public private(set) var project: StudioProject?
    public private(set) var attachments: [StudioRef] = []
    public private(set) var isSending = false
    public var prompt = ""
    public var modelKey = ""
    public var aspect = "9:16"
    public var count = 1
    public var message: String?

    private let client: APIClient
    private let sleep: @Sendable (Duration) async throws -> Void
    private let makeKey: @Sendable () -> String
    private var images: [String: Data] = [:]
    private var pollTask: Task<Void, Never>?
    /// Bumped every time a poll task is started. A finishing task only clears
    /// `pollTask` if it's still the one it started as — otherwise a cancelled
    /// task (e.g. from `close()`) finishing after a newer one was already
    /// started (from the following `open()`) would erase that newer task's
    /// reference, letting a third caller think nothing is polling and start a
    /// duplicate.
    private var pollGeneration = 0
    /// Off in tests, which call `pollUntilIdle()` themselves so the request count is exact.
    private let autoPoll: Bool

    public init(client: APIClient,
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) },
                makeKey: @escaping @Sendable () -> String = { UUID().uuidString },
                autoPoll: Bool = true) {
        self.client = client
        self.sleep = sleep
        self.makeKey = makeKey
        self.autoPoll = autoPoll
    }

    // MARK: derived

    public var selectedModel: StudioModelInfo? { catalog?.models.first { $0.key == modelKey } }
    public var estimateUSD: Double { (selectedModel?.priceUsd ?? 0) * Double(count) }
    public var hasRunning: Bool {
        project?.generations.contains { $0.status == .running || $0.status == .queued } ?? false
    }

    public func disabledReason(for model: StudioModelInfo) -> String? {
        if !model.available { return "\(model.label) isn't configured on the server." }
        if attachments.count > model.maxRefs {
            return "\(model.label) takes at most \(model.maxRefs) reference images."
        }
        return nil
    }

    public var canSend: Bool {
        guard let model = selectedModel, project != nil, !isSending else { return false }
        return !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && disabledReason(for: model) == nil
    }

    // MARK: catalog & projects

    public func loadCatalog() async {
        do {
            let cat = try await client.get(StudioCatalog.self, "v1", "studio", "models")
            catalog = cat
            if selectedModel == nil { modelKey = cat.models.first { $0.default }?.key ?? cat.models.first?.key ?? "" }
        } catch { message = error.userMessage }
    }

    public func loadProjects() async {
        do {
            projects = try await client.get(StudioProjectsResponse.self, "v1", "studio", "projects").projects
        } catch { message = error.userMessage }
    }

    public func open(_ id: String) async {
        if project?.id != id { attachments = []; prompt = "" }
        do {
            project = try await client.get(StudioProjectResponse.self, "v1", "studio", "projects", id).project
            startPolling()
        } catch { message = error.userMessage }
    }

    public func close() {
        pollTask?.cancel(); pollTask = nil
        project = nil
    }

    public func createProject() async -> String? {
        struct Body: Encodable, Sendable { let title: String }
        do {
            let created = try await client.post(StudioProjectResponse.self, body: Body(title: ""),
                                                "v1", "studio", "projects").project
            project = created
            attachments = []; prompt = ""
            await loadProjects()
            return created.id
        } catch { message = error.userMessage; return nil }
    }

    public func rename(_ id: String, to title: String) async {
        struct Body: Encodable, Sendable { let title: String }
        do {
            let updated = try await client.patch(StudioProjectResponse.self, body: Body(title: title),
                                                 "v1", "studio", "projects", id).project
            if project?.id == id { project = updated }
            await loadProjects()
        } catch { message = error.userMessage }
    }

    public func delete(_ id: String) async {
        do {
            try await client.delete("v1", "studio", "projects", id)
            if project?.id == id { close() }
            await loadProjects()
        } catch { message = error.userMessage }
    }

    // MARK: composer

    public func attach(_ ref: StudioRef) {
        if !attachments.contains(ref) { attachments.append(ref) }
    }

    public func detach(_ ref: StudioRef) { attachments.removeAll { $0 == ref } }

    public func send() async -> Bool {
        guard canSend else { return false }
        // Captured before the await: up to 3 attempts x 60 s can outlast the user
        // switching projects, and a prompt they've since typed into the newly
        // opened project must survive this send finishing, not be wiped by it.
        let startedProject = project?.id
        let ok = await post(prompt: prompt, model: modelKey, aspect: aspect, count: count, refs: attachments)
        if ok && project?.id == startedProject { prompt = "" }
        return ok
    }

    /// A new generation with the failed one's parameters and the same references.
    /// Refs are resent as `snapshot`s of the failed generation's own copies —
    /// never the original `{kind, id}` — so retry still works after the source
    /// material was pruned or the try-on library entry it pointed at was deleted.
    ///
    /// Only the failed slots are re-bought: one failed Gemini tile out of four
    /// must not re-buy all four. Qwen is the exception — a Qwen call returns
    /// every image of the generation at once, so its slots fail together and
    /// the whole count is resent.
    public func retry(_ generation: StudioGeneration) async -> Bool {
        guard let pid = project?.id else { return false }
        let refs = generation.refs.map { ref -> StudioRef in
            guard let file = ref.file else { return ref }
            return StudioRef(kind: .snapshot, id: "\(pid)/\(file)")
        }
        return await post(prompt: generation.prompt, model: generation.model, aspect: generation.aspect,
                          count: retryCount(for: generation), refs: refs)
    }

    /// How many images `retry(_:)` asks for. The grid shows its price on the button.
    public func retryCount(for generation: StudioGeneration) -> Int {
        let provider = catalog?.models.first { $0.key == generation.model }?.provider
        if provider == "qwen" { return generation.count }
        return max(1, generation.slots.filter { $0.status == .error }.count)
    }

    private func post(prompt: String, model: String, aspect: String, count: Int, refs: [StudioRef]) async -> Bool {
        guard let pid = project?.id, !isSending else { return false }
        struct Ref: Encodable { let kind: String; let id: String }
        struct Body: Encodable { let prompt: String; let model: String; let aspect: String; let count: Int; let refs: [Ref] }
        let body: Data
        do {
            body = try JSONEncoder().encode(Body(prompt: prompt, model: model, aspect: aspect, count: count,
                                                 refs: refs.map { Ref(kind: $0.kind.rawValue, id: $0.id) }))
        } catch { message = "Couldn't encode the request."; return false }
        isSending = true
        defer { isSending = false }
        message = nil
        let key = makeKey()
        for attempt in 1...Self.sendAttempts {
            switch await client.spendPost(["v1", "studio", "projects", pid, "generations"], body: body,
                                          idempotencyKey: key, timeout: 60) {
            case .http(status: 202, body: let data):
                guard let gen = try? MotionJSON.decoder.decode(StudioGenerationResponse.self, from: data).generation
                else { message = "The server's answer couldn't be read."; return false }
                // The open project may have changed while this was in flight (up to
                // 3 x 60 s): only fold the generation into the project it was sent
                // for, never into whatever project happens to be open now.
                if project?.id == pid {
                    project?.generations.append(gen)
                    startPolling()
                }
                return true
            case .http(status: 409, body: let data) where Self.errorCode(data) == "outcome_unknown":
                // An earlier request with this key is still being submitted on
                // the server. It will land as a generation of its own; resending
                // would only buy the same images twice. Treat it like a success:
                // re-read the project so it shows up and polling starts.
                message = "That generation is still being submitted — it will appear in the grid shortly."
                if project?.id == pid { await open(pid) }
                return true
            case .http(status: let status, body: let data):
                message = APIClient.error(status: status, body: data).userMessage
                return false
            case .transport(let reason):
                if attempt == Self.sendAttempts { message = "Couldn't reach the server: \(reason)"; return false }
                try? await sleep(.seconds(2))
            }
        }
        return false
    }

    private static func errorCode(_ body: Data) -> String? {
        if case .server(_, let code, _) = APIClient.error(status: 409, body: body) { return code }
        return nil
    }

    // MARK: polling

    private func startPolling() {
        guard autoPoll, hasRunning, pollTask == nil else { return }
        pollGeneration += 1
        let generation = pollGeneration
        pollTask = Task { [weak self] in
            await self?.pollUntilIdle()
            guard let self, self.pollGeneration == generation else { return }
            self.pollTask = nil
        }
    }

    /// Re-reads the open project every `pollInterval` while any slot is
    /// unfinished. Internal for tests, which call it directly.
    func pollUntilIdle() async {
        while hasRunning, let id = project?.id, !Task.isCancelled {
            try? await sleep(Self.pollInterval)
            guard let fresh = try? await client.get(StudioProjectResponse.self, "v1", "studio", "projects", id).project,
                  project?.id == id else { continue }
            project = fresh
        }
        // A cancelled task (e.g. `close()`) has nothing left to refresh, and
        // `loadProjects()` can set `message` on failure — noise for a screen
        // the phone has already left.
        guard !Task.isCancelled else { return }
        await loadProjects()
    }

    // MARK: images & promote

    public func image(projectID: String, imageID: String) async -> Data? {
        let key = "studio/\(projectID)/\(imageID)"
        if let cached = images[key] { return cached }
        guard let data = try? await client.data("v1", "studio", "projects", projectID, "images", imageID) else { return nil }
        images[key] = data
        return data
    }

    /// Thumbnail for an attached (or historical) reference. `nil` draws a placeholder.
    public func thumbnail(for ref: StudioRef) async -> Data? {
        let key = "\(ref.kind.rawValue)/\(ref.id)"
        if let cached = images[key] { return cached }
        let parts = ref.id.split(separator: "/", maxSplits: 1).map(String.init)
        let data: Data?
        switch ref.kind {
        case .material where parts.count == 2:
            data = try? await client.data("v1", "materials", parts[0], parts[1], "thumb")
        case .tryon:
            data = try? await client.data("v1", "tryon-library", ref.id, "image")
        case .runTryon where parts.count == 2:
            data = try? await client.data("v1", "runs", parts[0], "tryon", parts[1])
        case .studio where parts.count == 2:
            data = try? await client.data("v1", "studio", "projects", parts[0], "images", parts[1])
        case .snapshot where parts.count == 2:
            data = try? await client.data("v1", "studio", "projects", parts[0], "refs", parts[1])
        default:
            data = nil
        }
        if let data { images[key] = data }
        return data
    }

    public func promote(imageID: String, to target: StudioPromoteTarget) async -> String? {
        guard let pid = project?.id else { return nil }
        struct Body: Encodable, Sendable { let to: String }
        do {
            switch target {
            case .material:
                let m = try await client.post(StudioPromoteMaterial.self, body: Body(to: "material"),
                                              "v1", "studio", "projects", pid, "images", imageID, "promote")
                return "Added to Materials as \(m.material.name)."
            case .tryon:
                _ = try await client.post(StudioPromoteEntry.self, body: Body(to: "tryon"),
                                          "v1", "studio", "projects", pid, "images", imageID, "promote")
                return "Saved to the try-on library."
            }
        } catch { message = error.userMessage; return nil }
    }

    /// A temp file named `<imageID>.jpg|png` — Photos decides the type from the extension,
    /// and the image URL itself has none.
    public func download(imageID: String) async throws -> URL {
        guard let pid = project?.id else { throw APIError.transport("no project open") }
        let data = try await client.data("v1", "studio", "projects", pid, "images", imageID)
        let ext = data.starts(with: [0xFF, 0xD8, 0xFF]) ? "jpg" : "png"
        let dir = FileManager.default.temporaryDirectory.appending(component: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appending(component: "\(imageID).\(ext)")
        try data.write(to: file)
        return file
    }
}
