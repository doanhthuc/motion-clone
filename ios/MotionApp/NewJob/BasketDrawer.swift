import MotionKit
import SwiftUI

/// The basket above the action bar (2026-09-26 spec §1): collapsed it is one
/// line, "Batch · N" and the first jobs' outfits; tapped or dragged up it lists
/// the jobs over the cards. It is not a system sheet, which would cover the tab
/// bar and be dismissed by every picker, since iOS shows one sheet at a time.
/// The list inside is the stage's only scroll.
@MainActor
struct BasketDrawer: View {
    let batch: [DraftBatchEntry]
    let pipeline: (DraftBatchEntry) -> Pipeline?
    let materials: MaterialsStore
    let locked: Bool
    @Binding var expanded: Bool
    let onOpen: (DraftBatchEntry) -> Void
    let onDrop: (DraftBatchEntry) async -> Void
    let clearAll: AnyView
    @State private var dropCandidate: DraftBatchEntry?
    @GestureState private var drag: CGFloat = 0

    /// The handle's height; `NewJobView` reserves it under the cards.
    static let collapsedHeight: CGFloat = 48

    var body: some View {
        VStack(spacing: 0) {
            handle
            if expanded { list.transition(.move(edge: .bottom).combined(with: .opacity)) }
        }
        .background(.regularMaterial, in: .rect(cornerRadius: 20))
        .offset(y: max(drag, expanded ? 0 : -40) * (expanded ? 1 : 0.3))
        .animation(.snappy, value: expanded)
    }

    private var handle: some View {
        Button { withAnimation(.snappy) { expanded.toggle() } } label: {
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
        // horizontal swipe-to-Drop and its vertical scroll (review, 2026-09-26).
        .simultaneousGesture(
            DragGesture(minimumDistance: 12)
                .updating($drag) { value, state, _ in state = value.translation.height }
                .onEnded { value in
                    withAnimation(.snappy) {
                        if value.translation.height < -40 { expanded = true }
                        if value.translation.height > 40 { expanded = false }
                    }
                })

        .accessibilityHint(expanded ? "Collapse the batch" : "Show the batch")
        .accessibilityIdentifier("newjob.basket")
    }

    private var list: some View {
        List {
            ForEach(Array(batch.enumerated()), id: \.element.digest) { offset, entry in
                BatchEntryRow(index: offset + 1, entry: entry, pipeline: pipeline(entry),
                              materials: materials, dropDisabled: locked,
                              onOpen: { onOpen(entry) }, onDrop: { dropCandidate = entry })
                    .swipeActions(edge: .trailing) {
                        Button("Drop", systemImage: "trash", role: .destructive) { dropCandidate = entry }
                            .disabled(locked)
                    }
                    // On the row, so the popover points at the job it drops.
                    .confirmationDialog(
                        "Drop this batch entry?",
                        isPresented: Binding(get: { dropCandidate?.digest == entry.digest },
                                             set: { if !$0 { dropCandidate = nil } }),
                        titleVisibility: .visible
                    ) {
                        Button("Drop", role: .destructive) {
                            dropCandidate = nil
                            Task { await onDrop(entry) }
                        }
                        Button("Cancel", role: .cancel) { dropCandidate = nil }
                    } message: {
                        Text("Job \(offset + 1) · \(BatchEntryText.subtitle(entry, pipeline: pipeline(entry)))")
                    }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
    }
}
