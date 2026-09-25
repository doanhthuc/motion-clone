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
            NavigationLink {
                PipelineChoiceList(current: pipeline.id, pipelines: pipelines,
                                   name: displayName, stages: stageLine,
                                   onSelected: onPipelineSelected)
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(displayName(pipeline.id))
                        .font(.body)
                        .foregroundStyle(Theme.label)
                    if !pipeline.stages.isEmpty {
                        Text(stageLine(pipeline))
                            .font(.subheadline)
                            .foregroundStyle(Theme.secondary)
                            .lineLimit(1)
                    }
                }
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
                            ProviderMark(id: provider.id)
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

    private func stageLine(_ pipeline: Pipeline) -> String {
        pipeline.stages.map { Format.stageName($0).replacingOccurrences(of: "-", with: " ") }
            .joined(separator: " → ")
            .replacingOccurrences(of: "Try on", with: "Try-on")
            .replacingOccurrences(of: "tryon", with: "try-on")
    }

    /// `tryon-camera-motion-enhance` → "Try-on Camera Motion Enhance".
    private func displayName(_ id: String) -> String {
        id.replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
            .replacingOccurrences(of: "Tryon", with: "Try-on")
    }
}

/// Every pipeline on its own row with the stages it runs, so the choice is
/// made knowing what each one does; the menu this replaced showed bare names.
private struct PipelineChoiceList: View {
    let current: String
    let pipelines: [Pipeline]
    let name: (String) -> String
    let stages: (Pipeline) -> String
    let onSelected: (String) async -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            Section {
                ForEach(pipelines) { candidate in
                    let selected = candidate.id == current
                    Button {
                        if !selected { Task { await onSelected(candidate.id) } }
                        dismiss()
                    } label: {
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(name(candidate.id)).font(.body).foregroundStyle(Theme.label)
                                if !candidate.stages.isEmpty {
                                    Text(stages(candidate)).font(.subheadline).foregroundStyle(Theme.secondary)
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
                    .accessibilityLabel(name(candidate.id))
                    .accessibilityValue(stages(candidate))
                    .accessibilityAddTraits(selected ? .isSelected : [])
                }
            } footer: {
                Text("Stages run left to right; each one's output feeds the next.")
            }
        }
        .navigationTitle("Pipeline")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// The provider's mark in one color, like an SF Symbol: the brand shape is
/// what's recognizable at 22pt, and brand colors would be the only saturated
/// things in the list besides the accent checkmark that means "selected".
private struct ProviderMark: View {
    let id: String

    var body: some View {
        Group {
            switch id {
            case "gemini": Image("ProviderGemini").resizable().scaledToFit().padding(1)
            case "qwen-max": Image("ProviderQwen").resizable().scaledToFit().padding(1)
            // Self-hosted runs on the rented pod: the Pod tab's own symbol.
            case "qwen": Image(systemName: "cpu").resizable().scaledToFit()
            default: Image(systemName: "cloud").resizable().scaledToFit()
            }
        }
        .foregroundStyle(Theme.label)
        .frame(width: 22, height: 22)
        .frame(width: 28)
        .accessibilityHidden(true)
    }
}

private extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
