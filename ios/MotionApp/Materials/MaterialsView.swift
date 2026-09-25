import MotionKit
import SwiftUI

struct MaterialsView: View {
    let store: MaterialsStore
    @State private var adding = false
    @State private var deleteCandidate: MotionKit.Material?
    @State private var previewing: MotionKit.Material?
    /// An image just uploaded here: ask what it is, as the bot does.
    @State private var sorting: MotionKit.Material?
    @State private var justAdded: MotionKit.Material?
    /// 0 with the grid at rest, 1 once it has scrolled `collapseDistance`:
    /// drives the Add button from its tall form to a one-line bar.
    @State private var collapse: CGFloat = 0
    private static let collapseDistance: CGFloat = 80

    /// Tile width in a category row: three and a bit across a phone, so the
    /// cut-off fourth says "swipe for more" without a label.
    private static let rowTileWidth: CGFloat = 112

    var body: some View {
        VStack(spacing: 0) {
            addButton
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
            grid
        }
        .background(Theme.bg)
        .task { await store.refresh() }
        .navigationDestination(item: $previewing) { material in
            MaterialPreview(material: material, materials: store)
        }
        // The question waits for the sheet to finish closing: a dialog cannot
        // present from a view that is still covered.
        .sheet(isPresented: $adding, onDismiss: {
            sorting = justAdded
            justAdded = nil
        }) {
            AddMaterialSheet(store: store) { material in
                if material.kind == .image { justAdded = material }
            }
        }
        .confirmationDialog(
            "What is this image?",
            isPresented: Binding(get: { sorting != nil }, set: { if !$0 { sorting = nil } }),
            titleVisibility: .visible
        ) {
            ForEach(MaterialRole.options(for: .image), id: \.self) { role in
                Button(role.title) {
                    guard let material = sorting else { return }
                    sorting = nil
                    Task { await store.setRole(role, for: material) }
                }
            }
            Button("Decide later", role: .cancel) { sorting = nil }
        } message: {
            Text(sorting?.name ?? "")
        }
        .modifier(MaterialDeleteDialog(candidate: $deleteCandidate, store: store))
        .navigationDestination(for: MaterialGroup.self) { group in
            MaterialCategoryView(group: group, store: store)
        }
    }

