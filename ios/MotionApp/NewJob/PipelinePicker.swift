import MotionKit
import SwiftUI

/// Two `List` sections: the pipeline menu, then the provider as checkmark rows.
/// Rows, not chips, because provider labels run to ~40 characters and a chip
/// strip cut the second option off at "Gemi…".
@MainActor
struct PipelinePicker: View {
    let pipeline: Pipeline
    let pipelines: [Pipeline]
    let selectedProvider: String
    let disabled: Bool
    let onPipelineSelected: (String) async -> Void
    let onProviderSelected: (String) async -> Void

    var body: some View {
        Section("Pipeline") {
            Menu {
                ForEach(pipelines) { candidate in
                    Button(displayName(candidate.id)) {
                        Task { await onPipelineSelected(candidate.id) }
                    }
                }
            } label: {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(displayName(pipeline.id))
                            .font(.body)
                            .foregroundStyle(Theme.label)
                        if !pipeline.stages.isEmpty {
                            Text(pipeline.stages.map(stageName).joined(separator: " → "))
                                .font(.subheadline)
                                .foregroundStyle(Theme.secondary)
                                .lineLimit(1)
                        }
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(Theme.tertiary)
                }
                .contentShape(.rect)
            }
            .disabled(disabled)
            .accessibilityLabel("Pipeline")
            .accessibilityValue(displayName(pipeline.id))
        }

        if !pipeline.providers.isEmpty {
            Section("Provider") {
                ForEach(pipeline.providers) { provider in
                    let selected = provider.id == selectedProvider
                    let parts = plainLabel(provider.label).components(separatedBy: " — ")
                    Button {
                        guard !selected else { return }
                        Task { await onProviderSelected(provider.id) }
                    } label: {
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(parts[0]).font(.body).foregroundStyle(Theme.label)
                                if parts.count > 1 {
                                    Text(parts.dropFirst().joined(separator: " — ").capitalizedFirst)
                                        .font(.subheadline).foregroundStyle(Theme.secondary)
                                }
                            }
                            Spacer(minLength: 0)
                            if selected {
                                Image(systemName: "checkmark").font(.body.weight(.semibold))
                                    .foregroundStyle(Theme.accent)
                            }
                        }
                        .contentShape(.rect)
                    }
                    .disabled(disabled)
                    .accessibilityLabel(provider.label)
                    .accessibilityAddTraits(selected ? .isSelected : [])
                    .accessibilityValue(selected ? "Selected" : "Not selected")
                }
            }
        }
    }

    /// The server's labels lead with an emoji ("🖥 Self-host…", "☁️ Gemini…");
    /// the row shows text only. VoiceOver still gets the label verbatim.
    private func plainLabel(_ label: String) -> String {
        String(label.drop { !$0.isLetter && !$0.isNumber })
    }

    private func stageName(_ raw: String) -> String {
        Format.stageName(raw).replacingOccurrences(of: "-", with: " ")
    }

    private func displayName(_ id: String) -> String {
        id.replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
    }
}

private extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
