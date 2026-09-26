import Foundation
import Observation

/// One outfit of a cross build and the saved try-on to seed it from.
public struct CrossOutfit: Sendable, Equatable, Identifiable {
    public let outfitID: String
    public var seedID: String?
    public var id: String { outfitID }

    public init(outfitID: String, seedID: String?) {
        self.outfitID = outfitID
        self.seedID = seedID
    }
}

/// One basket job of a cross build: an outfit, and the driver it is paired
/// with when drivers are multi-selected (`nil` keeps the shared driver slot).
public struct CrossStep: Sendable, Equatable {
    public let outfit: CrossOutfit
    public let driverID: String?

    public init(outfit: CrossOutfit, driverID: String?) {
        self.outfit = outfit
        self.driverID = driverID
    }
}

/// Cross build (Phase 6 spec §4): the edited job's own slots are shared, and
/// each outfit becomes one basket job through `PATCH {slots.outfit,
/// tryon_seed}` + `add-to-batch` — no new route (API spec §5.10). Free: no
/// spend gate. Runs sequentially and stops at the first failure; running
/// again re-plans from the server's draft, so nothing is added twice.
///
/// Multi-driver batch (2026-09-25 spec §4): with `drivers` non-empty the build
/// is outfits × drivers, outfit-major, and each step PATCHes `outfit` and
/// `driver` together. With `drivers` empty the driver stays a shared slot and
/// every write is what it was before drivers existed. Seeds stay per outfit —
/// `TryonLibraryStore.matches(slots:)` ignores the driver — because the server
/// runs one try-on per outfit and shares it across that outfit's drivers
/// (local providers, non-camera pipelines); `tryonCount` is the number of
/// try-ons the build pays for, so the provider calls are visible before Phase A.
@MainActor @Observable
public final class BatchComposer {
    public struct Progress: Equatable, Sendable {
        public var done: Int
        public let total: Int
        public init(done: Int, total: Int) { self.done = done; self.total = total }
    }

    /// A mistap guard, not a server limit, counted in basket jobs rather than
    /// outfits: since drivers multiply the build, the rent estimate grows with
    /// outfits × drivers — every job is its own motion + enhance on the GPU —
    /// while the try-ons (the old reason for capping outfits) are now shared
    /// across drivers and grow slower. 12 is the old outfit cap kept as a job
    /// cap, so a driver-less build is capped exactly as before.
    public nonisolated static let maxJobs = 12
    public nonisolated static let outfitRole = "outfit"
    public nonisolated static let driverRole = "driver"

    public private(set) var outfits: [CrossOutfit] = []
    /// Driver material ids, multi-selected. Empty means the edited job's own
    /// driver slot is shared by every outfit, exactly as before.
    public private(set) var drivers: [String] = []
    /// Why the last toggle was refused, rendered next to the picker: a toggle
    /// past `maxJobs` must never be silently ignored (spec §4). Any toggle that
    /// goes through clears it.
    public private(set) var capReason: String?
    public private(set) var isRunning = false
    public private(set) var progress: Progress?
    public private(set) var failure: String?
    /// How many basket jobs the last complete run added — the steps it
    /// executed, not the size of the selection it started from. A **Continue**
    /// re-plans from the server's draft and `pending(in:)` skips the outfits
    /// already basketed, so the selection size over-reports; this number is
    /// rendered on the screen where the user decides how much GPU to rent.
    public private(set) var lastAdded: Int?

    private let draft: DraftStore
    private let library: TryonLibraryStore

    public init(draft: DraftStore, library: TryonLibraryStore) {
        self.draft = draft
        self.library = library
    }

    public nonisolated static func supports(_ pipeline: Pipeline) -> Bool {
        let roles = Set(pipeline.required + pipeline.optional)
        return roles.contains("character") && roles.contains(outfitRole)
    }

    public nonisolated static func supportsDrivers(_ pipeline: Pipeline) -> Bool {
        (pipeline.required + pipeline.optional).contains(driverRole)
    }

    /// Whether the selected pipeline's try-on is guided by the driver, so the
    /// server cannot share one try-on across drivers. Read from the catalog's
    /// `stages` rather than the pipeline id: `camera-tryon` is the stage that
    /// locks `cameraAware: true` (`scripts/batchlib/pipelines.py:77-85`), so a
    /// future camera pipeline is caught whatever it is named.
    public var cameraAwareTryon: Bool {
        draft.selectedPipeline?.stages.contains("camera-tryon") == true
    }

    /// Basket jobs this selection builds. An empty driver list still builds one
    /// job per outfit, on the shared driver.
    public var jobCount: Int { outfits.count * max(drivers.count, 1) }

