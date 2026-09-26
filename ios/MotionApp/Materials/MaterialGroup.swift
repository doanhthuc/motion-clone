import MotionKit

/// One category of the material library. By role, in the order a job is built
/// from: what moves, what is worn, who wears it. An image nobody has sorted
/// yet is Unsorted, not guessed — the same reason `tgbot/job.py`'s `slot_for`
/// asks instead of inferring.
struct MaterialGroup: Identifiable, Hashable {
    let title: String
    let role: MaterialRole?
    var id: String { title }

    static let all: [MaterialGroup] = [
        MaterialGroup(title: "Motion drivers", role: .driver),
        MaterialGroup(title: "Outfits", role: .outfit),
        MaterialGroup(title: "Characters", role: .character),
        MaterialGroup(title: "Backgrounds", role: .background),
        MaterialGroup(title: "Unsorted", role: nil),
    ]

    /// The plural category name a role is shown under.
    static func title(for role: MaterialRole) -> String {
        all.first { $0.role == role }?.title ?? role.title
    }

    func items(in materials: [MotionKit.Material]) -> [MotionKit.Material] {
        materials.filter { $0.materialRole == role }
    }
}

extension MaterialRole {
    /// Singular, for "Move to" and the Add sheet's category choice.
    var title: String {
        switch self {
        case .driver: "Motion driver"
        case .character: "Character"
        case .outfit: "Outfit"
        case .background: "Background"
        }
    }
}
