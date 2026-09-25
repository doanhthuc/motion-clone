import MotionKit
import SwiftUI
import UniformTypeIdentifiers

/// What the Materials tab's Add button opens: Photos, a TikTok link, or Files,
/// as the same tiles the job pickers use. A sheet rather than a menu — a
/// `PhotosPicker` inside a `Menu` never presented its picker (reported on a
/// phone 2026-09-25), and a link needs a text field a menu cannot hold.
@MainActor
struct AddMaterialSheet: View {
    let store: MaterialsStore
    @Environment(\.dismiss) private var dismiss
    @State private var showFiles = false
    @State private var fileFailure: String?

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                MaterialImportBar(anyKindIn: store, onFiles: { showFiles = true }) { _ in
                    dismiss()
                }
                if let fileFailure {
                    Label(fileFailure, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote).foregroundStyle(Theme.warning)
                }
                Spacer(minLength: 0)
            }
            .padding(16)
            .background(Theme.bg)
            .navigationTitle("Add material")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
        }
        .presentationDetents([.height(320), .medium])
        .presentationDragIndicator(.visible)
        .fileImporter(isPresented: $showFiles, allowedContentTypes: [.image, .movie],
                      allowsMultipleSelection: false, onCompletion: importFile)
    }

    private func importFile(_ result: Result<[URL], any Error>) {
        fileFailure = nil
        Task {
            do {
                guard let source = try result.get().first else { return }
                let imported = try await ImportStaging.stageAsync(source, securityScoped: true)
                defer { imported.removeStagedCopy() }
                if await store.startUpload(fileURL: imported.url, fileName: imported.fileName) != nil {
                    dismiss()
                } else {
                    fileFailure = store.errorMessage
                }
            } catch {
                fileFailure = error.localizedDescription
            }
        }
    }
}
