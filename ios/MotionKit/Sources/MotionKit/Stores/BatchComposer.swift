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

/// Cross build (Phase 6 spec §4): the edited job's own slots are shared, and
/// each outfit becomes one basket job through `PATCH {slots.outfit,
/// tryon_seed}` + `add-to-batch` — no new route (API spec §5.10). Free: no
/// spend gate. Runs sequentially and stops at the first failure; running
/// again re-plans from the server's draft, so nothing is added twice.
@MainActor @Observable
public final class BatchComposer {
    public struct Progress: Equatable, Sendable {
        public var done: Int
        public let total: Int
        public init(done: Int, total: Int) { self.done = done; self.total = total }
    }

    /// A mistap guard, not a server limit: Phase A runs one try-on per outfit
    /// and the rent estimate grows linearly with N.
    public static let maxOutfits = 12
    public static let outfitRole = "outfit"

    public private(set) var outfits: [CrossOutfit] = []
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

    public static func supports(_ pipeline: Pipeline) -> Bool {
        let roles = Set(pipeline.required + pipeline.optional)
        return roles.contains("character") && roles.contains(outfitRole)
    }

    /// Every filled slot of the edited job except the outfit.
    public var sharedSlots: [String: String] {
        (draft.draft?.filledSlots ?? [:]).filter { $0.key != Self.outfitRole }
    }

    /// Required roles still empty, the outfit excluded (the list fills it).
    public var missingShared: [String] {
        (draft.draft?.missing ?? []).filter { $0 != Self.outfitRole }
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
            && outfits.count <= Self.maxOutfits
            && draft.selectedPipeline.map(Self.supports) == true
    }

    public func toggle(outfitID: String) {
        guard !isRunning else { return }
        if let index = outfits.firstIndex(where: { $0.outfitID == outfitID }) {
            outfits.remove(at: index)
        } else if outfits.count < Self.maxOutfits {
            outfits.append(CrossOutfit(outfitID: outfitID, seedID: matches(for: outfitID).first?.id))
        }
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

    /// Outfits not yet in the basket as exactly this job.
    public func pending(in current: Draft) -> [CrossOutfit] {
        let shared = current.filledSlots.filter { $0.key != Self.outfitRole }
        return outfits.filter { outfit in
            let slots = shared.merging([Self.outfitRole: outfit.outfitID]) { $1 }
            return !current.batch.contains { entry in
                entry.pipeline == current.pipeline && entry.provider == current.provider
                    && entry.filledSlots == slots && entry.tryonSeed == outfit.seedID
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
        let sharedBefore = sharedSlots
        await draft.refresh()
        guard let current = draft.draft else { return }
        // Re-pick after the re-read and before planning, and only when the
        // re-read changed the shared slots. The order is the point: the seeds
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
        if sharedSlots != sharedBefore { repickSeeds() }
        let steps = pending(in: current)
        progress = Progress(done: outfits.count - steps.count, total: outfits.count)
        for outfit in steps {
            // The seed is named on every outfit, never left as `.keep`:
            // add-to-batch copies the edited job seed included, so the next
            // outfit would inherit the previous one's saved try-on.
            let seed: DraftPatch.Seed = outfit.seedID.map { .set($0) } ?? .clear
            guard await draft.apply(DraftPatch(slots: [Self.outfitRole: outfit.outfitID], seed: seed)) else {
                return stop(at: outfit)
            }
            if !(await draft.addToBatch()) {
                // `draft.error` is the store's, not ours: this reads the refusal
                // addToBatch just recorded, and staying nil afterwards depends on
                // the refresh below installing the server's draft through
                // accept(), which clears `error` (`DraftStore.swift:70`, `:222`).
                guard case .server(status: 422, code: "duplicate", message: _) = draft.error else {
                    return stop(at: outfit)
                }
                await draft.refresh()
            }
            progress?.done += 1
        }
        // add-to-batch leaves the edited job a copy of the last outfit; were
        // that basket entry dropped later, the copy would count as a job again.
        // `.keep` is what leaves the seed alone — this PATCH carries no key.
        if !(await draft.apply(DraftPatch(slots: [Self.outfitRole: nil]))) {
            failure = "All outfits were added, but the outfit slot could not be cleared: "
                + (draft.message ?? draft.error?.userMessage ?? "unknown error")
        }
        // `steps`, not `outfits`: on success every planned step ran, and a
        // failure returns early through `stop(at:)` without reaching this line.
        // `outfits.count` would report the whole selection after a Continue
        // that only added the outfits not already basketed.
        lastAdded = steps.count
        outfits = []
        progress = nil
    }

    private func stop(at outfit: CrossOutfit) {
        let reason = draft.message ?? draft.error?.userMessage ?? "unknown error"
        failure = "Stopped at \(progress?.done ?? 0)/\(progress?.total ?? outfits.count) — \(outfit.outfitID): \(reason)"
    }
}
