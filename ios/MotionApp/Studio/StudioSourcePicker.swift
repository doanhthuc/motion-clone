import MotionKit
import PhotosUI
import SwiftUI

/// Where a reference comes from. Photos go through the normal upload and
/// become a material, so the server only ever resolves names it owns.
struct StudioSourcePicker: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let studio: StudioStore
    @State private var photo: PhotosPickerItem?
    @State private var uploading = false
    @State private var failure: String?

    var body: some View {
        NavigationStack {
            List {
                Section {
                    PhotosPicker(selection: $photo, matching: .images) {
                        Label("Photo library", systemImage: "photo.on.rectangle")
                    }
                    .disabled(uploading)
                    if uploading {
                        Label { Text("Uploading…") } icon: { ProgressView().controlSize(.small) }
                            .font(.footnote).foregroundStyle(Theme.secondary)
                    } else if let failure {
                        Text(failure).font(.footnote).foregroundStyle(Theme.danger)
                    }
                }
                if let materials = model.materials {
                    Section("Materials") {
                        grid(materials.materials.filter { $0.kind == .image }.map { StudioRef(kind: .material, id: $0.id) })
                    }
                }
                if let library = model.tryonLibrary, !library.entries.isEmpty {
                    Section("Try-on library") { grid(library.entries.map { StudioRef(kind: .tryon, id: $0.id) }) }
                }
                if let previews = runPreviews, !previews.isEmpty {
                    Section("Current try-on previews") { grid(previews) }
                }
                if let project = studio.project {
                    let refs = project.generations.flatMap { g in g.slots.compactMap(\.image) }
                        .reversed().map { StudioRef(kind: .studio, id: "\(project.id)/\($0)") }
                    if !refs.isEmpty { Section("This project") { grid(Array(refs)) } }
                }
            }
            .navigationTitle("Add reference").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            .task {
                await model.materials?.refresh()
                await model.tryonLibrary?.load()
            }
            .onChange(of: photo) { _, item in
                guard let item, let materials = model.materials else { return }
                uploading = true
                Task {
                    defer { uploading = false; photo = nil }
                    do {
                        if let material = try await MediaImport.upload(item, to: materials) {
                            studio.attach(StudioRef(kind: .material, id: material.id))
                            dismiss()
                        } else {
                            failure = materials.errorMessage ?? "Upload failed."
                        }
                    } catch { failure = error.localizedDescription }
                }
            }
        }
    }

    /// Try-on previews of the live Phase A run, if any.
    private var runPreviews: [StudioRef]? {
        guard let flow = model.runFlow, let runID = flow.runID else { return nil }
        return flow.cards.filter(\.hasImage).map { StudioRef(kind: .runTryon, id: "\(runID)/\($0.index)") }
    }

    private func grid(_ refs: [StudioRef]) -> some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 88), spacing: 6)], spacing: 6) {
            ForEach(refs) { ref in
                SourceThumb(studio: studio, ref: ref, picked: studio.attachments.contains(ref)) {
                    if studio.attachments.contains(ref) { studio.detach(ref) } else { studio.attach(ref) }
                }
            }
        }
        .listRowInsets(EdgeInsets(top: 6, leading: 6, bottom: 6, trailing: 6))
    }
}

private struct SourceThumb: View {
    let studio: StudioStore
    let ref: StudioRef
    let picked: Bool
    let toggle: () -> Void
    @State private var image: UIImage?

    var body: some View {
        Button(action: toggle) {
            ZStack(alignment: .topTrailing) {
                Rectangle().fill(Theme.surfaceRaised)
                    .overlay { if let image { Image(uiImage: image).resizable().scaledToFill() } }
                    .clipped()
                if picked {
                    Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.accent).padding(4)
                }
            }
            .aspectRatio(3 / 4, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 8))
        }
        .buttonStyle(.plain)
        .task { if let data = await studio.thumbnail(for: ref) { image = UIImage(data: data) } }
    }
}
