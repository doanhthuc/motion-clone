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
    /// Each material this sheet produced, just before it closes itself.
    let onImported: (MotionKit.Material) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var showFiles = false
    @State private var fileFailure: String?

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                MaterialImportBar(anyKindIn: store, onFiles: { showFiles = true }) { material in
                    finish(with: material)
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

    private func finish(with material: MotionKit.Material) {
        onImported(material)
        dismiss()
    }

    private func importFile(_ result: Result<[URL], any Error>) {
        fileFailure = nil
        Task {
            do {
                guard let source = try result.get().first else { return }
                let imported = try await ImportStaging.stageAsync(source, securityScoped: true)
                defer { imported.removeStagedCopy() }
                if let material = await store.startUpload(fileURL: imported.url, fileName: imported.fileName) {
                    finish(with: material)
                } else {
                    fileFailure = store.errorMessage
                }
            } catch {
                fileFailure = error.localizedDescription
            }
        }
    }
}
