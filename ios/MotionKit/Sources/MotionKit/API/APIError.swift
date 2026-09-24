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

    /// What the phone shows. 409 carries the server's own text — the same
    /// wording Telegram shows for the same refusal. A 422 does too, except
    /// `invalid`, whose text is never written for a reader on the phone, on any
    /// path: that one gets a headline here and keeps its text in
    /// `detailMessage`.
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
            case 422 where code == "invalid":
                // The server's text for this one code is never written for a
                // reader on the phone: see `detailMessage`, which lists its four
                // forms and names the Telegram reader one of them is for.
                return "This draft didn't pass validation, so it can't run yet."
            case 502: return "RunPod/Vast didn't answer. Try again."
            case 503: return "The bot is busy. Try again in a moment."
            case 500...: return "Server error (\(code))."
            default: return message
            }
        }
    }

    /// The server's own text when `userMessage` replaces it with a headline, so
    /// a view can offer it behind a disclosure instead of discarding it. `nil`
    /// for every other error — and `nil` for a blank one, so `bot.py:6051`'s
    /// empty `invalid` message renders no empty disclosure.
    ///
    /// Only `422 invalid` needs this. Its message is one of: the validator's raw
    /// stdout+stderr, path-stripped and truncated (`drafts.py:535,544-548`); the
    /// literal `make batch-validate failed` (`drafts.py:566`); Telegram-facing
    /// copy telling the reader to "send the file(s) again" into a chat they are
    /// not in (`bot.py:6064`); or an empty string, because `_render_and_validate`
    /// already sent the real reason to Telegram (`bot.py:6048-6051`). Before
    /// 2026-09-24 all four reached the phone verbatim through `userMessage`'s
    /// `default` branch, and the empty one rendered a banner with no text in it.
    public var detailMessage: String? {
        guard case let .server(status, code, message) = self,
              status == 422, code == "invalid" else { return nil }
        guard !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return message
    }
}
