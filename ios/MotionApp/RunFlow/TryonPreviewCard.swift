import MotionKit
import SwiftUI

struct TryonPreviewCard: View {
    let flow: RunFlow
    let preview: TryonPreview
    @State private var image: UIImage?
    @State private var showVersions = false
    @State private var showRegenerate = false
    @State private var guidance: Set<Guidance> = []
    @State private var confirmDrop = false

    var body: some View {
        Section {
            Group {
                if let image {
                    Image(uiImage: image).resizable().scaledToFit()
                        .clipShape(.rect(cornerRadius: Theme.Radius.small))
                        .frame(maxWidth: .infinity)
                } else if preview.hasImage {
                    LoadingBlock(minHeight: 200)
                } else {
                    Text(preview.status == .error ? "Try-on failed for this job." : "No image yet.")
                        .font(.subheadline).foregroundStyle(Theme.secondary)
                }
            }
            .task(id: "\(preview.index)#\(flow.imageGeneration)#\(preview.hasImage)") {
                guard preview.hasImage else { image = nil; return }
                image = await flow.image(index: preview.index).flatMap(UIImage.init(data:))
            }
            .sheet(isPresented: $showRegenerate) { regenerateSheet }
            // On the always-present first row, not on the trigger: that button
            // lives in a conditional branch and disables itself the moment
            // `isDropping` flips.
            .confirmationDialog("Drop \(preview.run) from the batch?", isPresented: $confirmDrop, titleVisibility: .visible) {
                Button("Drop", role: .destructive) {
                    confirmDrop = false
                    Task { await flow.drop(preview) }
                }
                Button("Cancel", role: .cancel) { confirmDrop = false }
            } message: {
                Text("Free — nothing is rented yet. The draft is validated again, and Confirm then rents only what is left.")
            }
            if !(preview.shares?.isEmpty ?? true) {
                Text("Used by \((preview.shares?.count ?? 0) + 1) videos")
                    .font(.subheadline).foregroundStyle(Theme.secondary)
            }
            if preview.hasImage {
                Button(flow.isKept(preview.index) ? "Saved to library" : "Keep") {
                    Task { await flow.keep(index: preview.index) }
                }
                .disabled(flow.isKept(preview.index))
                Button(showVersions ? "Hide versions" : "Versions") {
                    showVersions.toggle()
                    if showVersions { Task { await flow.loadVersions(index: preview.index) } }
                }
                if showVersions { versionStrip }
            }
            Button("Regenerate…") { showRegenerate = true }
                .disabled(!flow.canSpend)
            // `RunFlow.canDropFromBatch(_:)` requires `!isDropping`, so without
            // the second term the control would disappear the instant a drop
            // starts and "Dropping…" would never render. It stays on screen but
            // inert until the store's trailing refreshes finish — `isDropping`
            // outlives the writes on purpose.
            if flow.canDropFromBatch(preview) || flow.isDropping {
                Button(flow.isDropping ? "Dropping…" : "Drop from batch", role: .destructive) { confirmDrop = true }
                    .disabled(!flow.canDropFromBatch(preview) || flow.batchEntry(for: preview) == nil)
                    .accessibilityIdentifier("tryon.drop.\(preview.index)")
                if flow.batchEntry(for: preview) == nil {
                    Text("Draft changed — reload").font(.footnote).foregroundStyle(Theme.warning)
                }
            }
        } header: {
            HStack(spacing: 8) {
                Text(preview.run).lineLimit(1).truncationMode(.middle)
                if flow.isSeeded(preview) {
                    Label("Saved try-on", systemImage: "photo.badge.checkmark")
                        .labelStyle(.titleAndIcon)
                        .accessibilityIdentifier("tryon.seeded.\(preview.index)")
                }
                Spacer()
                StageDot(status: preview.status).scaleEffect(0.8)
            }
        }
    }

    @ViewBuilder private var versionStrip: some View {
        let items = flow.versions[preview.index] ?? []
        if items.isEmpty {
            Text("No earlier versions.").font(.footnote).foregroundStyle(Theme.secondary)
        } else {
            ScrollView(.horizontal) {
                HStack(spacing: 8) {
                    ForEach(Array(items.enumerated()), id: \.offset) { n, data in
                        VStack(spacing: 4) {
                            if let ui = UIImage(data: data) {
                                Image(uiImage: ui).resizable().scaledToFill()
                                    .frame(width: 72, height: 96).clipShape(.rect(cornerRadius: Theme.Radius.small))
                            }
                            Text("v\(n + 1)").font(.footnote.monospacedDigit()).foregroundStyle(Theme.secondary)
                        }
                    }
                }
            }
        }
    }

    private var regenerateSheet: some View {
        NavigationStack {
            Form {
                Section("Guidance") {
                    ForEach(Guidance.allCases, id: \.self) { g in
                        Toggle(g.label, isOn: Binding(
                            get: { guidance.contains(g) },
                            set: { on in if on { guidance.insert(g) } else { guidance.remove(g) } }))
                    }
                }
                Section {
                    Text("Spends Gemini/Qwen quota again. The current image becomes an earlier version.")
                        .font(.footnote).foregroundStyle(Theme.secondary)
                }
            }
            .navigationTitle("Regenerate #\(preview.index)")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { showRegenerate = false } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Regenerate") {
                        showRegenerate = false
                        let chosen = guidance
                        Task { await flow.regenerate(index: preview.index, guidance: chosen) }
                    }
                    .disabled(!flow.canSpend)
                }
            }
        }
        .presentationDetents([.medium])
    }
}
