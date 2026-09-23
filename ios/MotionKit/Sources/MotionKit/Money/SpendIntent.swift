import Foundation

public enum SpendProvider: String, Codable, Sendable, Equatable {
    case runpod, vast
}

public enum TryonChoice: String, Codable, Sendable, Equatable {
    case reuse, rerun
}

public enum SpendKind: Sendable, Equatable {
    case phaseA, regen, confirm, resume
}

/// One spend call, exactly as `AppRuns`/`AppPod` read it (bot.py). Persisted
/// in the idempotency ledger, so a replay after an app kill resends the very
/// same path and body with the very same key.
public enum SpendIntent: Codable, Sendable, Equatable {
    case phaseA
    case regen(runID: String, index: String, runToken: String, guidance: [Guidance])
    case confirm(runID: String, provider: SpendProvider, panelToken: String, gpu: String?,
                 tryon: TryonChoice?)
    case resume(runID: String, provider: SpendProvider, runToken: String, gpu: String?)

    public var kind: SpendKind {
        switch self {
        case .phaseA: .phaseA
        case .regen: .regen
        case .confirm: .confirm
        case .resume: .resume
        }
    }

    public var runID: String? {
        switch self {
        case .phaseA: nil
        case let .regen(runID, _, _, _), let .confirm(runID, _, _, _, _),
             let .resume(runID, _, _, _): runID
        }
    }

    public var path: [String] {
        switch self {
        case .phaseA: ["v1", "runs", "phase-a"]
        case let .regen(runID, index, _, _): ["v1", "runs", runID, "tryon", index, "regen"]
        case let .confirm(runID, _, _, _, _): ["v1", "runs", runID, "confirm"]
        case let .resume(runID, _, _, _): ["v1", "runs", runID, "resume"]
        }
    }

    /// Keys are the server's snake_case names; optional fields are omitted
    /// rather than sent as null (`_gpu_mismatch` treats a missing `gpu` as
    /// "no check", and `confirm` rejects a `tryon` that is not reuse/rerun).
    public func body() -> Data {
        var object: [String: Any] = [:]
        switch self {
        case .phaseA:
            break
        case let .regen(_, _, runToken, guidance):
            object["run_token"] = runToken
            object["guidance"] = guidance.map(\.rawValue)
        case let .confirm(_, provider, panelToken, gpu, tryon):
            object["provider"] = provider.rawValue
            object["panel_token"] = panelToken
            if let gpu { object["gpu"] = gpu }
            if let tryon { object["tryon"] = tryon.rawValue }
        case let .resume(_, provider, runToken, gpu):
            object["provider"] = provider.rawValue
            object["run_token"] = runToken
            if let gpu { object["gpu"] = gpu }
        }
        // Strings, arrays of strings and nothing else: serialization cannot fail.
        return (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
    }
}
