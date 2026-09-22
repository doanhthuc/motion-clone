import MotionKit
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

struct MaterialsView: View {
    let store: MaterialsStore
    @Environment(\.scenePhase) private var scenePhase
    @State private var photoItem: PhotosPickerItem?
    @State private var showFiles = false
    @State private var deleteCandidate: MotionKit.Material?
    @State private var importError: String?

    private let columns = [
        GridItem(.flexible(), spacing: 12),
        GridItem(.flexible(), spacing: 12),
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                if let message = importError ?? store.errorMessage {
                    messageBanner(message)
                }
                if let progress = store.uploadProgress, store.isUploading {
                    uploadCard(progress)
                }
                if store.loaded && store.materials.isEmpty {
                    Text("No materials yet. Add an image or video to begin.")
                        .font(Theme.sans(14)).foregroundStyle(Theme.ink2)
                        .frame(maxWidth: .infinity).padding(.vertical, 60)
                } else {
                    LazyVGrid(columns: columns, spacing: 12) {
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
            .padding(.horizontal, 20)
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
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await store.resumePendingUpload() } }
        }
        .task {
            await store.refresh()
            await store.resumePendingUpload()
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

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("Material").font(Theme.sans(33, .bold)).foregroundStyle(Theme.ink)
            Spacer()
            if store.isStale { StaleTag(lastSuccess: store.lastSuccess) }
            Text("\(store.materials.count) items").font(Theme.mono(11)).foregroundStyle(Theme.ink2)
        }
    }

    private var addMenu: some View {
        Menu {
            PhotosPicker(selection: $photoItem, matching: .any(of: [.images, .videos])) {
                Label("Photo Library", systemImage: "photo.on.rectangle")
            }
            Button("Files", systemImage: "folder") { showFiles = true }
        } label: {
            Image(systemName: "plus").font(.system(size: 16, weight: .bold))
        }
        .disabled(store.isUploading)
        .accessibilityLabel("Add material")
    }

    private func uploadCard(_ progress: UploadProgress) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(progress.fileName).font(Theme.sans(14, .semibold)).foregroundStyle(Theme.ink1)
                    Text(phaseLabel(progress.phase)).font(Theme.mono(10)).foregroundStyle(Theme.lime)
                }
                Spacer()
                Text("\(ByteCountFormatter.string(fromByteCount: progress.bytesSent, countStyle: .file)) / \(ByteCountFormatter.string(fromByteCount: progress.totalBytes, countStyle: .file))")
                    .font(Theme.mono(9)).foregroundStyle(Theme.ink2)
            }
            ProgressView(value: Double(progress.bytesSent), total: Double(max(progress.totalBytes, 1)))
                .tint(progress.phase == .processing ? Theme.amber : Theme.lime)
        }
        .padding(13).card(border: Theme.limeLine)
    }

    private func messageBanner(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle").foregroundStyle(Theme.red)
            Text(message).font(Theme.sans(13)).foregroundStyle(Theme.ink1)
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
                    Text("Upload").font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
                }
            } else {
                Button("Dismiss") {
                    importError = nil
                    store.clearError()
                }
                .font(Theme.sans(12, .semibold)).foregroundStyle(Theme.lime)
            }
        }
        .padding(12)
        .background(Theme.redDim, in: .rect(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.redLine))
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
            let imported = try ImportStaging.stage(source, securityScoped: true)
            Task { await upload(imported) }
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
        case .preparing: "PREPARING"
        case .transferring: "UPLOADING"
        case .processing: "PROCESSING"
        case .complete: "COMPLETE"
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
