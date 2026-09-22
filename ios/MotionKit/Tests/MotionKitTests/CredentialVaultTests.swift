import Foundation
import Testing
@testable import MotionKit

@Suite struct CredentialVaultTests {
    let info: [String: Any] = [
        "CONTROL_API_URL": "https://api.example.test",
        "CF_ACCESS_CLIENT_ID": "id-1", "CF_ACCESS_CLIENT_SECRET": "sec-1", "CONTROL_API_TOKEN": "tok-1",
    ]

    @Test func seedsEmptyStorageFromInfo() throws {
        let vault = CredentialVault(storage: InMemorySecretStorage())
        vault.seedIfEmpty(from: info)
        let c = try #require(vault.load())
        #expect(c.baseURL.absoluteString == "https://api.example.test")
        #expect(c.bearerToken == "tok-1")
    }

    @Test func seedingNeverOverwritesAnEditedValue() throws {
        let storage = InMemorySecretStorage()
        try storage.write("CONTROL_API_TOKEN", "edited-in-settings")
        let vault = CredentialVault(storage: storage)
        vault.seedIfEmpty(from: info)
        #expect(vault.load()?.bearerToken == "edited-in-settings")
    }

    @Test func unexpandedOrEmptyBuildSettingsAreIgnored() {
        // A build without Secrets.xcconfig leaves "$(CONTROL_API_URL)" or "".
        let vault = CredentialVault(storage: InMemorySecretStorage())
        vault.seedIfEmpty(from: ["CONTROL_API_URL": "$(CONTROL_API_URL)", "CF_ACCESS_CLIENT_ID": ""])
        #expect(vault.load() == nil)
    }

    @Test func loadIsNilUntilAllFourExist() throws {
        let storage = InMemorySecretStorage()
        try storage.write("CONTROL_API_URL", "https://x.test")
        #expect(CredentialVault(storage: storage).load() == nil)
    }

    @Test func saveRoundTrips() throws {
        let vault = CredentialVault(storage: InMemorySecretStorage())
        let c = Credentials(baseURL: URL(string: "https://y.test")!, accessClientID: "a",
                            accessClientSecret: "b", bearerToken: "c")
        try vault.save(c)
        #expect(vault.load() == c)
    }
}
