import SwiftUI
import MotionKit

struct SettingsView: View {
    @Environment(AppModel.self) private var model
    var firstRun = false
    @State private var url = ""
    @State private var clientID = ""
    @State private var clientSecret = ""
    @State private var token = ""
    @State private var testResult: String?
    @State private var testOK = false
    @State private var saveError: String?

    var body: some View {
        Form {
            if firstRun {
                Section {
                    Text("No API credentials yet. Run `make ios-secrets` and rebuild, or paste them here.")
                        .font(.subheadline).foregroundStyle(Theme.secondary)
                }
            }
            Section("Control-plane API") {
                TextField("Base URL", text: $url).textInputAutocapitalization(.never).keyboardType(.URL)
            }
            Section("Cloudflare Access (service token)") {
                TextField("CF-Access-Client-Id", text: $clientID).textInputAutocapitalization(.never)
                SecureField("CF-Access-Client-Secret", text: $clientSecret)
            }
            Section("API bearer token") {
                SecureField("Authorization: Bearer", text: $token)
            }
            Section {
                Button("Save & test connection") { Task { await saveAndTest() } }
                if let testResult {
                    Label(testResult, systemImage: testOK ? "checkmark.circle.fill" : "xmark.octagon.fill")
                        .font(.subheadline.monospacedDigit())
                        .foregroundStyle(testOK ? Theme.label : Theme.danger)
                }
                if let saveError { Text(saveError).foregroundStyle(Theme.danger) }
            }
            Section("Notifications") {
                Text("Telegram reports progress and results — this app has no push (no paid Apple account).")
                    .font(.subheadline).foregroundStyle(Theme.secondary)
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear(perform: load)
    }

    private func load() {
        guard let c = model.vault.load() else { return }
        url = c.baseURL.absoluteString
        clientID = c.accessClientID
        clientSecret = c.accessClientSecret
        token = c.bearerToken
    }

    private func saveAndTest() async {
        saveError = nil
        guard let base = URL(string: url.trimmingCharacters(in: .whitespaces)), base.scheme == "https" else {
            saveError = "Base URL must be an https:// URL."
            return
        }
        let credentials = Credentials(baseURL: base, accessClientID: clientID.trimmingCharacters(in: .whitespaces),
                                      accessClientSecret: clientSecret.trimmingCharacters(in: .whitespaces),
                                      bearerToken: token.trimmingCharacters(in: .whitespaces))
        do { try model.vault.save(credentials) } catch {
            saveError = "Couldn't write the Keychain: \(error)"
            return
        }
        guard model.reconnect() else {
            testOK = false
            testResult = "A spend request is still in flight — save again once it's answered."
            return
        }
        do {
            let latency = try await APIClient(credentials: credentials).health()
            testOK = true
            let ms = Int((Double(latency.components.seconds) + Double(latency.components.attoseconds) / 1e18) * 1000)
            testResult = "Connected · \(ms) ms"
        } catch {
            testOK = false
            testResult = error.userMessage
        }
    }
}
