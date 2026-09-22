import MotionKit
import SwiftUI

@MainActor
struct PipelinePicker: View {
    let pipeline: Pipeline
    let pipelines: [Pipeline]
    let selectedProvider: String
    let disabled: Bool
    let onPipelineSelected: (String) async -> Void
    let onProviderSelected: (String) async -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(text: "Pipeline")

            Menu {
                ForEach(pipelines) { candidate in
                    Button(displayName(candidate.id)) {
                        Task { await onPipelineSelected(candidate.id) }
                    }
                }
            } label: {
                HStack(spacing: 10) {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(displayName(pipeline.id))
                            .font(Theme.sans(16, .semibold))
                            .foregroundStyle(Theme.ink1)
                        if !pipeline.stages.isEmpty {
                            Text(pipeline.stages.joined(separator: " · "))
                                .font(Theme.mono(10))
                                .foregroundStyle(Theme.ink3)
                                .lineLimit(1)
                        }
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(Theme.ink2)
                }
                .padding(13)
                .card()
            }
            .disabled(disabled)
            .accessibilityLabel("Pipeline")
            .accessibilityValue(displayName(pipeline.id))

            if !pipeline.providers.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Provider")
                        .font(Theme.mono(10))
                        .foregroundStyle(Theme.ink3)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(pipeline.providers) { provider in
                                Button(provider.label) {
                                    Task { await onProviderSelected(provider.id) }
                                }
                                .font(Theme.sans(13, .semibold))
                                .foregroundStyle(provider.id == selectedProvider ? Theme.limeInk : Theme.ink2)
                                .padding(.horizontal, 12)
                                .padding(.vertical, 9)
                                .background(provider.id == selectedProvider ? Theme.lime : Theme.surface2, in: .capsule)
                                .overlay(Capsule().strokeBorder(provider.id == selectedProvider ? Theme.lime : Theme.line2))
                                .disabled(disabled)
                                .accessibilityValue(provider.id == selectedProvider ? "Selected" : "Not selected")
                            }
                        }
                    }
                }
            }
        }
    }

    private func displayName(_ id: String) -> String {
        id.replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
    }
}
