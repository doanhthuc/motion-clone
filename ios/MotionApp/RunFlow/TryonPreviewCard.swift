import MotionKit
import SwiftUI

struct TryonPreviewCard: View {
    let flow: RunFlow
    let preview: TryonPreview
    @State private var image: UIImage?
    @State private var showVersions = false
    @State private var showRegenerate = false
    @State private var guidance: Set<Guidance> = []

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(preview.run).font(Theme.mono(12, .semibold)).foregroundStyle(Theme.ink1)
                Spacer()
                StageDot(status: preview.status)
            }
            if let image {
                Image(uiImage: image).resizable().scaledToFit()
                    .clipShape(.rect(cornerRadius: 12))
            } else if preview.hasImage {
                ProgressView().frame(maxWidth: .infinity, minHeight: 200)
            } else {
                Text(preview.status == .error ? "Try-on failed for this job." : "No image yet.")
                    .font(Theme.sans(13)).foregroundStyle(Theme.ink2)
            }
            if preview.hasImage {
                HStack(spacing: 10) {
                    Button(flow.isKept(preview.index) ? "Saved" : "Keep") {
                        Task { await flow.keep(index: preview.index) }
                    }
                    .buttonStyle(SecondaryButtonStyle())
                    .disabled(flow.isKept(preview.index))
                    Button("Versions") {
                        showVersions.toggle()
                        if showVersions { Task { await flow.loadVersions(index: preview.index) } }
                    }
                    .buttonStyle(SecondaryButtonStyle())
                }
            }
            Button("Regenerate…") { showRegenerate = true }
                .buttonStyle(SecondaryButtonStyle())
                .disabled(flow.isSpending)
            if showVersions { versionStrip }
        }
        .padding(14).card()
        .task(id: "\(preview.index)#\(flow.imageGeneration)#\(preview.hasImage)") {
            guard preview.hasImage else { image = nil; return }
            image = await flow.image(index: preview.index).flatMap(UIImage.init(data:))
        }
        .sheet(isPresented: $showRegenerate) { regenerateSheet }
    }

    @ViewBuilder private var versionStrip: some View {
        let items = flow.versions[preview.index] ?? []
        if items.isEmpty {
            Text("No earlier versions.").font(Theme.mono(11)).foregroundStyle(Theme.ink3)
        } else {
            ScrollView(.horizontal) {
                HStack(spacing: 8) {
                    ForEach(Array(items.enumerated()), id: \.offset) { n, data in
                        VStack(spacing: 4) {
                            if let ui = UIImage(data: data) {
                                Image(uiImage: ui).resizable().scaledToFill()
                                    .frame(width: 72, height: 96).clipShape(.rect(cornerRadius: 8))
                            }
                            Text("v\(n + 1)").font(Theme.mono(10)).foregroundStyle(Theme.ink2)
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
                        .font(Theme.sans(12)).foregroundStyle(Theme.ink2)
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
                    .disabled(flow.isSpending)
                }
            }
        }
        .presentationDetents([.medium])
    }
}
