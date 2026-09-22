import Foundation

public struct ByteRangeResponse: Sendable, Equatable {
    public let data: Data
    public let contentType: String?
    public let totalLength: Int64?
    public let acceptsRanges: Bool
}

public actor APIClient {
    public nonisolated let credentials: Credentials
    private let session: URLSession
    /// path → last ETag seen. Only `getIfChanged` reads it.
    private var etags: [String: String] = [:]

    public init(credentials: Credentials, session: URLSession = .shared) {
        self.credentials = credentials
        self.session = session
    }

    /// Sent on every request. Also handed to AVURLAsset for Range playback.
    /// An explicit User-Agent: Cloudflare answered Python urllib's default UA
    /// with error 1010 (2026-09-21).
    public nonisolated var authHeaders: [String: String] {
        ["CF-Access-Client-Id": credentials.accessClientID,
         "CF-Access-Client-Secret": credentials.accessClientSecret,
         "Authorization": "Bearer \(credentials.bearerToken)",
         "User-Agent": "MotionApp/1 (iOS)"]
    }

    /// Each component becomes exactly one percent-encoded path segment.
    public nonisolated func url(_ components: String...) -> URL {
        url(components)
    }

    nonisolated func url(_ components: [String]) -> URL {
        components.reduce(credentials.baseURL) { $0.appending(component: $1) }
    }

    public func get<T: Decodable & Sendable>(_ type: T.Type, _ components: String...) async throws(APIError) -> T {
        let (data, _) = try await send(url(components), extraHeaders: [:], okStatuses: [200])
        return try decode(type, data)
    }

    /// nil means 304 Not Modified — keep what you have.
    public func getIfChanged<T: Decodable & Sendable>(_ type: T.Type, _ components: String...) async throws(APIError) -> T? {
        let target = url(components)
        let key = target.absoluteString
        var headers: [String: String] = [:]
        if let etag = etags[key] { headers["If-None-Match"] = etag }
        let (data, response) = try await send(target, extraHeaders: headers, okStatuses: [200, 304])
        if response.statusCode == 304 { return nil }
        if let etag = response.value(forHTTPHeaderField: "ETag") { etags[key] = etag }
        return try decode(type, data)
    }

    public func health() async throws(APIError) -> Duration {
        let clock = ContinuousClock()
        let start = clock.now
        _ = try await send(url(["v1", "health"]), extraHeaders: [:], okStatuses: [200])
        return clock.now - start
    }

    public func data(_ components: String...) async throws(APIError) -> Data {
        try await send(url(components), extraHeaders: [:], okStatuses: [200]).0
    }

    public func post<Response: Decodable & Sendable, Body: Encodable & Sendable>(
        _ response: Response.Type, body: Body, _ components: String...
    ) async throws(APIError) -> Response {
        let encoded: Data
        do {
            let encoder = JSONEncoder()
            encoder.keyEncodingStrategy = .convertToSnakeCase
            encoded = try encoder.encode(body)
        } catch {
            throw .transport("couldn't encode the request: \(error.localizedDescription)")
        }
        let (data, _) = try await send(
            url(components), method: "POST", body: encoded, contentType: "application/json",
            extraHeaders: [:], okStatuses: [200, 201])
        return try decode(response, data)
    }

    public func post<Response: Decodable & Sendable>(
        _ response: Response.Type, _ components: String...
    ) async throws(APIError) -> Response {
        let (data, _) = try await send(
            url(components), method: "POST", extraHeaders: [:], okStatuses: [200, 201])
        return try decode(response, data)
    }

    public func put(data: Data, _ components: String...) async throws(APIError) {
        _ = try await send(
            url(components), method: "PUT", body: data, contentType: "application/octet-stream",
            timeout: 120, extraHeaders: [:], okStatuses: [200])
    }

    public func delete(_ components: String...) async throws(APIError) {
        _ = try await send(
            url(components), method: "DELETE", extraHeaders: [:], okStatuses: [204])
    }

    /// Fetches one byte range with the same Access and bearer headers as every
    /// other request. Used by AVAssetResourceLoader for authenticated seeking.
    public func byteRange(from offset: Int64, length: Int?,
                          _ components: String...) async throws(APIError) -> ByteRangeResponse {
        guard offset >= 0 else { throw .transport("invalid negative byte offset") }
        var range = "bytes=\(offset)-"
        if let length {
            guard length > 0 else { throw .transport("invalid empty byte range") }
            let (end, overflow) = offset.addingReportingOverflow(Int64(length - 1))
            guard !overflow else { throw .transport("byte range overflow") }
            range += "\(end)"
        }
        let (body, response) = try await send(
            url(components), extraHeaders: ["Range": range], okStatuses: [206])
        let contentRange = response.value(forHTTPHeaderField: "Content-Range")
        let totalLength = contentRange.flatMap(Self.totalLength)
        let acceptsRanges = response.statusCode == 206
            || response.value(forHTTPHeaderField: "Accept-Ranges")?.lowercased() == "bytes"
        return ByteRangeResponse(
            data: body,
            contentType: response.value(forHTTPHeaderField: "Content-Type"),
            totalLength: totalLength,
            acceptsRanges: acceptsRanges)
    }

    /// Downloads to a temp file that keeps the source extension — Photos
    /// decides video vs image from it.
    public func download(_ components: String...) async throws(APIError) -> URL {
        let body = try await send(url(components), extraHeaders: [:], okStatuses: [200]).0
        let name = components.last.map { ($0 as NSString).lastPathComponent } ?? "download"
        let dir = FileManager.default.temporaryDirectory.appending(component: UUID().uuidString)
        let file = dir.appending(component: name)
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try body.write(to: file)
        } catch {
            throw .transport("couldn't write the download: \(error.localizedDescription)")
        }
        return file
    }

    private func send(_ url: URL, method: String = "GET", body: Data? = nil,
                      contentType: String? = nil, timeout: TimeInterval = 30,
                      extraHeaders: [String: String],
                      okStatuses: Set<Int>) async throws(APIError) -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = body
        request.timeoutInterval = timeout
        if let contentType { request.setValue(contentType, forHTTPHeaderField: "Content-Type") }
        for (k, v) in authHeaders.merging(extraHeaders, uniquingKeysWith: { $1 }) {
            request.setValue(v, forHTTPHeaderField: k)
        }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw .transport(error.localizedDescription)
        }
        guard let http = response as? HTTPURLResponse else { throw .transport("not an HTTP response") }
        if okStatuses.contains(http.statusCode) { return (data, http) }
        throw Self.error(status: http.statusCode, body: data)
    }

    static func error(status: Int, body: Data) -> APIError {
        struct Envelope: Decodable { struct Inner: Decodable { let code: String; let message: String }; let error: Inner }
        if let env = try? JSONDecoder().decode(Envelope.self, from: body) {
            return .server(status: status, code: env.error.code, message: env.error.message)
        }
        if status == 403 { return .accessDenied(status: status) }
        let text = String(decoding: body.prefix(200), as: UTF8.self)
        return .server(status: status, code: "http_\(status)", message: text)
    }

    private static func totalLength(_ contentRange: String) -> Int64? {
        guard contentRange.lowercased().hasPrefix("bytes "),
              let slash = contentRange.lastIndex(of: "/") else { return nil }
        return Int64(contentRange[contentRange.index(after: slash)...])
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws(APIError) -> T {
        do { return try MotionJSON.decoder.decode(type, from: data) }
        catch { throw .decoding(String(describing: error)) }
    }
}
