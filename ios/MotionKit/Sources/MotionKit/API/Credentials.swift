import Foundation

/// The four values `make api-smoke` also uses: tunnel URL, Cloudflare Access
/// service token (id + secret), and the API's own bearer token.
public struct Credentials: Sendable, Equatable {
    public var baseURL: URL
    public var accessClientID: String
    public var accessClientSecret: String
    public var bearerToken: String

    public init(baseURL: URL, accessClientID: String, accessClientSecret: String, bearerToken: String) {
        self.baseURL = baseURL
        self.accessClientID = accessClientID
        self.accessClientSecret = accessClientSecret
        self.bearerToken = bearerToken
    }
}
