import MotionKit
import SwiftUI

/// The basket above the action bar (2026-09-26 spec §1): collapsed it is one
/// line, "Batch · N" and the first jobs' outfits; tapped or dragged up it lists
/// the jobs over the cards. It is not a system sheet, which would cover the tab
/// bar and be dismissed by every picker, since iOS shows one sheet at a time.
/// The list inside is the stage's only scroll.
///
/// Three heights (user, 2026-09-27): the handle alone, most of the stage, and
/// all of it. Dragging the handle up steps one level at a time; dragging it
/// down, tapping it or tapping the dimmed cards above closes the drawer from
/// either open level (user, 2026-09-27).
@MainActor
struct BasketDrawer: View {
    enum Level { case collapsed, half, tall }

    let batch: [DraftBatchEntry]
    let pipeline: (DraftBatchEntry) -> Pipeline?
    let materials: MaterialsStore
    let locked: Bool
    @Binding var level: Level
    let onOpen: (DraftBatchEntry) -> Void
    let onDrop: (DraftBatchEntry) async -> Void
    let clearAll: AnyView
    @State private var dropCandidate: DraftBatchEntry?
    @GestureState private var drag: CGFloat = 0

    /// The handle's height; `NewJobView` reserves it under the cards.
    static let collapsedHeight: CGFloat = 48

    private var expanded: Bool { level != .collapsed }

    var body: some View {
        VStack(spacing: 0) {
            handle
            if expanded { list.transition(.move(edge: .bottom).combined(with: .opacity)) }
        }
        .background(.regularMaterial, in: .rect(cornerRadius: 20))
        // The ScrollView draws into the bottom safe area; without the clip the
        // next jobs showed through under the action and tab bars.
        .clipShape(.rect(cornerRadius: 20))
        .offset(y: max(drag, expanded ? 0 : -40) * (expanded ? 1 : 0.3))
        .animation(.snappy, value: level)
    }

    private var handle: some View {
        Button { withAnimation(.snappy) { level = expanded ? .collapsed : .half } } label: {
            HStack(spacing: 10) {
                Image(systemName: expanded ? "chevron.down" : "chevron.up")
                    .font(.footnote.weight(.semibold)).foregroundStyle(Theme.secondary)
                // Its own `Text`: the smokes read "Batch · N" by exact string.
                Text("Batch · \(batch.count)").font(.subheadline.weight(.semibold))
                if !expanded {
                    HStack(spacing: -8) {
                        ForEach(batch.prefix(4), id: \.digest) { entry in
                            BatchEntryMaterialTile(role: "outfit",
                                                   materialID: (entry.slots["outfit"] ?? nil) ?? entry.slots.values.compactMap { $0 }.first,
                                                   kind: .image, materials: materials, width: 22,
                                                   showsTitle: false)
                        }
                    }
                    .accessibilityHidden(true)
                }
                Spacer(minLength: 0)
                if expanded { clearAll }
            }
            .padding(.horizontal, 16)
            .frame(height: Self.collapsedHeight)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // On the handle only: over the whole drawer it took the list's
        // vertical scroll (review, 2026-09-26).
        .simultaneousGesture(
            DragGesture(minimumDistance: 12)
                .updating($drag) { value, state, _ in state = value.translation.height }
                .onEnded { value in
                    withAnimation(.snappy) {
                        if value.translation.height < -40 { level = level == .collapsed ? .half : .tall }
                        if value.translation.height > 40 { level = .collapsed }
                    }
                })

        .accessibilityHint(expanded ? "Collapse the batch" : "Show the batch")
        .accessibilityIdentifier("newjob.basket")
    }

    /// A ScrollView, not a List: a List row carries one context menu, so every
    /// tile's long-press peeked at the row's first tile — pressing Outfit
    /// showed the Character (user, 2026-09-27). The row's trash button is the
    /// Drop now that there is no swipe action.
    private var list: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(Array(batch.enumerated()), id: \.element.digest) { offset, entry in
                    if offset > 0 { Divider().padding(.leading, 16) }
                    BatchEntryRow(index: offset + 1, entry: entry, pipeline: pipeline(entry),
                                  materials: materials, dropDisabled: locked,
                                  onOpen: { onOpen(entry) }, onDrop: { dropCandidate = entry },
                                  confirmingDrop: Binding(get: { dropCandidate?.digest == entry.digest },
                                                          set: { if !$0 { dropCandidate = nil } }),
                                  onConfirmDrop: {
                                      dropCandidate = nil
                                      Task { await onDrop(entry) }
                                  })
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                }
            }
        }
    }
}
