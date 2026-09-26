import MotionKit
import SwiftUI

/// Aspect, count, and model with its per-image price (like Flow's sheet).
struct StudioSettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    let studio: StudioStore

    var body: some View {
        @Bindable var studio = studio
        NavigationStack {
            Form {
                Section {
                    Text("This will use about \(StudioFormat.usd(studio.estimateUSD))")
                        .font(.footnote).foregroundStyle(Theme.secondary)
                }
                Section("Aspect") {
                    Picker("Aspect", selection: $studio.aspect) {
                        ForEach(studio.catalog?.aspects ?? [], id: \.self) { Text($0).tag($0) }
                    }.pickerStyle(.segmented)
                }
                Section("Images") {
                    Picker("Count", selection: $studio.count) {
                        ForEach(1...(studio.catalog?.maxCount ?? 4), id: \.self) { Text("x\($0)").tag($0) }
                    }.pickerStyle(.segmented)
                }
                Section("Model") {
                    ForEach(studio.catalog?.models ?? []) { m in
                        let reason = studio.disabledReason(for: m)
                        Button { studio.modelKey = m.key } label: {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(m.label)
                                    if let reason { Text(reason).font(.caption).foregroundStyle(Theme.secondary) }
                                }
                                Spacer()
                                Text("\(StudioFormat.usd(m.priceUsd))/image").font(.caption).foregroundStyle(Theme.secondary)
                                if m.key == studio.modelKey { Image(systemName: "checkmark").foregroundStyle(Theme.accent) }
                            }
                        }
                        .disabled(reason != nil)
                        .accessibilityIdentifier("studio.model.\(m.key)")
                    }
                }
            }
            .navigationTitle("Settings").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.accessibilityIdentifier("studio.settings.done")
                }
            }
        }
    }
}
