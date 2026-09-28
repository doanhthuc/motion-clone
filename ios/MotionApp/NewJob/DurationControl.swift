import MotionKit
import SwiftUI

/// Full / 10s / 15s / Custom, shared by NewJob's settings sheet and a queued
/// batch entry's detail (2026-09-28). "Full" is the driver's own measured
/// length — the server's own default when no override is sent, so it needs
/// no bound. The other segments send `driverDurSec`, which the server
/// refuses past the driver's own probed length (`duration_too_long`) — this
/// view disables anything that can't pass rather than round-tripping to find
/// out, using the same measured length the server would check against.
struct DurationControl: View {
    /// `nil` is Full.
    let current: Int?
    /// The attached driver's own probed length; `nil` disables every
    /// segment but Full (no driver attached yet to measure against).
    let driverLengthSec: Double?
    let disabled: Bool
    let onSelect: (DurationChoice) async -> Void

    // Optimistic, the same pattern as SettingsSheet's pendingPipeline: shown
    // until the PATCH this view fired lands and `current` catches up.
    @State private var pending: DurationChoice?
    @State private var expandCustom = false
    @State private var customValue: Double = 1

    private var maxSeconds: Int { max(1, Int(driverLengthSec ?? 0)) }

    private var effective: Int? {
        switch pending {
        case .full: nil
        case .seconds(let n): n
        case nil: current
        }
    }

    private var isCustomValue: Bool {
        guard let effective else { return false }
        return effective != 10 && effective != 15
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                segment(label: "Full", selected: effective == nil, enabled: true) {
                    select(.full)
                }
                segment(label: "10s", selected: effective == 10, enabled: maxSeconds >= 10) {
                    select(.seconds(10))
                }
                segment(label: "15s", selected: effective == 15, enabled: maxSeconds >= 15) {
                    select(.seconds(15))
                }
                segment(label: isCustomValue ? "\(effective ?? maxSeconds)s" : "Custom",
                       selected: isCustomValue || expandCustom, enabled: maxSeconds > 1) {
                    if !isCustomValue { customValue = Double(effective ?? maxSeconds) }
                    withAnimation(.snappy) { expandCustom = true }
                }
            }
            if expandCustom || isCustomValue {
                customSlider.transition(.opacity.combined(with: .move(edge: .top)))
            }
            if driverLengthSec == nil {
                Text("Attach a driver video to choose a length.")
                    .font(.caption).foregroundStyle(Theme.secondary)
            }
        }
        .disabled(disabled)
        .animation(.snappy, value: expandCustom)
        .onAppear(perform: syncCustomValue)
        .onChange(of: current) { _, _ in syncCustomValue() }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Length")
        .accessibilityValue(effective.map { "\($0) seconds" } ?? "Full")
    }

    private func syncCustomValue() {
        guard let current, current != 10, current != 15 else { return }
        customValue = min(Double(maxSeconds), max(1, Double(current)))
    }

    private var customSlider: some View {
        HStack(spacing: 10) {
            Button { nudge(-1) } label: { Image(systemName: "minus.circle.fill") }
                .disabled(customValue <= 1)
            Slider(value: $customValue, in: 1...Double(maxSeconds), step: 1) { editing in
                if !editing { select(.seconds(Int(customValue))) }
            }
            Button { nudge(1) } label: { Image(systemName: "plus.circle.fill") }
                .disabled(customValue >= Double(maxSeconds))
            Text("\(Int(customValue))s")
                .font(.footnote.weight(.semibold).monospacedDigit())
                .frame(minWidth: 32)
        }
        .foregroundStyle(Theme.accent)
        .buttonStyle(.plain)
        .accessibilityIdentifier("duration.custom")
    }

    private func nudge(_ delta: Double) {
        customValue = min(Double(maxSeconds), max(1, customValue + delta))
        select(.seconds(Int(customValue)))
    }

    private func select(_ choice: DurationChoice) {
        pending = choice
        Task {
            await onSelect(choice)
            pending = nil
        }
    }

    private func segment(label: String, selected: Bool, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(label)
                .font(.footnote.weight(selected ? .semibold : .regular))
                .foregroundStyle(selected ? Theme.label : Theme.secondary)
                .lineLimit(1).minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .background(selected ? Theme.accent.opacity(0.14) : .clear,
                           in: .rect(cornerRadius: Theme.Radius.medium - 2))
                .overlay {
                    RoundedRectangle(cornerRadius: Theme.Radius.medium - 2)
                        .strokeBorder(Theme.accent.opacity(selected ? 0.7 : 0), lineWidth: 1)
                }
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.4)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}
