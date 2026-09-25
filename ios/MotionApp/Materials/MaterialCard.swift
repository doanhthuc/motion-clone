import SwiftUI
import MotionKit

/// A grid tile: the media fills its own rounded frame, two quiet lines below.
/// No surface behind it — the photo is the surface.
struct MaterialCard: View {
    let material: MotionKit.Material
    let warning: String?
    let thumbnail: Data?
    var selected = false
    /// Three-across grids: the name only, one line — the kind/size/age line
    /// does not fit a third of a phone and the badge already says "video".
    var compact = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Color.clear
                .aspectRatio(4 / 5, contentMode: .fit)
                .overlay { thumbnailView }
                .clipShape(.rect(cornerRadius: Theme.Radius.small))
                .overlay {
                    if selected {
                        RoundedRectangle(cornerRadius: Theme.Radius.small)
                            .strokeBorder(Theme.accent, lineWidth: 3)
                    }
                }
                .overlay(alignment: .bottomLeading) {
                    if material.kind == .video {
                        Image(systemName: "video.fill")
                            .font(.caption).foregroundStyle(.white)
                            .shadow(color: .black.opacity(0.6), radius: 3)
                            .padding(8)
                    }
                }

            VStack(alignment: .leading, spacing: 2) {
                Text(material.name)
                    .font(compact ? .caption : .subheadline)
                    .foregroundStyle(Theme.label)
                    .lineLimit(1).truncationMode(.middle)
                if !compact {
                    Text(detail)
                        .font(.footnote.monospacedDigit())
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1)
                }
            }

            if let warning, !warning.isEmpty {
                Label(warning, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(Theme.warning)
                    .lineLimit(2)
            }
        }
    }

    private var detail: String {
        let kind = material.kind.rawValue.capitalized
        let size = ByteCountFormatter.string(fromByteCount: material.bytes, countStyle: .file)
        let age = Format.ago(Date.now.timeIntervalSince1970 - material.updatedAt)
        return "\(kind) · \(size) · \(age)"
    }

    @ViewBuilder private var thumbnailView: some View {
        if let thumbnail, let image = UIImage(data: thumbnail) {
            Image(uiImage: image).resizable().scaledToFill()
        } else {
            ZStack {
                Theme.surface
                Image(systemName: material.kind == .video ? "film" : "photo")
                    .font(.title2)
                    .foregroundStyle(Theme.tertiary)
            }
        }
    }
}
