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
    /// "Checking the earlier Confirm…" while the launch replay is outstanding.
    public private(set) var pendingNotice: String?

    private let client: APIClient
    private let gate: any SpendSending
    private let sleep: @Sendable (Duration) async throws -> Void
    private var images: [String: Data] = [:]
    private var keptKeys: Set<String> = []
    private var pendingIntent: SpendIntent?
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

    /// Every spend button's gate. An unanswered spend (`needsRecheck`) or the
    /// launch replay (`pendingNotice`) must be resolved first — through
    /// "Check again", which resends the saved key — never by a new tap.
    public var canSpend: Bool { !isSpending && !needsRecheck && pendingNotice == nil }

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
        guard !isSpending else { return }
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
        // A fresh load may be re-entering a stale in-memory instance (the
        // view was closed while Phase A/regen finished elsewhere) — always
        // invalidate the image/version caches so `image(index:)` never hands
        // back bytes from before this read. `isKept` is keyed by generation
        // too, so it resets for free.
        imageGeneration += 1
        images = [:]
        versions = [:]
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
        let wasRunning = tryon?.phaseARunning == true || phase == .phaseARunning
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

    // MARK: rent panel

    public func continueToRent() async {
        phase = .rentPanel
        await loadPanel(force: false)
    }

    /// `force` = pull-to-refresh: fresh stock instead of the bot's cache.
    public func loadPanel(force: Bool) async {
        guard let runID else { return }
        isLoadingPanel = true
        defer { isLoadingPanel = false }
        do {
            // The bot's Vast quote can take up to 120 s (bot.py `_rent_panel_data`);
            // Cloudflare's ~100 s origin ceiling may answer first.
            let fresh = force
                ? try await client.get(RentPanel.self, query: [URLQueryItem(name: "force", value: "1")],
                                       timeout: 120, "v1", "runs", runID, "rent-panel")
                : try await client.get(RentPanel.self, timeout: 120, "v1", "runs", runID, "rent-panel")
            panel = fresh
            error = nil
            if quote(for: selectedProvider) == nil {
                selectedProvider = quote(for: .runpod) != nil ? .runpod
                    : quote(for: .vast) != nil ? .vast : .runpod
            }
        } catch {
            self.error = error
        }
    }

    /// No quote → no spend button for that row.
    public func quote(for provider: SpendProvider) -> Double? {
        guard let panel else { return nil }
        switch provider {
        case .runpod:
            guard !panel.runpod.soldOut else { return nil }
            return CostEstimate.quote(estimateMin: panel.estimateMin, usdPerHr: panel.runpod.usdPerHr)
        case .vast:
            guard panel.vast.canSpend else { return nil }
            return panel.vast.sessionUsd
                ?? CostEstimate.quote(estimateMin: panel.estimateMin, usdPerHr: panel.vast.usdPerHr)
        }
    }

    public func canConfirm(_ provider: SpendProvider) -> Bool {
        panel != nil && quote(for: provider) != nil && canSpend && !isLoadingPanel
    }

    private func confirmLabel(_ provider: SpendProvider, suffix: String = "") -> String {
        let where_ = provider == .runpod ? (panel?.runpod.gpu ?? "RunPod") : "Vast"
        let price = quote(for: provider).map { " · ~\(Format.usd($0)) quote" } ?? ""
        return "Confirm\(suffix) · \(where_)\(price)"
    }

    public func confirm() async {
        guard let runID, let panel, canConfirm(selectedProvider) else { return }
        let provider = selectedProvider
        await spend(.confirm(runID: runID, provider: provider, panelToken: panel.panelToken,
                             gpu: provider == .runpod ? panel.runpod.gpu : nil, tryon: nil),
                    label: confirmLabel(provider))
    }

    /// The reuse/rerun answer to `choice_required` — its own tap, its own key.
    /// The provider comes from the phase (the confirm that was actually sent,
    /// set from the refused `SpendIntent`), never from `selectedProvider`,
    /// which may have moved on while this was outstanding. Refuses to send
    /// without a fresh panel and quote — otherwise a stale-GPU guard could be
    /// bypassed and no price would be shown before the tap.
    public func choose(_ choice: TryonChoice) async {
        guard let runID, case let .choiceRequired(token, provider) = phase else { return }
        guard let panel, quote(for: provider) != nil else {
            message = "Couldn't price this confirm — pull to refresh the rent panel, then choose again."
            return
        }
        await spend(.confirm(runID: runID, provider: provider, panelToken: token,
                             gpu: provider == .runpod ? panel.runpod.gpu : nil, tryon: choice),
                    label: confirmLabel(provider, suffix: choice == .reuse ? " (reuse try-on)" : " (re-run try-on)"))
    }

    // MARK: resume

    public func retryRental() async {
        guard canRetryRental, let runID, let gpu = pod?.gpu else { return }
        await refreshTryon()      // resume needs the CURRENT run_token
        guard let token = tryon?.runToken else { return }
        await spend(.resume(runID: runID, provider: .runpod, runToken: token, gpu: gpu),
                    label: "Retry rental · \(gpu)")
    }

    // MARK: recheck and replay

    /// Resends the saved request with its saved key. Never mints a new one.
    /// The intent comes from the gate's ledger — the request actually being
    /// resent — and only falls back to the in-memory copy.
    public func recheck() async {
        guard needsRecheck, inFlightLabel == nil else { return }
        let saved = await gate.pending()
        guard let intent = saved?.intent ?? pendingIntent else { needsRecheck = false; return }
        inFlightLabel = "Checking the earlier request…"
        message = nil
        let result = await gate.recheck { [weak self] attempt, reason in
            Task { @MainActor in self?.noteRetry(attempt, reason) }
        }
        inFlightLabel = nil
        retryNote = nil
        if case let .notSent(reason) = result {
            message = reason
            // The gate had nothing left to resend: there is nothing to check.
            if await gate.pending() == nil {
                needsRecheck = false
                pendingIntent = nil
            }
            return
        }
        await apply(result, intent: intent)
    }

    public func replayPendingOnce() async {
        guard !didReplay else { return }
        didReplay = true
        guard let entry = await gate.pending() else { return }
        pendingNotice = "Checking the earlier \(entry.label)…"
        let result = await gate.replayPending()
        pendingNotice = nil
        guard let result else { return }
        if pod == nil { pod = try? await client.get(PodStatus.self, "v1", "pod") }
        await apply(result, intent: entry.intent)
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
        // `needsRecheck`/`pendingIntent` are left alone until the answer is
        // known: a `.notSent` (e.g. an earlier request still pending) must not
        // hide "Check again" or replace the intent it would apply.
        let result = await gate.perform(intent, label: label) { [weak self] attempt, reason in
            Task { @MainActor in self?.noteRetry(attempt, reason) }
        }
        inFlightLabel = nil
        retryNote = nil
        await apply(result, intent: intent)
    }

    private func noteRetry(_ attempt: Int, _ reason: SpendRetryReason) {
        guard inFlightLabel != nil else { return }
        retryNote = reason == .botBusy
            ? "The bot is busy — retry \(attempt) of 3"
            : "No answer — retry \(attempt) of 2 with the same request"
    }

    func apply(_ result: SpendResult, intent: SpendIntent) async {
        let kind = intent.kind
        switch result {
        case let .accepted(runID, _):
            needsRecheck = false
            pendingIntent = nil
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
            pendingIntent = nil
            await applyRefusal(status: status, code: code, text: text, panelToken: panelToken, intent: intent)
        case .outcomeUnknown:
            needsRecheck = false
            pendingIntent = nil
            phase = .outcomeUnknown
            message = "Couldn't tell whether this went through — check the pod before trying again."
            podRequested = true
        case let .busy(attempts):
            needsRecheck = false   // the gate cleared the ledger: nothing pending
            pendingIntent = nil
            message = "The bot stayed busy after \(attempts) attempts. Nothing was recorded — tap again when ready."
        case let .unreachable(detail):
            needsRecheck = true
            pendingIntent = intent
            message = "No answer from the VPS (\(detail)). The request is saved — Check again resends it without spending twice."
        case let .expired(label, createdAt):
            needsRecheck = false
            pendingIntent = nil
            let when = createdAt.map { $0.formatted(date: .omitted, time: .shortened) } ?? "earlier"
            message = "\(label) from \(when) couldn't be verified. Check Runs and the pod before trying again."
        case let .notSent(reason):
            message = reason   // nothing changed: keep any pending recheck as it was
        }
    }

    /// The provider for `choice_required` comes from the confirmed intent,
    /// never `selectedProvider` — it may have moved on since the tap that
    /// caused this refusal. A missing panel is reloaded so a price can be
    /// shown before Reuse/Re-run are tappable (`choose` refuses without one).
    func applyRefusal(status: Int, code: String, text: String, panelToken: String?,
                      intent: SpendIntent) async {
        message = APIError.server(status: status, code: code, message: text).userMessage
        switch (code, intent.kind) {
        case ("choice_required", .confirm):
            if let panelToken, case let .confirm(_, provider, _, _, _) = intent {
                phase = .choiceRequired(panelToken: panelToken, provider: provider)
                if panel == nil { await loadPanel(force: false) }
            }
        case ("stale_panel", .confirm):
            // Never an automatic re-confirm: re-read, show the new price, wait.
            phase = .rentPanel
            await loadPanel(force: false)
        case ("stale_panel", .regen), ("stale_run", .resume):
            await refreshTryon()
        case ("stale_panel", .resume):
            // The server's gpu-mismatch code: the .env card changed since the
            // pod was read. Re-read it so Retry rental names the card it sends.
            await refreshPod()
        default:
            break
        }
    }

    private func apiError(_ error: any Error) -> APIError {
        error as? APIError ?? .transport(error.localizedDescription)
    }
}
