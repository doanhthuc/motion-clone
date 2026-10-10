import SwiftUI

/// Photos-style multi-select, shared by Materials, Saved try-ons and Runs:
/// Select in the navigation bar, a check on each tile, and a bottom bar with
/// the count, Select All and Delete. Deleting one at a time through a
/// long-press menu and a confirm each was the only way before (2026-10-10).
struct Selection {
    var active = false
    var ids: Set<String> = []

    func contains(_ id: String) -> Bool { ids.contains(id) }

    mutating func toggle(_ id: String) {
        if ids.contains(id) { ids.remove(id) } else { ids.insert(id) }
    }

    mutating func end() {
        active = false
        ids = []
    }

    /// After a bulk delete: what the server refused stays selected so it can
    /// be retried; nothing left ends the mode.
    mutating func keep(_ failed: Set<String>) {
        ids = failed
        if failed.isEmpty { active = false }
    }
}

extension View {
    /// The Select/Cancel button, the bottom bar in place of the tab bar, and
    /// pruning of ids that left the list (refreshed away, deleted elsewhere).
    /// `selectable` is every id a tap can select — Select All picks those.
    func selectionMode(_ selection: Binding<Selection>, selectable: [String], busy: Bool,
                       onDelete: @escaping () -> Void) -> some View {
        modifier(SelectionMode(selection: selection, selectable: selectable, busy: busy, onDelete: onDelete))
    }
}

private struct SelectionMode: ViewModifier {
    @Binding var selection: Selection
    let selectable: [String]
    let busy: Bool
    let onDelete: () -> Void

    func body(content: Content) -> some View {
        content
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(selection.active ? "Cancel" : "Select") {
                        withAnimation(.snappy) {
                            if selection.active { selection.end() } else { selection.active = true }
                        }
                    }
                    .disabled(busy || (!selection.active && selectable.isEmpty))
                    .accessibilityIdentifier("selection.toggle")
                }
            }
            .toolbar(selection.active ? .hidden : .automatic, for: .tabBar)
            .safeAreaInset(edge: .bottom) {
                if selection.active {
                    SelectionBar(count: selection.ids.count, total: selectable.count, busy: busy,
                                 onToggleAll: {
                                     selection.ids = selection.ids.count == selectable.count ? [] : Set(selectable)
                                 },
                                 onDelete: onDelete)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            }
            .onChange(of: selectable) { _, ids in selection.ids.formIntersection(ids) }
    }
}

private struct SelectionBar: View {
    let count: Int
    let total: Int
    let busy: Bool
    let onToggleAll: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack {
            Button(count == total && total > 0 ? "Deselect All" : "Select All", action: onToggleAll)
                .disabled(total == 0 || busy)
                .frame(maxWidth: .infinity, alignment: .leading)
            Text(count == 0 ? "Select items" : "\(count) selected")
                .font(.subheadline.weight(.semibold).monospacedDigit())
                .foregroundStyle(count == 0 ? Theme.secondary : Theme.label)
            Group {
                if busy {
                    ProgressView()
                } else {
                    Button("Delete", systemImage: "trash", action: onDelete)
                        .labelStyle(.iconOnly)
                        .font(.title3)
                        .tint(Theme.danger)
                        .disabled(count == 0)
                        .accessibilityLabel(count == 0 ? "Delete" : "Delete \(count)")
                        .accessibilityIdentifier("selection.delete")
                }
            }
            .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .frame(minHeight: 44)
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .background(.bar)
    }
}

/// The check in a tile's corner while selecting: an empty ring, or a filled
/// accent check once picked.
struct SelectionCheck: View {
    let selected: Bool

    var body: some View {
        Image(systemName: selected ? "checkmark.circle.fill" : "circle")
            .font(.title2)
            .symbolRenderingMode(.palette)
            .foregroundStyle(selected ? Theme.onAccent : .white, selected ? Theme.accent : .clear)
            .background(Circle().fill(.black.opacity(selected ? 0 : 0.25)))
            .shadow(color: .black.opacity(0.4), radius: 2)
            .padding(6)
            .accessibilityHidden(true)
    }
}