    /// Try-ons this build will pay for. With a local provider, Phase A makes
    /// one per unseeded outfit, since a seed skips the provider and the server
    /// shares an outfit's try-on across its drivers — unless the try-on is
    /// camera-aware, when every pair gets its own. Sharing exists only in
    /// Phase A, so a pod provider (e.g. `qwen`) runs its own try-on inside
    /// every job: one per job. The local set is `RunFlow.localTryonProviders`,
    /// not a second copy of it.
    public var tryonCount: Int {
        guard RunFlow.localTryonProviders.contains(draft.draft?.provider ?? "") else { return jobCount }
        let unseeded = outfits.filter { $0.seedID == nil }.count
        return cameraAwareTryon ? unseeded * max(drivers.count, 1) : unseeded
    }

    /// Roles the selection fills per step: the outfit always, the driver only
    /// when drivers are multi-selected.
    private var crossedRoles: Set<String> {
        drivers.isEmpty ? [Self.outfitRole] : [Self.outfitRole, Self.driverRole]
    }

    /// Every filled slot of the edited job except the crossed roles.
    public var sharedSlots: [String: String] {
        let crossed = crossedRoles
        return (draft.draft?.filledSlots ?? [:]).filter { !crossed.contains($0.key) }
    }

    /// What the per-outfit seeds depend on: every filled slot except the
    /// outfit and the driver, because `TryonLibraryStore.matches(slots:)`
    /// ignores the driver. This, not `sharedSlots`, is what a seed re-pick
    /// must watch: `sharedSlots` gains or loses `driver` whenever the first
    /// driver is toggled on or the last one off, and re-picking then would
    /// overwrite a seed chosen in the "Saved image" picker (an explicit nil
    /// included) over a change that cannot alter any match.
    public var seedKey: [String: String] {
        (draft.draft?.filledSlots ?? [:]).filter { $0.key != Self.outfitRole && $0.key != Self.driverRole }
    }

    /// Required roles still empty, the crossed roles excluded (the lists fill them).
    public var missingShared: [String] {
        let crossed = crossedRoles
        return (draft.draft?.missing ?? []).filter { !crossed.contains($0) }
    }

    /// `isBusy` is `isMutating || isValidating` (`DraftStore.swift:31-32`) and excludes
    /// `isRefreshing`, so without that third term a `load()` in flight would leave `canRun`
    /// true and `run()`'s own refresh would return at once on its `guard !isRefreshing`
    /// (`DraftStore.swift:62`) — planning from the cached draft, which is the silent
    /// under-add the re-read exists to prevent. The batch run button is
    /// `.disabled(!canRun)`, so this term is also what keeps it inert during a load
    /// instead of looking tappable.
    public var canRun: Bool {
        !isRunning && !draft.isBusy && !draft.isRefreshing
            && missingShared.isEmpty && !outfits.isEmpty
            && jobCount <= Self.maxJobs
            && draft.selectedPipeline.map(Self.supports) == true
            // Drivers picked, then a pipeline without a driver role selected:
            // the PATCH would name a slot the pipeline refuses.
            && (drivers.isEmpty || draft.selectedPipeline.map(Self.supportsDrivers) == true)
    }

    public func toggle(outfitID: String) {
        guard !isRunning else { return }
        if let index = outfits.firstIndex(where: { $0.outfitID == outfitID }) {
            outfits.remove(at: index)
        } else {
            guard fits(outfits: outfits.count + 1, drivers: drivers.count) else { return refuse() }
            outfits.append(CrossOutfit(outfitID: outfitID, seedID: matches(for: outfitID).first?.id))
        }
        selectionChanged()
    }

    public func toggle(driverID: String) {
        guard !isRunning else { return }
        if let index = drivers.firstIndex(of: driverID) {
            drivers.remove(at: index)
        } else {
            guard fits(outfits: outfits.count, drivers: drivers.count + 1) else { return refuse() }
            drivers.append(driverID)
        }
        selectionChanged()
    }

    /// Each dimension counts as at least 1, even while empty. Otherwise 13
    /// drivers could be picked with no outfits, and then every outfit toggle
    /// would be refused — a selection stuck until drivers are removed.
    private func fits(outfits: Int, drivers: Int) -> Bool {
        max(outfits, 1) * max(drivers, 1) <= Self.maxJobs
    }

    /// A refused toggle changes nothing, so it leaves `progress` and
    /// `lastAdded` alone: they still describe the selection on screen. The
    /// counts use the same floor of 1 as `fits`, so the reason never reads
    /// "× 0 drivers" or "is already 0".
    private func refuse() {
        let o = max(outfits.count, 1), d = max(drivers.count, 1)
        capReason = "At most \(Self.maxJobs) videos per batch — \(o) outfit\(o == 1 ? "" : "s") × "
            + "\(d) driver\(d == 1 ? "" : "s") is already \(o * d)."
    }

