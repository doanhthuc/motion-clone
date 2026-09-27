import SwiftUI
import MotionKit

/// One of the five GPUs on the Pod stage: its home stock at a glance, the
/// price, whether it is the next rental's card, and whether it is watched.
/// Tapping opens the GPU sheet; nothing here spends or rewrites `.env`.
struct GpuTile: View {
    let row: GpuStockRow
    let selected: Bool
    let watching: Int
    let armed: Bool
    let height: CGFloat
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Circle().fill(dotColor).frame(width: 10, height: 10)
                    Text(row.name).font(.headline).lineLimit(1).minimumScaleFactor(0.8)
                    Spacer(minLength: 0)
                    if selected {
                        Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.accent)
                    }
                }
                Text(stockLine).font(.subheadline.monospacedDigit())
                    .foregroundStyle(row.soldOutEverywhere ? Theme.warning : Theme.secondary)
                    .lineLimit(1)
                Spacer(minLength: 0)
                if watching > 0 {
                    Label(armed ? "Watching · auto-resume" : "Watching", systemImage: armed ? "bolt.fill" : "bell.fill")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(Theme.accent)
                        .lineLimit(1)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, minHeight: height, maxHeight: height, alignment: .topLeading)
            .background(Theme.surface, in: .rect(cornerRadius: Theme.Radius.medium))
            .overlay {
                if selected {
                    RoundedRectangle(cornerRadius: Theme.Radius.medium).stroke(Theme.accent, lineWidth: 1.5)
                }
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("gpu.tile.\(row.gpu)")
    }

    private var status: String { row.home?.stock.lowercased() ?? (row.soldOutEverywhere ? "none" : "") }

    private var dotColor: Color {
        switch status {
        case "high": .green
        case "medium": .yellow
        case "low": .orange
        case "none": Theme.danger
        default: Theme.secondary
        }
    }

    private var stockLine: String {
        if row.soldOutEverywhere { return "Sold out everywhere" }
        let price = row.usdPerHr.map { "\(Format.usd($0))/h" } ?? "price ?"
        return "\(price) · \(row.home?.stock ?? "not at home")"
    }
}
