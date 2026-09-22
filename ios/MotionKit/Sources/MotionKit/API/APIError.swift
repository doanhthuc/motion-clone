import Foundation

public enum APIError: Error, Sendable, Equatable {
    /// The API answered with `{"error": {code, message}}`, or a non-JSON
    /// error body (code `http_<status>`).
    case server(status: Int, code: String, message: String)
    /// Cloudflare refused before the API saw the request: a missing/expired
    /// Access service token, or a 1010 browser-signature block. HTML, not JSON.
    case accessDenied(status: Int)
    case transport(String)
    case decoding(String)

    public var isOffline: Bool {
        if case .transport = self { return true }
        return false
    }

    /// What the phone shows. 409/422 carry the server's own text — the same
    /// wording Telegram shows for the same refusal.
    public var userMessage: String {
        switch self {
        case .accessDenied:
            return "Cloudflare Access rejected the request — check the service token in Settings."
        case .transport(let detail):
            return "Can't reach the control plane (\(detail))."
        case .decoding(let detail):
            return "The server answered in a shape this app doesn't know (\(detail)). Update the app."
        case .server(let status, let code, let message):
            switch status {
            case 401: return "Bearer token rejected — check Settings."
            case 404: return "Not found — it may have been removed from Telegram."
            case 502: return "RunPod/Vast didn't answer. Try again."
            case 503: return "The bot is busy. Try again in a moment."
            case 500...: return "Server error (\(code))."
            default: return message
            }
        }
    }
}
