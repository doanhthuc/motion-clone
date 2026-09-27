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
        pipeline.stages.map(stage).joined(separator: " → ")
    }

    /// `camera-tryon` → "Camera try-on".
    static func stage(_ id: String) -> String {
        Format.stageName(id).replacingOccurrences(of: "-", with: " ")
            .replacingOccurrences(of: "Try on", with: "Try-on")
            .replacingOccurrences(of: "tryon", with: "try-on")
    }
}

/// Every pipeline on its own row with its stages as glyphs, so pipelines that
/// differ by one stage differ visibly; the text strings this replaced all
/// ended "→ Enhance" and had to be read to be told apart. Rows only:
/// `SettingsSheet` owns the list.
struct PipelineChoiceList: View {
    let current: String
    let pipelines: [Pipeline]
    let onSelected: (String) async -> Void

    var body: some View {
        ForEach(pipelines) { candidate in
            let selected = candidate.id == current
            Button {
                if !selected { Task { await onSelected(candidate.id) } }
            } label: {
                HStack(spacing: 12) {
                    Text(PipelineText.name(candidate.id)).font(.body)
                        .foregroundStyle(Theme.label)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    StageGlyphs(stages: candidate.stages, selected: selected)
                    Image(systemName: "checkmark").font(.body.weight(.semibold))
                        .foregroundStyle(Theme.accent)
                        .opacity(selected ? 1 : 0)
                }
                .contentShape(.rect)
            }
            .listRowBackground(selected ? Theme.accent.opacity(0.14) : Theme.surface)
            .accessibilityLabel(PipelineText.name(candidate.id))
            .accessibilityValue(PipelineText.stages(candidate))
            .accessibilityAddTraits(selected ? .isSelected : [])
        }
    }
}

/// A pipeline's stages left to right, one SF Symbol each with a hairline
/// chevron between: the order is the point, since each output feeds the next.
private struct StageGlyphs: View {
    let stages: [String]
    let selected: Bool

    var body: some View {
        HStack(spacing: 3) {
            ForEach(Array(stages.enumerated()), id: \.offset) { index, stage in
                if index > 0 {
                    Image(systemName: "chevron.compact.right").font(.caption2)
                        .foregroundStyle(Theme.tertiary)
                }
                // Fitted into one box: at a shared font size the swap glyph
                // is twice as wide as the rest and the columns stop lining up.
                Image(systemName: Self.symbol(stage))
                    .resizable().scaledToFit()
                    .frame(width: 20, height: 20)
                    .foregroundStyle(selected ? Theme.accent : Theme.secondary)
            }
        }
        .accessibilityHidden(true)
    }

    static func symbol(_ stage: String) -> String {
        switch stage {
        case "tryon": "tshirt"
        case "camera-tryon": "camera.viewfinder"
        case "motion": "figure.walk"
        case "camera-motion": "video"
        case "character-swap": "person.line.dotted.person"
        case "enhance": "sparkles"
        default: "circle.dotted"
        }
    }
}

/// The providers as one row of segments, mark over a short name; the full
/// caveat of the selected one is the section footer. Dimmed, not removed, on
/// a pipeline without a try-on stage so the pipeline rows below do not jump.
struct ProviderSegments: View {
    let providers: [PipelineProvider]
    let current: String
    let enabled: Bool
    let onSelected: (String) async -> Void

    var body: some View {
        HStack(spacing: 6) {
            ForEach(providers) { provider in
                let selected = provider.id == current
                Button {
                    if !selected { Task { await onSelected(provider.id) } }
                } label: {
                    VStack(spacing: 6) {
                        ProviderMark(id: provider.id)
                        Text(ProviderText.short(id: provider.id, label: provider.label))
                            .font(.footnote.weight(selected ? .semibold : .regular))
                            .lineLimit(1).minimumScaleFactor(0.8)
                    }
                    .foregroundStyle(selected ? Theme.label : Theme.secondary)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(selected ? Theme.accent.opacity(0.14) : .clear,
                                in: .rect(cornerRadius: Theme.Radius.medium - 2))
                    .overlay {
                        RoundedRectangle(cornerRadius: Theme.Radius.medium - 2)
                            .strokeBorder(Theme.accent.opacity(selected ? 0.7 : 0), lineWidth: 1)
                    }
                    .contentShape(.rect)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(provider.label)
                .accessibilityAddTraits(selected ? .isSelected : [])
                .accessibilityValue(selected ? "Selected" : "Not selected")
            }
        }
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.4)
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

    /// A name that fits a third of the sheet's width; the server's names run
    /// to "Qwen-Image API (DashScope)".
    static func short(id: String, label: String) -> String {
        switch id {
        case "qwen": "Pod"
        case "gemini": "Gemini"
        case "qwen-max": "Qwen API"
        default: ProviderText(label: label).name
        }
    }
}

/// The provider's mark in one color, like an SF Symbol: the brand shape is
/// what's recognizable at 22pt, and brand colors would be the only saturated
/// things in the sheet besides the accent that means "selected".
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
        .frame(width: 22, height: 22)
        .frame(width: 28)
        .accessibilityHidden(true)
    }
}

private extension String {
    var capitalizedFirst: String { prefix(1).uppercased() + dropFirst() }
}
