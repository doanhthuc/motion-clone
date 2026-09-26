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
            NavigationStack {
                List {
                    Section {
                        PipelineChoiceList(current: pipeline.id, pipelines: pipelines,
                                           name: PipelineText.name, stages: PipelineText.stages,
                                           onSelected: onPipelineSelected, onDone: { open = false })
                    } header: {
                        Text("Pipeline")
                    } footer: {
                        Text("Stages run left to right; each one's output feeds the next.")
                    }
                    if !pipeline.providers.isEmpty {
                        Section {
                            ProviderChoiceList(providers: pipeline.providers, current: selectedProvider,
                                               onSelected: onProviderSelected, onDone: { open = false })
                        } header: {
                            Text("Provider")
                        } footer: {
                            Text("Hosted providers make the try-on here, before any pod is rented.")
                        }
                    }
                }
                .navigationTitle("Settings")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) { Button("Done") { open = false } }
                }
            }
            .presentationDetents([.medium, .large])
        }
    }
}
