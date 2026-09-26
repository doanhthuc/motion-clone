import SwiftUI

/// The stage's cards, two across, sized to the space the grid is given so New
/// Job never scrolls (2026-09-26 spec §1). The old grid used a fixed 3:4 per
/// tile, and in Batch mode that plus three other sections ran past the screen.
/// A card's picture is at most 3:4. When height is short (iPhone SE, or a
/// banner showing), the picture gets shorter instead of the stage scrolling.
struct SlotCardGrid<Content: View>: View {
    let count: Int
    @ViewBuilder let content: (CGSize) -> Content

    static var spacing: CGFloat { 12 }
    /// Title and subtitle under each picture.
    static var captionHeight: CGFloat { 40 }

    var body: some View {
        GeometryReader { proxy in
            let columns = count <= 1 ? 1 : 2
            let rows = max(Int((Double(count) / Double(columns)).rounded(.up)), 1)
            let width = (proxy.size.width - Self.spacing * CGFloat(columns - 1)) / CGFloat(columns)
            let fitHeight = (proxy.size.height - Self.spacing * CGFloat(rows - 1)) / CGFloat(rows)
                - Self.captionHeight
            let picture = CGSize(width: width, height: max(min(width * 4 / 3, fitHeight), 60))
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(width), spacing: Self.spacing), count: columns),
                      spacing: Self.spacing) {
                content(picture)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}
