import Foundation

public enum ImportedFilename {
    public static func sanitize(_ providerName: String?) -> String {
        let normalized = (providerName ?? "").replacingOccurrences(of: "\\", with: "/")
        let name = (normalized as NSString).lastPathComponent
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, name != ".", name != ".." else { return "upload.bin" }
        return name
    }
}

public enum Format {
    /// "12:34" under an hour, "1:12:47" from an hour.
    public static func clock(_ seconds: Double) -> String {
        let s = max(0, Int(seconds))
        let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
        return h > 0 ? String(format: "%d:%02d:%02d", h, m, sec) : String(format: "%02d:%02d", m, sec)
    }

    public static func usd(_ value: Double) -> String { String(format: "$%.2f", value) }

    public static func ago(_ seconds: Double) -> String {
        let s = max(0, Int(seconds))
        if s < 60 { return "\(s)s ago" }
        if s < 3600 { return "\(s / 60)m ago" }
        return "\(s / 3600)h ago"
    }

    /// Journal stage keys ("tryon", "motion", "enhance") → labels.
    public static func stageName(_ raw: String) -> String {
        if raw == "tryon" { return "Try-on" }
        let spaced = raw.replacingOccurrences(of: "_", with: " ")
        return spaced.prefix(1).uppercased() + spaced.dropFirst()
    }

    /// Balance runway: "12h 28m", or "45m" under an hour.
    public static func runway(hours: Double) -> String {
        let minutes = max(0, Int((hours * 60).rounded()))
        let h = minutes / 60, m = minutes % 60
        return h > 0 ? "\(h)h \(m)m" : "\(m)m"
    }
}

public enum CostEstimate {
    /// elapsed × quoted rate. A quote, not the invoice (`runpodctl billing`
    /// is the invoice); nil when the lease carries no rate (Vast).
    public static func usd(elapsed: Double, ratePerHour: Double?) -> Double? {
        guard let rate = ratePerHour else { return nil }
        return max(0, elapsed) / 3600 * rate
    }

    /// The rent panel's price: estimated minutes × the row's rate. A quote
    /// shown before the tap; nil when the row has no rate, and then no spend
    /// button is drawn for it.
    public static func quote(estimateMin: Double, usdPerHr: Double?) -> Double? {
        guard let rate = usdPerHr else { return nil }
        return max(0, estimateMin) / 60 * rate
    }
}
