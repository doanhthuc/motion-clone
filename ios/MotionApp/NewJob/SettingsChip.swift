import MotionKit
import SwiftUI

/// The pipeline and provider as one toolbar chip, where the Single | Batch
/// switch was (2026-09-26 spec §1). Until then they were a Settings section
/// below the materials, and a growing basket pushed it under the action bar.
/// The label is "Pipeline" and the value is the pipeline's name: the UI smokes
/// find the pipeline control by that pair.
@MainActor
struct SettingsChip: View {
    let pipeline: Pipeline
    let pipelines: [Pipeline]
    let selectedProvider: String
    let disabled: Bool
    let onPipelineSelected: (String) async -> Void
    let onProviderSelected: (String) async -> Void
    @State private var open = false

    private var provider: PipelineProvider? {
        pipeline.providers.first { $0.id == selectedProvider } ?? pipeline.providers.first
    }

    var body: some View {
        Button { open = true } label: {
            // Two short lines, capped at 170 pt: centered in the bar, the chip
            // may only be as wide as the bar minus twice its wider side.
            HStack(spacing: 4) {
                VStack(spacing: 0) {
                    Text(PipelineText.name(pipeline.id))
                        .font(.caption.weight(.semibold))
                    if let provider {
                        Text(ProviderText(label: provider.label).name)
                            .font(.caption2).foregroundStyle(Theme.secondary)
                    }
                }
                .lineLimit(1)
                Image(systemName: "chevron.down").font(.caption2.weight(.semibold))
                    .foregroundStyle(Theme.secondary)
            }
            .frame(maxWidth: 170)
        }
        .disabled(disabled)
        .accessibilityLabel("Pipeline")
        .accessibilityValue(PipelineText.name(pipeline.id))
        .sheet(isPresented: $open) {
            SettingsSheet(pipeline: pipeline, pipelines: pipelines, selectedProvider: selectedProvider,
                          onPipelineSelected: onPipelineSelected, onProviderSelected: onProviderSelected,
                          onDone: { open = false })
        }
    }
}

/// Provider first, then the pipelines as rows of stage glyphs. A pick no
/// longer closes the sheet: until 2026-09-27 each tap dismissed it, so setting
/// a pipeline and its provider took two trips, and at the medium detent the
/// provider section sat below the fold.
@MainActor
private struct SettingsSheet: View {
    let pipeline: Pipeline
    let pipelines: [Pipeline]
    let selectedProvider: String
    let onPipelineSelected: (String) async -> Void
    let onProviderSelected: (String) async -> Void
    let onDone: () -> Void
    // The draft answers a pick with a PATCH; these show the pick until it lands.
    @State private var pendingPipeline: String?
    @State private var pendingProvider: String?

    /// Every provider any pipeline offers, so the segments stay put (dimmed)
    /// on a pipeline without a try-on stage instead of the rows below jumping.
    private var allProviders: [PipelineProvider] {
        var seen = Set<String>()
        return (pipeline.providers + pipelines.flatMap(\.providers)).filter { seen.insert($0.id).inserted }
    }

    var body: some View {
        NavigationStack {
            List {
                if !allProviders.isEmpty {
                    Section {
                        ProviderSegments(providers: allProviders,
                                         current: pendingProvider ?? selectedProvider,
                                         enabled: !pipeline.providers.isEmpty) { id in
                            pendingProvider = id
                            await onProviderSelected(id)
                            pendingProvider = nil
                        }
                        .listRowInsets(EdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8))
                    } header: {
                        Text("Provider")
                    } footer: {
                        Text(providerFooter)
                    }
                }
                Section {
                    PipelineChoiceList(current: pendingPipeline ?? pipeline.id, pipelines: pipelines) { id in
                        pendingPipeline = id
                        await onPipelineSelected(id)
                        pendingPipeline = nil
                    }
                } header: {
                    Text("Pipeline")
                }
            }
            .animation(.snappy, value: pendingPipeline ?? pipeline.id)
            .animation(.snappy, value: pendingProvider ?? selectedProvider)
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done", action: onDone) }
            }
        }
        // Tall enough for three providers and six pipelines at default type
        // size on an iPhone 18 Pro (measured 2026-09-27); larger text scrolls.
        .presentationDetents([.fraction(0.8), .large])
    }

    private var providerFooter: String {
        guard !pipeline.providers.isEmpty else { return "This pipeline has no try-on stage." }
        let current = allProviders.first { $0.id == (pendingProvider ?? selectedProvider) }
        return current.flatMap { ProviderText(label: $0.label).caveat } ?? ""
    }
}
