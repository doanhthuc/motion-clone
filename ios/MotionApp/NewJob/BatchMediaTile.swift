import MotionKit
import SwiftUI

/// One chosen outfit or driver in a batch strip: the picture, a quiet name,
/// and an optional badge. Fetches its own thumbnail, the way `SlotMaterialRow`
/// does, because a `@ViewBuilder` function cannot hold `@State`.
@MainActor
struct BatchMediaTile: View {
    let material: MotionKit.Material?
    let fallbackName: String
    let materials: MaterialsStore
    var badge: String?
    @State private var thumbnail: Data?

    static let width: CGFloat = 84

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Color.clear
                .frame(width: Self.width, height: Self.width * 5 / 4)
                .overlay { picture }
                .clipShape(.rect(cornerRadius: Theme.Radius.small))
                .overlay(alignment: .bottomLeading) {
                    if material?.kind == .video {
                        Image(systemName: "video.fill")
                            .font(.caption2).foregroundStyle(.white)
                            .shadow(color: .black.opacity(0.6), radius: 3)
                            .padding(6)
                    }
                }
                .overlay(alignment: .topLeading) {
                    if let badge {
                        Label(badge, systemImage: "photo.badge.checkmark")
                            .labelStyle(.iconOnly)
                            .font(.caption)
                            .foregroundStyle(Theme.onAccent)
                            .padding(5)
                            .background(Theme.accent, in: .circle)
                            .padding(5)
                    }
                }
            Text(material?.name ?? fallbackName)
                .font(.caption)
                .foregroundStyle(Theme.secondary)
                .lineLimit(1).truncationMode(.middle)
                .frame(width: Self.width, alignment: .leading)
        }
        .task(id: material?.id) {
            guard let material else {
                thumbnail = nil
                return
            }
            thumbnail = await materials.thumbnail(for: material)
        }
    }

    @ViewBuilder private var picture: some View {
        if let thumbnail, let image = UIImage(data: thumbnail) {
            Image(uiImage: image).resizable().scaledToFill()
        } else {
            ZStack {
                Theme.surfaceRaised
                Image(systemName: material?.kind == .video ? "film" : "photo")
                    .foregroundStyle(Theme.tertiary)
            }
        }
    }
}

/// The leading tile of a strip: always first, so it never scrolls out of
/// reach horizontally however many items sit after it.
struct BatchAddTile: View {
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 4) {
                RoundedRectangle(cornerRadius: Theme.Radius.small)
                    .strokeBorder(Theme.accent.opacity(0.6), style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
                    .frame(width: BatchMediaTile.width, height: BatchMediaTile.width * 5 / 4)
                    .overlay {
                        Image(systemName: "plus")
                            .font(.title2.weight(.medium))
                            .foregroundStyle(Theme.accent)
                    }
                Text(title)
                    .font(.caption)
                    .foregroundStyle(Theme.accent)
                    .lineLimit(1)
                    .frame(width: BatchMediaTile.width, alignment: .leading)
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
    }
}
