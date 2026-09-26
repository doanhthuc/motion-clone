import MotionKit
import SwiftUI

/// Full-screen image with Edit this / Use as material / Save to try-on library
/// / Save & Share, plus the prompt and model that made it.
struct StudioImageViewer: View {
    let studio: StudioStore
    let generation: StudioGeneration
    let imageID: String
    @Environment(\.dismiss) private var dismiss
    @State private var image: UIImage?
    @State private var exporter = MediaExporter()
    @State private var note: String?

    var body: some View {
        NavigationStack {
            VStack(spacing: 16) {
                Group {
                    if let image { Image(uiImage: image).resizable().scaledToFit() } else { ProgressView() }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .overlay(alignment: .bottom) {
                    MediaStatusView(exporter: exporter).padding(.horizontal, 16).padding(.bottom, 12)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(generation.prompt).font(.callout).lineLimit(4)
                    Text(studio.catalog?.models.first { $0.key == generation.model }?.label ?? generation.model)
                        .font(.caption).foregroundStyle(Theme.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let note { Text(note).font(.footnote).foregroundStyle(Theme.accent) }
                HStack(spacing: 12) {
                    Button("Edit this", systemImage: "wand.and.stars") {
                        if let pid = studio.project?.id { studio.attach(StudioRef(kind: .studio, id: "\(pid)/\(imageID)")) }
                        dismiss()
                    }
                    .buttonStyle(.borderedProminent)
                    Menu {
                        Button("Use as material", systemImage: "photo.badge.plus") {
                            Task { note = await studio.promote(imageID: imageID, to: .material) }
                        }
                        Button("Save to try-on library", systemImage: "tshirt") {
                            Task { note = await studio.promote(imageID: imageID, to: .tryon) }
                        }
                        Button("Save to Photos", systemImage: "square.and.arrow.down") {
                            Task { await exporter.saveToPhotos(isVideo: false) { try await studio.download(imageID: imageID) } }
                        }
                        Button("Share", systemImage: "square.and.arrow.up") {
                            Task { await exporter.share { try await studio.download(imageID: imageID) } }
                        }
                    } label: { Label("More", systemImage: "ellipsis.circle") }
                    .buttonStyle(.bordered)
                }
            }
            .padding()
            .background(Theme.bg)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("Done") { dismiss() } } }
            // Mirrors how MediaActionsModifier (Components/MediaActions.swift) presents a share:
            // the exporter downloads first, then this sheet hands the file to the system share sheet.
            .sheet(item: Binding(get: { exporter.sharing }, set: { exporter.sharing = $0 })) { shared in
                ActivityView(items: [shared.url])
                    .presentationDetents([.medium, .large])
                    .onDisappear { try? FileManager.default.removeItem(at: shared.url.deletingLastPathComponent()) }
            }
            .task {
                guard let pid = studio.project?.id,
                      let data = await studio.image(projectID: pid, imageID: imageID) else { return }
                image = UIImage(data: data)
            }
        }
    }
}
