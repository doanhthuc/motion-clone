import Foundation
import Observation

/// Phase A → try-on previews → rent panel → confirm/resume for the one run
/// slot (API spec §5.8). Reads go straight to `APIClient`; every spend goes
/// through `SpendSending`, which owns the key and the retries. Nothing here
/// ever calls `perform` except in response to one user tap.
@MainActor @Observable
public final class RunFlow {
    public enum Phase: Equatable, Sendable {
        case loading, compose, phaseARunning, previews, rentPanel
        case choiceRequired(panelToken: String, provider: SpendProvider)
        case started(runID: String)
        case outcomeUnknown
    }

    public enum Entry: Sendable { case newJob, existing }

    static let localTryonProviders: Set<String> = ["gemini", "qwen-max"]

    public private(set) var phase: Phase = .loading
    public private(set) var pod: PodStatus?
    public private(set) var tryon: TryonPreviews?
    public private(set) var draft: Draft?
    public private(set) var catalog: [Pipeline] = []
    public private(set) var panel: RentPanel?
    public private(set) var error: APIError?
    /// Server refusals verbatim (409/422), or a notice about the last spend.
    public private(set) var message: String?
    /// Set while a spend request is outstanding; every spend button disables.
    public private(set) var inFlightLabel: String?
    public private(set) var retryNote: String?
    /// The last spend got no definitive answer; "Check again" resends its key.
    public private(set) var needsRecheck = false
    /// Increments when a Phase A or regenerate finishes — the image cache key.
    public private(set) var imageGeneration = 0
    public private(set) var versions: [String: [Data]] = [:]
    public private(set) var isLoadingPanel = false
    public var selectedProvider: SpendProvider = .runpod
    /// RootView switches to the Runs tab (pod strip) and clears it.
    public private(set) var podRequested = false

    private let client: APIClient
    private let gate: any SpendSending
    private let sleep: @Sendable (Duration) async throws -> Void
    private var images: [String: Data] = [:]
    private var keptKeys: Set<String> = []
    private var pendingKind: SpendKind?
    private var didReplay = false

    public init(client: APIClient, gate: any SpendSending,
                sleep: @escaping @Sendable (Duration) async throws -> Void = { try await Task.sleep(for: $0) }) {
        self.client = client
        self.gate = gate
        self.sleep = sleep
    }

    // MARK: derived

    public var runID: String? { pod?.runId }
    public var isSpending: Bool { inFlightLabel != nil }

    /// Preview try-on is the primary action only when some job would call a
    /// hosted try-on provider. A pipeline without a try-on stage never does.
    public var hasLocalTryon: Bool {
        guard let draft else { return false }
        func local(_ pipeline: String, _ provider: String) -> Bool {
            let stages = catalog.first { $0.id == pipeline }?.stages ?? []
            return stages.contains("tryon") && Self.localTryonProviders.contains(provider)
        }
        let current = draft.missing.isEmpty && local(draft.pipeline, draft.provider)
        return current || draft.batch.contains { local($0.pipeline, $0.provider) }
    }

    /// Only offered when the server says a rental failed and nothing is leased.
    public var canRetryRental: Bool {
        pod?.failedRental != nil && pod?.lease == nil && runID != nil
    }

    public var needsTryonPolling: Bool {
        if phase == .phaseARunning || tryon?.phaseARunning == true { return true }
        return tryon?.previews.contains { $0.status == .pending || $0.status == .running } ?? false
    }

    public func isKept(_ index: String) -> Bool {
        keptKeys.contains("\(index)#\(imageGeneration)")
    }

    // MARK: loading

    public func start(_ entry: Entry) async {
        phase = .loading
        error = nil
        do {
            pod = try await client.get(PodStatus.self, "v1", "pod")
        } catch {
            self.error = error
            return
        }
        guard let runID else {
            error = .decoding("GET /v1/pod returned no run_id")
            return
        }
        do {
            async let t = client.get(TryonPreviews.self, "v1", "runs", runID, "tryon")
            async let d = client.get(Draft.self, "v1", "draft")
            async let c = client.get(PipelineCatalogResponse.self, "v1", "pipelines")
            let (tryon, draft, catalog) = try await (t, d, c)
            self.tryon = tryon
            self.draft = draft
            self.catalog = catalog.pipelines
        } catch {
            self.error = apiError(error)
            return
        }
        if tryon?.phaseARunning == true {
            phase = .phaseARunning
        } else if entry == .existing, !(tryon?.previews.isEmpty ?? true) {
            phase = .previews
        } else {
            phase = .compose
        }
    }

    /// Cheap (files only on the server). Run detail uses it to decide on
    /// Retry rental without disturbing whatever phase the flow is in.
    public func refreshPod() async {
        do {
            pod = try await client.get(PodStatus.self, "v1", "pod")
        } catch {
            self.error = error
        }
    }

