import MotionKit
import SwiftUI

/// "See all" for one category: the whole role as a three-across grid, so a
/// long list of drivers is browsed here instead of pushing the other
/// categories off the Materials screen. Adding from here files the new
/// material under this category — no question afterwards.
@MainActor
struct MaterialCategoryView: View {
    let group: MaterialGroup
    let store: MaterialsStore
    let uploads: MaterialUploadQueue
    @State private var previewing: MotionKit.Material?
    @State private var deleteCandidate: MotionKit.Material?
    @State private var adding = false

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 10, alignment: .top), count: 3)

    private var busy: Bool { uploads.isRunning || store.isUploading || store.isImportingLink }

    var body: some View {
        let items = group.items(in: store.materials)
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if !uploads.items.isEmpty {
                    UploadQueueCard(queue: uploads)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }
                if items.isEmpty {
                    ContentUnavailableView {
                        Label("Nothing in \(group.title)", systemImage: "square.stack.3d.up.slash")
                    } actions: {
                        Button("Add to \(group.title)") { adding = true }
                            .buttonStyle(.borderedProminent)
                            .foregroundStyle(Theme.onAccent)
                            .disabled(busy)
                    }
                    .padding(.top, 60)
                } else {
                    LazyVGrid(columns: columns, spacing: 16) {
                        ForEach(items) { material in
                            MaterialTile(material: material, store: store,
                                         onOpen: { previewing = material },
                                         onDelete: { deleteCandidate = material })
                        }
                    }
                }
            }
            .padding(16)
            .animation(.snappy, value: uploads.items.isEmpty)
        }
        .background(Theme.bg)
        .navigationTitle(group.title)
        .navigationSubtitle("\(items.count)")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Add to \(group.title)", systemImage: "plus") { adding = true }
                    .disabled(busy)
                    .accessibilityIdentifier("category.add")
            }
        }
        .refreshable { await store.refresh() }
        .navigationDestination(item: $previewing) { material in
            MaterialPreview(material: material, materials: store)
        }
        .modifier(MaterialDeleteDialog(candidate: $deleteCandidate, store: store))
        .modifier(MaterialAdder(isPresented: $adding, group: group, store: store, queue: uploads))
    }
}
