import MotionKit
import SwiftUI

/// What a picker sheet is open for. `chained` is decided when the sheet opens:
/// only a pick started on a fresh draft walks on to the next card
/// (2026-09-26 spec §3). Editing one card of a filled draft closes as before.
struct PickTarget: Identifiable, Equatable {
    let card: NewJobState.Card
    let chained: Bool
    var id: String { "\(card)" }
}

/// One sheet for every card's picker. In a chain it swaps its picker in place
/// for the next empty required card, so a first job is picked in one pass
/// without closing and reopening the sheet. It closes when no required card is
/// empty, or on Done or a swipe down.
@MainActor
struct PickerChainSheet: View {
    let start: PickTarget
    let pipeline: Pipeline
    let store: DraftStore
    let composer: BatchComposer
    let materials: MaterialsStore
    let onClose: () -> Void
    @State private var card: NewJobState.Card?

    private var current: NewJobState.Card { card ?? start.card }

    private var state: NewJobState? {
        store.draft.map { NewJobState(pipeline: pipeline, draft: $0,
                                      outfits: composer.outfits.count, drivers: composer.drivers.count) }
    }

    var body: some View {
        Group {
            switch current {
            case .single(let role):
                MaterialPicker(role: role, kind: pipeline.roles[role] ?? .unknown,
                               selectedID: store.draft?.slots[role]?.materialID, materials: materials,
                               onSelect: { id in Task { await store.assign(role: role, materialID: id); if start.chained { advance() } } },
                               onAdvance: start.chained ? {} : nil)
                    .interactiveDismissDisabled(store.isBusy)
            case .outfits:
                MaterialMultiPicker(
                    title: "Choose outfits", kind: pipeline.roles[BatchComposer.outfitRole] ?? .image,
                    role: .outfit, materials: materials, identifierPrefix: "outfit.pick",
                    disabled: composer.isRunning, note: composer.capReason,
                    isChosen: { id in composer.outfits.contains { $0.outfitID == id } },
                    toggle: { composer.toggle(outfitID: $0) },
                    onNext: start.chained ? { advance() } : nil)
            case .drivers:
                MaterialMultiPicker(
                    title: "Choose drivers", kind: pipeline.roles[BatchComposer.driverRole] ?? .video,
                    role: .driver, materials: materials, identifierPrefix: "driver.pick",
                    disabled: composer.isRunning, note: composer.capReason,
                    isChosen: { composer.drivers.contains($0) },
                    toggle: { composer.toggle(driverID: $0) },
                    onNext: start.chained ? { advance() } : nil)
            }
        }
        .id(current)
        .transition(.push(from: .trailing))
    }

    /// Runs after the pick has landed (single cards await `assign`), so
    /// `next(after:)` reads the card just filled as filled.
    private func advance() {
        guard let next = state?.next(after: current) else { return onClose() }
        withAnimation(.snappy) { card = next }
    }
}