    private func selectionChanged() {
        capReason = nil
        // Both describe a run that no longer matches this selection: `progress`
        // survives a run on purpose (spec §4 wants k/N at the stop), but a
        // selection edited afterwards would render "1/3" against four rows.
        progress = nil
        lastAdded = nil
    }

    public func setSeed(_ seedID: String?, for outfitID: String) {
        guard !isRunning, let index = outfits.firstIndex(where: { $0.outfitID == outfitID }) else { return }
        outfits[index].seedID = seedID
        progress = nil
    }

    /// New Job (2026-09-26 spec) shows one Outfit card and one Driver card, both
    /// backed by this selection, so a crossed role the draft carries — left
    /// there by Saved try-ons' "Use in job", the Telegram bot, or a draft from
    /// before the redesign — is moved in here and cleared on the draft in one
    /// PATCH. Otherwise the card would show nothing while the draft held a job
    /// that Run would submit. A dimension that already has a selection is left
    /// alone: a hand-built selection is never replaced by what the draft says.
    public func adoptDraftSelection() async {
        guard !isRunning, let current = draft.draft,
              let pipeline = draft.selectedPipeline, Self.supports(pipeline) else { return }
        var clear: [String: String?] = [:]
        var seed: DraftPatch.Seed = .keep
        if drivers.isEmpty, Self.supportsDrivers(pipeline),
           let driverID = current.filledSlots[Self.driverRole] {
            drivers = [driverID]
            clear[Self.driverRole] = .some(nil)
        }
        // After the driver, so `matches(for:)` reads the shared slots without it.
        if outfits.isEmpty, let outfitID = current.filledSlots[Self.outfitRole] {
            outfits = [CrossOutfit(outfitID: outfitID,
                                   seedID: current.tryonSeed ?? matches(for: outfitID).first?.id)]
            clear[Self.outfitRole] = .some(nil)
            seed = .clear
        }
        guard !clear.isEmpty else { return }
        selectionChanged()
        await draft.apply(DraftPatch(slots: clear, seed: seed))
    }

    /// Clear draft empties the draft on the server, but this selection lives
    /// only here, so it is emptied beside it. Also called when a pipeline
    /// without a character + outfit pair is selected: its cards cannot show
    /// the selection, and hidden outfits would still count as jobs.
    public func reset() {
        guard !isRunning else { return }
        outfits = []
        drivers = []
        failure = nil
        selectionChanged()
    }

    public func matches(for outfitID: String) -> [TryonLibraryEntry] {
        library.matches(slots: sharedSlots.merging([Self.outfitRole: outfitID]) { $1 })
    }

    /// After the shared slots change, earlier matches name other materials.
    /// A manually chosen seed is re-picked too, deliberately: it was saved from
    /// the *old* character/outfit pair, so keeping it would seed a job with an
    /// image that does not match its own materials.
    public func refreshSeeds() {
        guard !isRunning else { return }
        repickSeeds()
    }

    /// The re-pick without the `isRunning` guard, for `run()`: a shared slot
    /// edited by another surface (Telegram bot, phone API, another tab) lands
    /// server-side while a build is in flight, and the guard above is what
    /// stops the observers from correcting it then. Without this, `run()` sends
    /// the seed it picked against the pre-refresh pair, and Phase A copies that
    /// image over the stage output without ever calling the provider
    /// (`runner.py:670-685`) — a wrong video with nothing on screen to say so.
    /// When the fresh slots match nothing the seed becomes nil and the step
    /// clears it, so Phase A makes a new try-on: one provider call instead of a
    /// silently wrong one.
    private func repickSeeds() {
        outfits = outfits.map { CrossOutfit(outfitID: $0.outfitID, seedID: matches(for: $0.outfitID).first?.id) }
        progress = nil
    }

    /// Steps not yet in the basket as exactly this job, outfit-major. The
    /// comparison covers the driver too, so a Continue skips every pair
    /// already basketed and no other.
    public func pending(in current: Draft) -> [CrossStep] {
        let crossed = crossedRoles
        let shared = current.filledSlots.filter { !crossed.contains($0.key) }
        let driverIDs: [String?] = drivers.isEmpty ? [nil] : drivers.map { $0 }
        return outfits.flatMap { outfit in
            driverIDs.map { CrossStep(outfit: outfit, driverID: $0) }
        }.filter { step in
            var slots = shared.merging([Self.outfitRole: step.outfit.outfitID]) { $1 }
            if let driver = step.driverID { slots[Self.driverRole] = driver }
            return !current.batch.contains { entry in
                entry.pipeline == current.pipeline && entry.provider == current.provider
                    && entry.filledSlots == slots && entry.tryonSeed == step.outfit.seedID
            }
        }
    }

