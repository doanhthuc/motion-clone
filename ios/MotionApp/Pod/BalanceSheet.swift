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
                .refreshable {
                    async let a: Void = store.load()
                    async let b: Void = store.loadVast()
                    _ = await (a, b)
                }
        }
        .onAppear { store.prefetchVast() }
        .presentationDetents([.medium])
    }
}

/// Balance rows: the runway is the hero number, the Vast credit loads beside it.
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
            VStack(alignment: .leading, spacing: 4) {
                Text("Vast").font(.subheadline).foregroundStyle(Theme.secondary)
                if let vast = store.vastLine {
                    Text(vast).font(.title3.weight(.semibold).monospacedDigit())
                } else if store.isLoadingVast {
                    HStack(spacing: 8) {
                        ProgressView()
                        Text("Reading the Vast credit (~30 s)…").font(.footnote).foregroundStyle(Theme.secondary)
                    }
                }
                if let error = store.vastError {
                    Text("Couldn't read — \(error.userMessage)")
                        .font(.footnote).foregroundStyle(Theme.warning)
                }
            }
            .padding(.vertical, 2)
            ForEach(store.balance?.errors ?? [], id: \.self) { text in
                Text(text).font(.footnote).foregroundStyle(Theme.warning)
            }
            Button(store.vast == nil ? "Check Vast credit" : "Refresh Vast credit") {
                Task { await store.loadVast() }
            }
            .disabled(store.isLoadingVast)
            .accessibilityIdentifier("pod.checkVast")
        }
    }
}
