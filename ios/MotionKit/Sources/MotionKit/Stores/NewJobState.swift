import Foundation

/// What New Job's single stage shows and allows (2026-09-26 spec), computed
/// from the draft and the composer's selection sizes so the rules are unit
/// tested rather than spread over view bodies. On a pipeline with a character
/// + outfit pair, the Outfit card and (when the pipeline has one) the Driver
/// card are the composer's multi-selects. Every other card is one draft slot.
public struct NewJobState: Equatable, Sendable {
    public enum Card: Hashable, Sendable { case single(String), outfits, drivers }

    public let cards: [Card]
    public let required: Set<String>
    /// Required roles still empty, in the pipeline's order.
    public let missing: [String]
    public let isBatch: Bool
    /// Jobs Add would put in the basket now; 0 means Add has nothing to do.
    public let addCount: Int
    /// Continue has something to run: a pending add, or jobs the server
    /// already counts (the basket plus a complete edited job — `drafts.py`
    /// `jobs_for`).
    public let canContinue: Bool
    /// Nothing picked anywhere: the only state that chains the pickers.
    public let isFresh: Bool

    private let filled: Set<String>

    public init(pipeline: Pipeline, draft: Draft, outfits: Int, drivers: Int) {
        let roles = pipeline.required + pipeline.optional
        let batch = BatchComposer.supports(pipeline)
        let crossDrivers = batch && BatchComposer.supportsDrivers(pipeline)
        isBatch = batch
        required = Set(pipeline.required)
        cards = roles.map { role in
            if batch, role == BatchComposer.outfitRole { return .outfits }
            if crossDrivers, role == BatchComposer.driverRole { return .drivers }
            return .single(role)
        }
        let draftFilled = Set(draft.slots.filter { $0.value.materialID != nil && $0.value.exists }.keys)
        var filled = draftFilled
        if batch {
            if outfits > 0 { filled.insert(BatchComposer.outfitRole) } else { filled.remove(BatchComposer.outfitRole) }
            // An empty driver selection falls back to the draft's shared driver slot.
            if crossDrivers, drivers > 0 { filled.insert(BatchComposer.driverRole) }
        }
        self.filled = filled
        missing = pipeline.required.filter { !filled.contains($0) }
        if !missing.isEmpty {
            addCount = 0
        } else if batch {
            addCount = outfits * max(drivers, 1)
        } else {
            addCount = 1
        }
        canContinue = addCount > 0 || draft.jobs > 0
        isFresh = draftFilled.isEmpty && outfits == 0 && drivers == 0
    }

    public func role(of card: Card) -> String {
        switch card {
        case .single(let role): role
        case .outfits: BatchComposer.outfitRole
        case .drivers: BatchComposer.driverRole
        }
    }

    public func isFilled(_ card: Card) -> Bool { filled.contains(role(of: card)) }

    /// The next empty required card after `card`, in card order; nil when none
    /// is left, which is when a chained pick closes its sheet.
    public func next(after card: Card) -> Card? {
        let start = (cards.firstIndex(of: card) ?? -1) + 1
        // After `card` first, then wrap to the cards before it.
        return (Array(cards[start...]) + Array(cards[..<start])).first {
            $0 != card && required.contains(role(of: $0)) && !isFilled($0)
        }
    }
}
