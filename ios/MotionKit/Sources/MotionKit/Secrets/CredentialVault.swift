import Foundation

/// Same names as the root .env keys `make api-smoke` reads, so one grep finds
/// every place a secret travels.
public enum SecretKey: String, CaseIterable, Sendable {
    case baseURL = "CONTROL_API_URL"
    case accessClientID = "CF_ACCESS_CLIENT_ID"
    case accessClientSecret = "CF_ACCESS_CLIENT_SECRET"
    case bearerToken = "CONTROL_API_TOKEN"
}

public struct CredentialVault: Sendable {
    let storage: SecretStorage
    public init(storage: SecretStorage) { self.storage = storage }

    /// Copies build-time values (Info.plist, from Secrets.xcconfig) into the
    /// Keychain — only into empty keys, so an edit made in Settings is never
    /// overwritten by the next reinstall.
    public func seedIfEmpty(from info: [String: Any]) {
        for key in SecretKey.allCases where (storage.read(key.rawValue) ?? "").isEmpty {
            guard let value = info[key.rawValue] as? String,
                  !value.isEmpty, !value.hasPrefix("$(") else { continue }
            try? storage.write(key.rawValue, value)
        }
    }

    public func load() -> Credentials? {
        func v(_ k: SecretKey) -> String? {
            guard let s = storage.read(k.rawValue), !s.isEmpty else { return nil }
            return s
        }
        guard let urlString = v(.baseURL), let url = URL(string: urlString),
              let id = v(.accessClientID), let secret = v(.accessClientSecret),
              let token = v(.bearerToken) else { return nil }
        return Credentials(baseURL: url, accessClientID: id, accessClientSecret: secret, bearerToken: token)
    }

    public func save(_ c: Credentials) throws {
        try storage.write(SecretKey.baseURL.rawValue, c.baseURL.absoluteString)
        try storage.write(SecretKey.accessClientID.rawValue, c.accessClientID)
        try storage.write(SecretKey.accessClientSecret.rawValue, c.accessClientSecret)
        try storage.write(SecretKey.bearerToken.rawValue, c.bearerToken)
    }
}
