import Foundation
import Security

public protocol SecretStorage: Sendable {
    func read(_ key: String) -> String?
    func write(_ key: String, _ value: String) throws
    func delete(_ key: String)
}

public struct KeychainError: Error, Equatable { public let status: OSStatus }

/// Generic-password items, this device only, readable after first unlock.
/// Survives reinstalling the app from Xcode over itself (the weekly
/// free-provisioning reinstall).
public struct KeychainStorage: SecretStorage {
    let service: String
    public init(service: String = "xyz.doanhthuc.motion") { self.service = service }

    private func query(_ key: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: key]
    }

    public func read(_ key: String) -> String? {
        var q = query(key)
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public func write(_ key: String, _ value: String) throws {
        delete(key)
        var q = query(key)
        q[kSecValueData as String] = Data(value.utf8)
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        let status = SecItemAdd(q as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError(status: status) }
    }

    public func delete(_ key: String) {
        SecItemDelete(query(key) as CFDictionary)
    }
}

public final class InMemorySecretStorage: SecretStorage, @unchecked Sendable {
    private let lock = NSLock()
    private var values: [String: String] = [:]
    public init() {}
    public func read(_ key: String) -> String? { lock.withLock { values[key] } }
    public func write(_ key: String, _ value: String) throws { lock.withLock { values[key] = value } }
    public func delete(_ key: String) { _ = lock.withLock { values.removeValue(forKey: key) } }
}
