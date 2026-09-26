import MotionKit
import SwiftUI

// Pipeline and provider display helpers and choice lists. The choices open
// from `SettingsChip` since 2026-09-26; before that they were a Settings
// section under the materials.

/// How a pipeline reads to a person, shared by the picker and the batch
/// entries so the same job never shows under two different names.
enum PipelineText {
    /// `tryon-camera-motion-enhance` → "Try-on Camera Motion Enhance".
    static func name(_ id: String) -> String {
        id.replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "_", with: " ")
            .capitalized
            .replacingOccurrences(of: "Tryon", with: "Try-on")
    }

    static func stages(_ pipeline: Pipeline) -> String {
        pipeline.stages.map { Format.stageName($0).replacingOccurrences(of: "-", with: " ") }
            .joined(separator: " → ")
            .replacingOccurrences(of: "Try on", with: "Try-on")
            .replacingOccurrences(of: "tryon", with: "try-on")
    }
}

/// Every pipeline on its own row with the stages it runs, so the choice is
/// made knowing what each one does; the menu this replaced showed bare names.
/// Rows only: `SettingsChip`'s sheet owns the list and the section footer.
struct PipelineChoiceList: View {
    let current: String
    let pipelines: [Pipeline]
    let name: (String) -> String
    let stages: (Pipeline) -> String
    let onSelected: (String) async -> Void
    let onDone: () -> Void

    var body: some View {
        ForEach(pipelines) { candidate in
            let selected = candidate.id == current
            Button {
                if !selected { Task { await onSelected(candidate.id) } }
                onDone()
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
    }
}

/// Every provider with its full caveat, so the choice is made knowing the
/// trade-off; the chip shows only the provider's name.
struct ProviderChoiceList: View {
    let providers: [PipelineProvider]
    let current: String
    let onSelected: (String) async -> Void
    let onDone: () -> Void

    var body: some View {
        ForEach(providers) { provider in
            let selected = provider.id == current
            let parts = ProviderText(label: provider.label)
            Button {
                if !selected { Task { await onSelected(provider.id) } }
                onDone()
            } label: {
                HStack(spacing: 12) {
                    ProviderMark(id: provider.id)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(parts.name).font(.body).foregroundStyle(Theme.label)
                        if let caveat = parts.caveat {
                            Text(caveat).font(.subheadline).foregroundStyle(Theme.secondary)
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
            .accessibilityLabel(provider.label)
            .accessibilityAddTraits(selected ? .isSelected : [])
            .accessibilityValue(selected ? "Selected" : "Not selected")
        }
    }
}

/// A server provider label split for display: "☁️ Gemini API — runs here, no
/// pod wait; may crop…" → name "Gemini API", caveat "Runs here, no pod wait;
/// may crop…". The label leads with an
/// emoji, which the rows drop; VoiceOver in the choice list still gets it verbatim.
struct ProviderText {
    let name: String
    let caveat: String?

    init(label: String) {
        let plain = String(label.drop { !$0.isLetter && !$0.isNumber })
        let parts = plain.components(separatedBy: " — ")
        name = parts[0]
        caveat = parts.count > 1 ? parts.dropFirst().joined(separator: " — ").capitalizedFirst : nil
    }
}

/// The provider's mark in one color, like an SF Symbol: the brand shape is
/// what's recognizable at 22pt, and brand colors would be the only saturated
/// things in the list besides the accent checkmark that means "selected".
struct ProviderMark: View {
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
