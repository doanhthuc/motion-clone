import MotionKit
import SwiftUI

/// One chosen material of a multi-select card; `seeded` draws the saved
/// try-on badge on an outfit.
struct SlotCardItem: Identifiable, Equatable {
    let id: String
    let seeded: Bool
}

/// One input of the job, picture first (2026-09-26 spec §2). A single-select
/// card shows its draft slot. A multi-select card (Outfit, Driver on a
/// try-on pipeline) pages horizontally through its picks with page dots, and
/// shows "×N" in the corner. Tap opens the picker, long press opens the menu
/// for what is on screen, and ✕ clears it. The accessibility label and
/// value are `SlotText`'s, which the UI smokes read.
@MainActor
struct SlotCard: View {
    let role: String
    let required: Bool
    let kind: PipelineRoleKind
    let slot: DraftSlot?
    let items: [SlotCardItem]?
    let materials: MaterialsStore
    let disabled: Bool
    let identifier: String?
    let size: CGSize
    let onTap: () -> Void
    let menu: (SlotCardItem?) -> AnyView
    /// The ✕ on a filled card: empties a single card, removes the pick on
    /// screen from a multi-select one.
    let onClear: (SlotCardItem?) -> Void
    @State private var page: String?

    private var text: SlotText { SlotText(role: role, required: required, kind: kind, slot: slot) }
    private var picks: [SlotCardItem] { items ?? [] }
    private var isMulti: Bool { items != nil }
    private var filled: Bool { isMulti ? !picks.isEmpty : text.assigned }

    private var warning: String? {
        guard !isMulti, let warning = slot?.warning, !warning.isEmpty else { return nil }
        return warning
    }

    private var accessibilityValue: String {
        guard isMulti else { return warning.map { "\(text.state). \($0)" } ?? text.state }
        if picks.isEmpty { return required ? "Missing required" : "Empty optional" }
        return "\(picks.count) selected"
    }

