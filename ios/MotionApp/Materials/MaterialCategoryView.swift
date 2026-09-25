import MotionKit
import SwiftUI

/// "See all" for one category: the whole role as a three-across grid, so a
/// long list of drivers is browsed here instead of pushing the other
/// categories off the Materials screen.
@MainActor
struct MaterialCategoryView: View {
    let group: MaterialGroup
    let store: MaterialsStore
    @State private var previewing: MotionKit.Material?
    @State private var deleteCandidate: MotionKit.Material?

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 10, alignment: .top), count: 3)

    var body: some View {
        let items = group.items(in: store.materials)
        ScrollView {
            if items.isEmpty {
                ContentUnavailableView("Nothing in \(group.title)", systemImage: "square.stack.3d.up.slash")
                    .padding(.top, 80)
            } else {
                LazyVGrid(columns: columns, spacing: 16) {
                    ForEach(items) { material in
                        MaterialTile(material: material, store: store,
                                     onOpen: { previewing = material },
                                     onDelete: { deleteCandidate = material })
                    }
                }
                .padding(16)
            }
        }
        .background(Theme.bg)
        .navigationTitle(group.title)
        .navigationSubtitle("\(items.count)")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await store.refresh() }
        .navigationDestination(item: $previewing) { material in
            MaterialPreview(material: material, materials: store)
        }
        .modifier(MaterialDeleteDialog(candidate: $deleteCandidate, store: store))
    }
}
