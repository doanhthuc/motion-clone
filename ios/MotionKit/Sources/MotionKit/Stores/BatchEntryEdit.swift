import Foundation

/// The request an edit of a queued batch job sends (2026-09-26). The one rule
/// it adds over naming the change: a saved try-on goes with the change that
/// would make it wrong, in the same request.
public enum BatchEntryEdit {
    /// The roles a try-on image is made from; the driver is not one.
    static let seedRoles: Set<String> = ["character", "outfit"]

    public static func replacing(_ role: String, with materialID: String?,
                                 in entry: DraftBatchEntry) -> BatchEntryPatch {
        let stale = entry.tryonSeed != nil && seedRoles.contains(role)
        return BatchEntryPatch(slots: [role: materialID], seed: stale ? .clear : .keep)
    }

    public static func provider(_ provider: String, in entry: DraftBatchEntry) -> BatchEntryPatch {
        let refused = entry.tryonSeed != nil && !RunFlow.localTryonProviders.contains(provider)
        return BatchEntryPatch(provider: provider, seed: refused ? .clear : .keep)
    }

    /// Only a hosted provider reads a seed; the server refuses one otherwise.
    public static func canSeed(_ entry: DraftBatchEntry) -> Bool {
        RunFlow.localTryonProviders.contains(entry.provider)
    }
}