    /// A slot warning takes the subtitle: the glyph alone said something was
    /// wrong but not what, and the old list's warning lines are gone.
    private var subtitle: String {
        if let warning { return warning }
        if isMulti {
            return picks.isEmpty ? (required ? "Required · pick many" : "Optional") : "\(picks.count) selected"
        }
        return text.assigned ? text.value : (required ? "Required" : "Optional")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            picture
                .frame(width: size.width, height: size.height)
                .clipShape(.rect(cornerRadius: Theme.Radius.medium))
                .contentShape(.rect)
                .onTapGesture { if !disabled { onTap() } }
                .contextMenu {
                    if filled, !disabled { menu(currentItem) }
                } preview: {
                    if let material = currentMaterial { MaterialPeek(material: material, materials: materials) }
                }
                .overlay(alignment: .bottomTrailing) {
                    if picks.count > 1 {
                        Text("×\(picks.count)")
                            .font(.caption.weight(.bold).monospacedDigit())
                            .foregroundStyle(Theme.onAccent)
                            .padding(.horizontal, 7).padding(.vertical, 3)
                            .background(Theme.accent, in: .capsule)
                            .padding(8)
                    }
                }
                // Back on the card since 2026-09-26, asked for as the quick way
                // to empty it. The glyph the tiles had before was under 44 pt;
                // this one draws 24 pt inside a 44 pt target.
                .overlay(alignment: .topTrailing) {
                    if filled, !disabled {
                        Button { onClear(currentItem) } label: {
                            Image(systemName: "xmark")
                                .font(.caption.weight(.bold)).foregroundStyle(.white)
                                .frame(width: 24, height: 24)
                                .background(.black.opacity(0.55), in: .circle)
                                .frame(width: 44, height: 44)
                                .contentShape(.rect)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .overlay(alignment: .topLeading) {
                    if let warning = slot?.warning, !warning.isEmpty, !isMulti {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .font(.footnote).foregroundStyle(Theme.warning)
                            .padding(6).background(.black.opacity(0.5), in: .circle).padding(6)
                    }
                }
            VStack(alignment: .leading, spacing: 1) {
                Text(text.title)
                    .font(.subheadline.weight(.semibold)).foregroundStyle(Theme.label)
                    .lineLimit(1).minimumScaleFactor(0.8)
                Text(subtitle)
                    .font(.caption).foregroundStyle(warning == nil ? Theme.secondary : Theme.warning)
                    .lineLimit(1).truncationMode(.middle)
            }
            .frame(height: SlotCardGrid<EmptyView>.captionHeight - 6, alignment: .top)
        }
        .opacity(disabled ? 0.6 : 1)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text.title)
        .accessibilityValue(accessibilityValue)
        .accessibilityHint(isMulti ? "Choose one or more materials" : "Choose a material")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { if !disabled { onTap() } }
        // The long-press menu and the paging live on the picture, which the
        // ignored children hide from VoiceOver, so both are offered here too.
        .accessibilityActions {
            if filled, !disabled {
                Button(isMulti ? "Remove shown" : "Clear") { onClear(currentItem) }
                menu(currentItem)
            }
        }
        .accessibilityAdjustableAction { direction in
            guard picks.count > 1 else { return }
            let index = picks.firstIndex { $0.id == currentItem?.id } ?? 0
            let next = direction == .increment ? min(index + 1, picks.count - 1) : max(index - 1, 0)
            page = picks[next].id
        }
        .accessibilityIdentifier(identifier ?? role)
    }

    private var currentItem: SlotCardItem? {
        isMulti ? (picks.first { $0.id == page } ?? picks.first) : nil
    }

    private var currentMaterial: MotionKit.Material? {
        let id = isMulti ? currentItem?.id : slot?.materialID
        return id.flatMap { id in materials.materials.first { $0.id == id } }
    }

    @ViewBuilder private var picture: some View {
        if isMulti, !picks.isEmpty {
            TabView(selection: $page) {
                ForEach(picks) { item in
                    CardThumbnail(materialID: item.id, kind: kind, materials: materials)
                        .overlay(alignment: .topLeading) {
                            if item.seeded {
                                Image(systemName: "photo.badge.checkmark")
                                    .font(.caption).foregroundStyle(Theme.onAccent)
                                    .padding(5).background(Theme.accent, in: .circle).padding(6)
                                    .accessibilityLabel("Saved try-on")
                            }
                        }
                        .tag(Optional(item.id))
                }
            }
            .tabViewStyle(.page(indexDisplayMode: picks.count > 1 ? .always : .never))
            // A removed pick must not stay the page: the TabView would show no
            // tag while the menu acted on another. Its neighbor takes over.
            .onChange(of: picks.map(\.id)) { old, ids in
                guard let page, !ids.contains(page) else { return }
                let index = old.firstIndex(of: page) ?? 0
                self.page = ids.isEmpty ? nil : ids[min(index, ids.count - 1)]
            }
        } else if !isMulti, text.assigned, let id = slot?.materialID {
            CardThumbnail(materialID: id, kind: kind, materials: materials)
        } else {
            EmptyCardFace(kind: kind, multi: isMulti)
        }
    }
}

/// A material's thumbnail filling the card, fetched per card because a
/// `@ViewBuilder` cannot hold the `@State`.
@MainActor
private struct CardThumbnail: View {
    let materialID: String
    let kind: PipelineRoleKind
    let materials: MaterialsStore
    @State private var data: Data?

    private var material: MotionKit.Material? { materials.materials.first { $0.id == materialID } }

    var body: some View {
        ZStack {
            Theme.surfaceRaised
            if let data, let image = UIImage(data: data) {
                Image(uiImage: image).resizable().scaledToFill()
            } else {
                Image(systemName: kind == .video ? "film" : "photo").font(.title3).foregroundStyle(Theme.tertiary)
            }
        }
        .overlay(alignment: .bottomLeading) {
            if material?.kind == .video {
                Image(systemName: "video.fill").font(.caption2).foregroundStyle(.white)
                    .shadow(color: .black.opacity(0.6), radius: 3).padding(8)
            }
        }
        .task(id: materialID) {
            guard let material else { data = nil; return }
            data = await materials.thumbnail(for: material)
        }
    }
}

/// The dashed "tap to fill" face, the same one the tiles had before
/// 2026-09-26, so "tap to fill" reads as it did.
private struct EmptyCardFace: View {
    let kind: PipelineRoleKind
    let multi: Bool

    var body: some View {
        RoundedRectangle(cornerRadius: Theme.Radius.medium)
            .strokeBorder(Theme.accent.opacity(0.6), style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
            .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
            .overlay {
                VStack(spacing: 6) {
                    Image(systemName: multi ? "plus.square.on.square" : "plus")
                        .font(.title2.weight(.medium)).foregroundStyle(Theme.accent)
                    Image(systemName: kind == .video ? "film" : kind == .image ? "photo" : "questionmark.square.dashed")
                        .font(.footnote).foregroundStyle(Theme.tertiary)
                }
            }
    }
}
