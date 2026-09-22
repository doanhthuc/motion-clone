import SwiftUI
import MotionKit

struct MaterialCard: View {
    let material: MotionKit.Material
    let warning: String?
    let thumbnail: Data?

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            thumbnailView
                .frame(maxWidth: .infinity)
                .aspectRatio(4 / 5, contentMode: .fit)
                .clipShape(.rect(cornerRadius: 11))

            Text(material.name)
                .font(Theme.sans(14, .semibold))
                .foregroundStyle(Theme.ink1)
                .lineLimit(2)

            HStack(spacing: 6) {
                Text(material.kind.rawValue.uppercased())
                Text("·")
                Text(ByteCountFormatter.string(fromByteCount: material.bytes, countStyle: .file))
            }
            .font(Theme.mono(9))
            .foregroundStyle(Theme.ink3)

            HStack {
                Text(material.owner == "app" ? "THIS APP" : material.owner)
                Spacer(minLength: 4)
                Text(Format.ago(Date.now.timeIntervalSince1970 - material.updatedAt) + " ago")
            }
            .font(Theme.mono(9))
            .foregroundStyle(Theme.ink2)

            if let warning, !warning.isEmpty {
                Label(warning, systemImage: "exclamationmark.triangle.fill")
                    .font(Theme.sans(10, .medium))
                    .foregroundStyle(Theme.amber)
                    .lineLimit(2)
            }
        }
        .padding(10)
        .card()
    }

    @ViewBuilder private var thumbnailView: some View {
        if let thumbnail, let image = UIImage(data: thumbnail) {
            Image(uiImage: image).resizable().scaledToFill()
        } else {
            ZStack {
                Theme.surface2
                Image(systemName: material.kind == .video ? "film" : "photo")
                    .font(.system(size: 28))
                    .foregroundStyle(Theme.ink3)
            }
        }
    }
}