    public func refreshTryon() async {
        guard let runID else { return }
        // Deliberately excludes `phase == .phaseARunning`: `apply` sets that
        // optimistically right before this call, and an immediate refresh can
        // still return the pre-spend snapshot (the manifest write hasn't
        // landed yet) — using the optimistic phase here would misread that
        // stale "done" snapshot as "just finished" and bounce back to
        // `.previews` a beat early.
        let wasRunning = tryon?.phaseARunning == true
        do {
            let fresh = try await client.get(TryonPreviews.self, "v1", "runs", runID, "tryon")
            tryon = fresh
            error = nil
            if wasRunning && !fresh.phaseARunning {
                imageGeneration += 1
                images = [:]
                versions = [:]
                if phase == .phaseARunning { phase = .previews }
            } else if fresh.phaseARunning, phase == .compose || phase == .previews {
                phase = .phaseARunning
            }
        } catch {
            self.error = error
        }
    }

    /// Every 5 s while the view is visible and something is still running.
    public func pollTryon(interval: Duration = .seconds(5)) async {
        while !Task.isCancelled {
            if needsTryonPolling { await refreshTryon() }
            do { try await sleep(interval) } catch { return }
        }
    }

    // MARK: previews

    public func image(index: String) async -> Data? {
        let key = "\(index)#\(imageGeneration)"
        if let cached = images[key] { return cached }
        guard let runID else { return nil }
        guard let data = try? await client.data("v1", "runs", runID, "tryon", index) else { return nil }
        images[key] = data
        return data
    }

    /// The API returns no version count, so probe 1, 2, … until 404 (max 10).
    public func loadVersions(index: String) async {
        guard let runID, versions[index] == nil else { return }
        var found: [Data] = []
        for n in 1...10 {
            guard let data = try? await client.data("v1", "runs", runID, "tryon", index, "versions", "\(n)") else { break }
            found.append(data)
        }
        versions[index] = found
    }

    public func keep(index: String) async {
        guard let runID else { return }
        do {
            _ = try await client.post(TryonLibraryRecord.self,
                                      body: TryonKeepRequest(runId: runID, index: index),
                                      "v1", "tryon-library")
            keptKeys.insert("\(index)#\(imageGeneration)")
        } catch {
            message = error.userMessage
        }
    }

    // MARK: spends (Task 6)

    public func startPhaseA() async {
        let jobs = draft?.jobs ?? 0
        await spend(.phaseA, label: "Try-on preview · \(jobs) job\(jobs == 1 ? "" : "s")")
    }

    public func regenerate(index: String, guidance: Set<Guidance>) async {
        guard let runID, let token = tryon?.runToken else { return }
        let ordered = Guidance.allCases.filter(guidance.contains)
        await spend(.regen(runID: runID, index: index, runToken: token, guidance: ordered),
                    label: "Regenerate try-on #\(index)")
    }

    public func dismissMessage() { message = nil }
    public func acknowledgePodRequest() { podRequested = false }

    // MARK: spend plumbing

    func spend(_ intent: SpendIntent, label: String) async {
        guard inFlightLabel == nil else {
            message = "Another spend request is still in flight."
            return
        }
        inFlightLabel = label
        retryNote = nil
        message = nil
        needsRecheck = false
        pendingKind = intent.kind
        let result = await gate.perform(intent, label: label) { [weak self] attempt, reason in
            Task { @MainActor in self?.noteRetry(attempt, reason) }
        }
        inFlightLabel = nil
        retryNote = nil
        await apply(result, kind: intent.kind)
    }

    private func noteRetry(_ attempt: Int, _ reason: SpendRetryReason) {
        guard inFlightLabel != nil else { return }
        retryNote = reason == .botBusy
            ? "The bot is busy — retry \(attempt) of 3"
            : "No answer — retry \(attempt) of 2 with the same request"
    }

    func apply(_ result: SpendResult, kind: SpendKind) async {
        switch result {
        case let .accepted(runID, _):
            needsRecheck = false
            switch kind {
            case .phaseA, .regen:
                phase = .phaseARunning
                await refreshTryon()
            case .confirm, .resume:
                phase = .started(runID: runID ?? self.runID ?? "")
                pod = try? await client.get(PodStatus.self, "v1", "pod")
            }
        case let .refused(status, code, text, panelToken):
            needsRecheck = false
            await applyRefusal(status: status, code: code, text: text, panelToken: panelToken, kind: kind)
        case .outcomeUnknown:
            needsRecheck = false
            phase = .outcomeUnknown
            message = "Couldn't tell whether this went through — check the pod before trying again."
            podRequested = true
        case let .busy(attempts):
            message = "The bot stayed busy after \(attempts) attempts. Nothing was recorded — tap again when ready."
        case let .unreachable(detail):
            needsRecheck = true
            pendingKind = kind
            message = "No answer from the VPS (\(detail)). The request is saved — Check again resends it without spending twice."
        case let .expired(label, createdAt):
            needsRecheck = false
            let when = createdAt.map { $0.formatted(date: .omitted, time: .shortened) } ?? "earlier"
            message = "\(label) from \(when) couldn't be verified. Check Runs and the pod before trying again."
        case let .notSent(reason):
            message = reason
        }
    }

    /// Filled in by Task 7 (stale panel, choice_required, stale_run). For
    /// now: the server's text verbatim for 409/422, the mapped text otherwise.
    func applyRefusal(status: Int, code: String, text: String, panelToken: String?,
                      kind: SpendKind) async {
        message = APIError.server(status: status, code: code, message: text).userMessage
        if code == "stale_panel", kind == .regen { await refreshTryon() }
    }

    private func apiError(_ error: any Error) -> APIError {
        error as? APIError ?? .transport(error.localizedDescription)
    }
}
