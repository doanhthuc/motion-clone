import SwiftUI
import MotionKit

struct BalanceSheet: View {
    let store: BalanceStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List { BalanceSection(store: store) }
                .navigationTitle("Balance")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
                .refreshable { await store.load() }
        }
        .presentationDetents([.medium])
    }
}

/// Balance rows: the runway is the hero number, the Vast credit is on tap.
struct BalanceSection: View {
    let store: BalanceStore

    var body: some View {
        Section("Balance") {
            if let line = store.runpodLine {
                let low = store.balance?.runpod?.lowRunway == true
                let refreshFailed = store.error != nil
                VStack(alignment: .leading, spacing: 4) {
                    Text("RunPod").font(.subheadline).foregroundStyle(Theme.secondary)
                    Text(line).font(.title3.weight(.semibold).monospacedDigit())
                        .foregroundStyle(low ? Theme.warning : Theme.label)
                    if low {
                        Label("Under 1 h of runway — top up before renting.", systemImage: "exclamationmark.triangle.fill")
                            .font(.footnote).foregroundStyle(Theme.warning)
                    }
                    if refreshFailed, let error = store.error {
                        Text("Couldn't refresh — \(error.userMessage)")
                            .font(.footnote).foregroundStyle(Theme.warning)
                    }
                }
                .opacity(refreshFailed ? 0.6 : 1)
                .padding(.vertical, 2)
            } else if let error = store.error {
                ErrorBanner(error: error) { await store.load() }
            } else {
                LoadingBlock()
            }
            if let vast = store.vastLine {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Vast").font(.subheadline).foregroundStyle(Theme.secondary)
                    Text(vast).font(.title3.weight(.semibold).monospacedDigit())
                }
                .padding(.vertical, 2)
            }
            ForEach(store.balance?.errors ?? [], id: \.self) { text in
                Text(text).font(.footnote).foregroundStyle(Theme.warning)
            }
            Button { Task { await store.loadVast() } } label: {
                HStack(spacing: 8) {
                    Text(store.isLoadingVast ? "Reading the Vast credit (~30 s)…" : "Check Vast credit")
                    if store.isLoadingVast { Spacer(); ProgressView() }
                }
            }
            .disabled(store.isLoadingVast)
            .accessibilityIdentifier("pod.checkVast")
        }
    }
}
