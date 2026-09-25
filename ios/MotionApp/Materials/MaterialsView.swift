import MotionKit
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct MaterialsView: View {
    let store: MaterialsStore
    @State private var photoItem: PhotosPickerItem?
    @State private var showFiles = false
    @State private var deleteCandidate: MotionKit.Material?
    @State private var importError: String?

    private let columns = [
        GridItem(.flexible(), spacing: 12, alignment: .top),
        GridItem(.flexible(), spacing: 12, alignment: .top),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if store.isStale { StaleTag(lastSuccess: store.lastSuccess) }
                if let message = importError ?? store.errorMessage {
                    messageBanner(message)
                }
                if let progress = store.uploadProgress, store.isUploading {
                    uploadCard(progress)
                }
                if store.loaded && store.materials.isEmpty {
                    EmptyNote(title: "No materials yet", systemImage: "photo.on.rectangle",
                              message: "Add an image or video with the + button.")
                        .padding(.top, 40)
                } else {
                    LazyVGrid(columns: columns, spacing: 20) {
                        ForEach(store.materials) { material in
                            MaterialCell(store: store, material: material)
                                .contextMenu {
                                    if material.canDelete {
                                        Button("Delete", systemImage: "trash", role: .destructive) {
                                            deleteCandidate = material
                                        }
                                    }
                                }
                        }
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .background(Theme.bg)
        .refreshable { await store.refresh() }
        .toolbar { ToolbarItem(placement: .topBarTrailing) { addMenu } }
        .fileImporter(
            isPresented: $showFiles,
            allowedContentTypes: [.image, .movie],
            allowsMultipleSelection: false,
            onCompletion: importFile)
        .onChange(of: photoItem) { _, item in importPhoto(item) }
        .task { await store.refresh() }
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

    private var addMenu: some View {
        Menu {
            PhotosPicker(selection: $photoItem, matching: .any(of: [.images, .videos])) {
                Label("Photo Library", systemImage: "photo.on.rectangle")
            }
            Button("Files", systemImage: "folder") { showFiles = true }
        } label: {
            Image(systemName: "plus")
        }
        .disabled(store.isUploading)
        .accessibilityLabel("Add material")
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
                Button("Dismiss") {
                    importError = nil
                    store.clearError()
                }
                .font(.subheadline.weight(.semibold))
            }
        }
        .heroSurface()
    }

    private func importPhoto(_ item: PhotosPickerItem?) {
        guard let item else { return }
        Task {
            defer { photoItem = nil }
            do {
                guard let imported = try await item.loadTransferable(type: ImportedMedia.self) else {
                    importError = "The selected item could not be imported."
                    return
                }
                await upload(imported)
            } catch {
                importError = error.localizedDescription
            }
        }
    }

    private func importFile(_ result: Result<[URL], any Error>) {
        do {
            guard let source = try result.get().first else { return }
            Task {
                do {
                    let imported = try await ImportStaging.stageAsync(
                        source, securityScoped: true)
                    await upload(imported)
                } catch {
                    importError = error.localizedDescription
                }
            }
        } catch {
            importError = error.localizedDescription
        }
    }

    private func upload(_ imported: ImportedMedia) async {
        defer { imported.removeStagedCopy() }
        importError = nil
        await store.startUpload(fileURL: imported.url, fileName: imported.fileName)
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
