import Foundation

/// The closed regenerate vocabulary (API spec §5.10). The server answers any
/// other string with 400, so the phone can only ever send these three.
public enum Guidance: String, Codable, Sendable, CaseIterable, Hashable {
    case keepFace = "keep_face"
    case tighterCrop = "tighter_crop"
    case matchLighting = "match_lighting"

    public var label: String {
        switch self {
        case .keepFace: "Keep face"
        case .tighterCrop: "Tighter crop"
        case .matchLighting: "Match lighting"
        }
    }
}