    private var grid: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 24) {
                if store.isStale { StaleTag(lastSuccess: store.lastSuccess) }
                if let message = store.errorMessage {
                    messageBanner(message)
                }
                if let progress = store.uploadProgress, store.isUploading {
                    uploadCard(progress)
                }
                if store.loaded && store.materials.isEmpty {
                    EmptyNote(title: "No materials yet", systemImage: "photo.on.rectangle",
                              message: "Add a photo, a video or a TikTok link with the button above.")
                        .padding(.top, 40)
                } else {
                    ForEach(MaterialGroup.all) { group in
                        let items = group.items(in: store.materials)
                        if !items.isEmpty { row(group, items) }
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .onScrollGeometryChange(for: CGFloat.self) { geometry in
            max(0, geometry.contentOffset.y + geometry.contentInsets.top)
        } action: { _, offset in
            collapse = min(1, offset / Self.collapseDistance)
        }
        .refreshable { await store.refresh() }
    }

    /// One category as a single horizontal row with a See all, so every
    /// category stays about one screen away however large the library grows —
    /// stacked full grids pushed the next category a whole grid further down.
    private func row(_ group: MaterialGroup, _ items: [MotionKit.Material]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            NavigationLink(value: group) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(group.title).font(.title3.weight(.semibold)).foregroundStyle(Theme.label)
                    Text("\(items.count)").font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
                    Spacer()
                    Text("See all").font(.subheadline)
                    Image(systemName: "chevron.right").font(.footnote.weight(.semibold))
                }
                .contentShape(.rect)
            }
            .buttonStyle(.plain)
            .foregroundStyle(Theme.accent)
            .accessibilityLabel("\(group.title), \(items.count)")
            .accessibilityHint("Shows all of them")
            .accessibilityAddTraits(.isHeader)

            ScrollView(.horizontal) {
                LazyHStack(alignment: .top, spacing: 12) {
                    ForEach(items) { material in
                        MaterialTile(material: material, store: store,
                                     onOpen: { previewing = material },
                                     onDelete: { deleteCandidate = material })
                            .frame(width: Self.rowTileWidth)
                    }
                }
                .padding(.horizontal, 16)
            }
            .scrollIndicators(.hidden)
            // Edge to edge: the row scrolls under the screen's side margins.
            .padding(.horizontal, -16)
        }
    }

    /// Part of the layout, directly under the Materials | Saved try-ons
    /// switch, full width: a big target in the upper-middle of the screen
    /// rather than the navigation bar's corner +. Tall at rest, with what it
    /// offers spelled out; as the grid scrolls it shrinks to a one-line bar
    /// and stays, so it never scrolls out of reach.
    private var addButton: some View {
        let height = 88 - 40 * collapse
        return Button { adding = true } label: {
            HStack(spacing: 12) {
                // Not a bare "plus": the tab bar's New Job is one.
                Image(systemName: "photo.badge.plus")
                    .font(.system(size: 28 - 8 * collapse, weight: .semibold))
                VStack(alignment: .leading, spacing: 2) {
                    Text("Add material").font(.headline)
                    if collapse < 0.5 {
                        Text("Photos · TikTok link · Files")
                            .font(.subheadline)
                            .opacity(1 - collapse * 2)
                    }
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right").font(.footnote.weight(.semibold))
                    .opacity(0.6)
            }
            .foregroundStyle(Theme.onAccent)
            .padding(.horizontal, 16)
            .frame(maxWidth: .infinity, minHeight: height, maxHeight: height)
            .background(Theme.accent, in: .rect(cornerRadius: Theme.Radius.medium))
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .disabled(store.isUploading || store.isImportingLink)
        .opacity(store.isUploading || store.isImportingLink ? 0.5 : 1)
        .accessibilityLabel("Add material")
        .accessibilityHint("Photos, a TikTok link or Files")
    }

    private func uploadCard(_ progress: UploadProgress) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(progress.fileName).font(.subheadline).lineLimit(1).truncationMode(.middle)
                Spacer()
                Text("\(ByteCountFormatter.string(fromByteCount: progress.bytesSent, countStyle: .file)) of \(ByteCountFormatter.string(fromByteCount: progress.totalBytes, countStyle: .file))")
                    .font(.footnote.monospacedDigit()).foregroundStyle(Theme.secondary)
            }
            ProgressView(value: Double(progress.bytesSent), total: Double(max(progress.totalBytes, 1)))
                .tint(Theme.label)
            Text(phaseLabel(progress.phase)).font(.footnote).foregroundStyle(Theme.secondary)
        }
        .heroSurface()
    }

    private func messageBanner(_ message: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(Theme.danger)
            Text(message).font(.subheadline)
            Spacer(minLength: 0)
            if store.hasPendingUpload {
                Menu {
                    Button("Retry upload", systemImage: "arrow.clockwise") {
                        Task { await store.resumePendingUpload() }
                    }
                    Button("Discard upload", systemImage: "trash", role: .destructive) {
                        Task { await store.discardPendingUpload() }
                    }
                } label: {
                    Text("Upload").font(.subheadline.weight(.semibold))
                }
            } else {
                Button("Dismiss") { store.clearError() }
                    .font(.subheadline.weight(.semibold))
            }
        }
        .heroSurface()
    }

    private func phaseLabel(_ phase: UploadPhase) -> String {
        switch phase {
        case .preparing: "Preparing…"
        case .transferring: "Uploading…"
        case .processing: "Processing on the server…"
        case .complete: "Complete"
        }
    }
}
