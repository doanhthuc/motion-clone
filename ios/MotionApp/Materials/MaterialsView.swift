import MotionKit
import SwiftUI

struct MaterialsView: View {
    let store: MaterialsStore
    @State private var adding = false
    @State private var deleteCandidate: MotionKit.Material?
    @State private var previewing: MotionKit.Material?
    /// The Add button shrinks to a circle once the grid scrolls, so it covers
    /// less of what is being browsed; back at the top it says what it does.
    @State private var addCollapsed = false

    private let columns = [
        GridItem(.flexible(), spacing: 12, alignment: .top),
        GridItem(.flexible(), spacing: 12, alignment: .top),
    ]

    /// Videos first — drivers are what a batch runs out of soonest — then
    /// images, then anything the server lists that is neither.
    private var groups: [(title: String, items: [MotionKit.Material])] {
        let all = store.materials
        return [("Videos", all.filter { $0.kind == .video }),
                ("Images", all.filter { $0.kind == .image }),
                ("Other", all.filter { $0.kind != .video && $0.kind != .image })]
            .filter { !$0.items.isEmpty }
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16, pinnedViews: [.sectionHeaders]) {
                if store.isStale { StaleTag(lastSuccess: store.lastSuccess) }
                if let message = store.errorMessage {
                    messageBanner(message)
                }
                if let progress = store.uploadProgress, store.isUploading {
                    uploadCard(progress)
                }
                if store.loaded && store.materials.isEmpty {
                    EmptyNote(title: "No materials yet", systemImage: "photo.on.rectangle",
                              message: "Add a photo, a video or a TikTok link with the button below.")
                        .padding(.top, 40)
                } else {
                    ForEach(groups, id: \.title) { group in
                        Section {
                            LazyVGrid(columns: columns, spacing: 20) {
                                ForEach(group.items) { material in tile(material) }
                            }
                        } header: {
                            sectionHeader(group.title, count: group.items.count)
                        }
                    }
                }
            }
            .padding(.horizontal, 16)
            // Room for the floating Add button, so the last row is never under it.
            .padding(.bottom, 88)
        }
        .onScrollGeometryChange(for: Bool.self) { geometry in
            geometry.contentOffset.y + geometry.contentInsets.top > 24
        } action: { _, scrolled in
            withAnimation(.snappy) { addCollapsed = scrolled }
        }
        .overlay(alignment: .bottom) { addButton }
        .background(Theme.bg)
        .refreshable { await store.refresh() }
        .task { await store.refresh() }
        .navigationDestination(item: $previewing) { material in
            MaterialPreview(material: material, materials: store)
        }
        .sheet(isPresented: $adding) {
            AddMaterialSheet(store: store)
        }
        .confirmationDialog(
            "Delete this material?",
            isPresented: Binding(
                get: { deleteCandidate != nil },
                set: { if !$0 { deleteCandidate = nil } }),
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) {
                guard let material = deleteCandidate else { return }
                deleteCandidate = nil
                Task { await store.delete(material) }
            }
            Button("Cancel", role: .cancel) { deleteCandidate = nil }
        } message: {
            Text(deleteCandidate?.name ?? "")
        }
    }

    private func tile(_ material: MotionKit.Material) -> some View {
        Button { previewing = material } label: {
            MaterialCell(store: store, material: material)
        }
        .buttonStyle(.plain)
        .accessibilityHint(material.kind == .video ? "Plays the video" : "Shows the full image")
        .contextMenu {
            if material.canDelete {
                Button("Delete", systemImage: "trash", role: .destructive) {
                    deleteCandidate = material
                }
            }
        }
    }

    /// Pinned while its grid scrolls under it, so the kind stays readable.
    private func sectionHeader(_ title: String, count: Int) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).font(.title3.weight(.semibold))
            Text("\(count)").font(.subheadline.monospacedDigit()).foregroundStyle(Theme.secondary)
            Spacer()
        }
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.bg)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    /// Bottom centre, above the tab bar: the one place a thumb reaches on a
    /// Pro Max held in one hand. The navigation bar's + it replaces was the
    /// farthest point on the screen.
    private var addButton: some View {
        Button { adding = true } label: {
            HStack(spacing: 8) {
                Image(systemName: "plus").font(.title3.weight(.semibold))
                if !addCollapsed {
                    Text("Add material").font(.headline)
                        .transition(.opacity.combined(with: .scale(scale: 0.8, anchor: .leading)))
                }
            }
            // The glass style adds its own padding: 36 pt inside comes out at
            // ~56 pt, a circle when collapsed and a pill when not.
            .padding(.horizontal, addCollapsed ? 0 : 16)
            .frame(minWidth: 36, minHeight: 36)
        }
        .buttonStyle(.glassProminent)
        .buttonBorderShape(addCollapsed ? .circle : .capsule)
        .controlSize(.large)
        .foregroundStyle(Theme.onAccent)
        .disabled(store.isUploading || store.isImportingLink)
        .accessibilityLabel("Add material")
        .padding(.bottom, 12)
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

private struct MaterialCell: View {
    let store: MaterialsStore
    let material: MotionKit.Material
    @State private var thumbnail: Data?

    var body: some View {
        MaterialCard(
            material: material,
            warning: store.warning(for: material.id),
            thumbnail: thumbnail)
            .task(id: material.id) { thumbnail = await store.thumbnail(for: material) }
    }
}
