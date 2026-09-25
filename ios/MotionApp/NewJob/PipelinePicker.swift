import MotionKit
import SwiftUI

/// One "Settings" section: the pipeline and the provider, one row each, both
/// opening a full list of choices. Until 2026-09-25 the providers were
/// checkmark rows inline, and their ~40-character labels plus caveats took
/// ~550 pt, which pushed the materials — what the user actually came to pick —
/// below the tab bar. The caveats now live in `ProviderChoiceList`, and the
/// row keeps the first clause ("Runs here, no pod wait").
@MainActor
struct PipelinePicker: View {
    let pipeline: Pipeline
    let pipelines: [Pipeline]
    let selectedProvider: String
    let disabled: Bool
    let onPipelineSelected: (String) async -> Void
    let onProviderSelected: (String) async -> Void

    var body: some View {
        Section("Settings") {
            NavigationLink {
                PipelineChoiceList(current: pipeline.id, pipelines: pipelines,
                                   name: displayName, stages: stageLine,
                                   onSelected: onPipelineSelected)
            } label: {
                SettingRow(title: displayName(pipeline.id),
                           detail: pipeline.stages.isEmpty ? nil : stageLine(pipeline)) {
                    Image(systemName: "arrow.triangle.branch")
                        .resizable().scaledToFit()
                        .foregroundStyle(Theme.label)
                        .frame(width: 20, height: 20)
                        .frame(width: 28)
                }
            }
            .disabled(disabled)
            .accessibilityLabel("Pipeline")
            .accessibilityValue(displayName(pipeline.id))

            if let provider = pipeline.providers.first(where: { $0.id == selectedProvider })
                ?? pipeline.providers.first {
                let parts = ProviderText(label: provider.label)
                NavigationLink {
                    ProviderChoiceList(providers: pipeline.providers, current: selectedProvider,
                                       onSelected: onProviderSelected)
                } label: {
                    SettingRow(title: parts.name, detail: parts.shortCaveat) {
                        ProviderMark(id: provider.id)
                    }
                }
                .disabled(disabled)
                .accessibilityLabel("Provider")
                .accessibilityValue(parts.name)
            }
        }
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

/// A leading mark, a title and one secondary line — the two Settings rows.
private struct SettingRow<Mark: View>: View {
    let title: String
    let detail: String?
    @ViewBuilder let mark: () -> Mark

    var body: some View {
        HStack(spacing: 12) {
            mark()
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.body).foregroundStyle(Theme.label)
                if let detail {
                    Text(detail)
                        .font(.subheadline)
                        .foregroundStyle(Theme.secondary)
                        .lineLimit(1)
                }
            }
        }
    }
}

/// Every provider with its full caveat, so the choice is made knowing the
/// trade-off; the row that opens this shows only the first clause.
private struct ProviderChoiceList: View {
    let providers: [PipelineProvider]
    let current: String
    let onSelected: (String) async -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            Section {
                ForEach(providers) { provider in
                    let selected = provider.id == current
                    let parts = ProviderText(label: provider.label)
                    Button {
                        if !selected { Task { await onSelected(provider.id) } }
                        dismiss()
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
            } footer: {
                Text("Hosted providers make the try-on here, before any pod is rented.")
            }
        }
        .navigationTitle("Provider")
        .navigationBarTitleDisplayMode(.inline)
    }
}

/// A server provider label split for display: "☁️ Gemini API — runs here, no
/// pod wait; may crop…" → name "Gemini API", caveat "Runs here, no pod wait;
/// may crop…", short caveat "Runs here, no pod wait". The label leads with an
/// emoji, which the rows drop; VoiceOver in the choice list still gets it verbatim.
private struct ProviderText {
    let name: String
    let caveat: String?

    init(label: String) {
        let plain = String(label.drop { !$0.isLetter && !$0.isNumber })
        let parts = plain.components(separatedBy: " — ")
        name = parts[0]
        caveat = parts.count > 1 ? parts.dropFirst().joined(separator: " — ").capitalizedFirst : nil
    }

    var shortCaveat: String? {
        caveat?.components(separatedBy: ";").first
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