    public func run() async {
        guard canRun else { return }
        // Set before the refresh await, so a second run() cannot slip in while
        // the re-read is in flight and both plan from the same stale draft.
        isRunning = true
        failure = nil
        lastAdded = nil
        defer { isRunning = false }
        // Spec §4: Continue re-reads the draft and re-plans. Planning from the
        // cached copy alone would skip an outfit another surface dropped from
        // the basket since our last read — a silent under-add reported as
        // success. Offline needs no handling here: `refresh()` keeps the last
        // good draft and the first PATCH below then fails loudly through
        // `stop(at:)`, with the transport's or the server's own message.
        let seedKeyBefore = seedKey
        await draft.refresh()
        guard let current = draft.draft else { return }
        // Re-pick after the re-read and before planning, and only when the
        // re-read changed `seedKey` (the slots a match reads; a driver the
        // re-read moved cannot change one). The order is the point: the seeds
        // then come from the server's current slots, and `pending(in:)` compares
        // each outfit against the basket carrying its *new* seed, so an outfit
        // whose seed changed is not skipped as already basketed. The condition
        // matters too — an unconditional re-pick would replace the seed chosen
        // in an outfit row's "Saved image" picker with the newest automatic
        // match on *every* build, not only after a concurrent edit. Not before
        // the `guard`, either: `matches(for:)` reads the store's draft, so on
        // the nil-draft bail-out it would match nothing and wipe every seed for
        // a run that never happened. A provider or pipeline change needs no
        // re-pick — `matches(slots:)` ignores both, and the server refuses a
        // seed beside a non-local provider loudly (`drafts.py:435-439`).
        if seedKey != seedKeyBefore { repickSeeds() }
        let steps = pending(in: current)
        progress = Progress(done: jobCount - steps.count, total: jobCount)
        for step in steps {
            // The seed is named on every step, never left as `.keep`:
            // add-to-batch copies the edited job seed included, so the next
            // outfit would inherit the previous one's saved try-on. Every driver
            // of one outfit names the same seed, which is what lets the server
            // share that outfit's try-on across them.
            let seed: DraftPatch.Seed = step.outfit.seedID.map { .set($0) } ?? .clear
            var slots: [String: String?] = [Self.outfitRole: step.outfit.outfitID]
            if let driver = step.driverID { slots[Self.driverRole] = driver }
            guard await draft.apply(DraftPatch(slots: slots, seed: seed)) else {
                return stop(at: step)
            }
            if !(await draft.addToBatch()) {
                // `draft.error` is the store's, not ours: this reads the refusal
                // addToBatch just recorded, and staying nil afterwards depends on
                // the refresh below installing the server's draft through
                // accept(), which clears `error` (`DraftStore.swift:70`, `:222`).
                guard case .server(status: 422, code: "duplicate", message: _) = draft.error else {
                    return stop(at: step)
                }
                await draft.refresh()
            }
            progress?.done += 1
        }
        // add-to-batch leaves the edited job a copy of the last step; were
        // that basket entry dropped later, the copy would count as a job again.
        // The driver is cleared only when drivers were multi-selected: otherwise
        // it is the user's shared slot, not something this build wrote.
        // `.keep` is what leaves the seed alone — this PATCH carries no key.
        var clear: [String: String?] = [Self.outfitRole: nil]
        if !drivers.isEmpty { clear[Self.driverRole] = .some(nil) }
        if !(await draft.apply(DraftPatch(slots: clear))) {
            failure = "All jobs were added, but the "
                + (drivers.isEmpty ? "outfit slot" : "outfit and driver slots")
                + " could not be cleared: "
                + (draft.message ?? draft.error?.userMessage ?? "unknown error")
        }
        // `steps`, not `jobCount`: on success every planned step ran, and a
        // failure returns early through `stop(at:)` without reaching this line.
        // `jobCount` would report the whole selection after a Continue that
        // only added the pairs not already basketed.
        lastAdded = steps.count
        outfits = []
        drivers = []
        progress = nil
    }

    private func stop(at step: CrossStep) {
        let reason = draft.message ?? draft.error?.userMessage ?? "unknown error"
        let name = step.driverID.map { "\(step.outfit.outfitID) × \($0)" } ?? step.outfit.outfitID
        failure = "Stopped at \(progress?.done ?? 0)/\(progress?.total ?? jobCount) — \(name): \(reason)"
    }
}
