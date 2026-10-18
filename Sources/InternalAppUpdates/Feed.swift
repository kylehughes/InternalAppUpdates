import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// A source capable of loading update-feed bytes.
public protocol UpdateFeedLoading: Sendable {
    /// Loads a feed while validating its URL and enforcing the caller's response-size limit.
    ///
    /// - Parameters:
    ///   - url: The feed URL to validate and load.
    ///   - maximumResponseSize: The largest accepted response, in bytes.
    /// - Returns: The complete feed response body.
    func load(from url: URL, maximumResponseSize: Int) async throws -> Data
}

/// An error produced by ``URLSessionUpdateFeedLoader``.
public enum UpdateFeedLoaderError: Error, Equatable, Sendable {
    /// The server returned a non-success status code, or `0` when the response was not HTTP.
    case invalidHTTPStatus(Int)
    /// The response exceeded the configured byte limit, or the caller supplied a nonpositive limit.
    case responseTooLarge
    /// The initial or final response URL did not satisfy the feed URL policy.
    case insecureResponseURL
}

/// A bounded, timeout-controlled update-feed loader backed by `URLSession`.
public final class URLSessionUpdateFeedLoader: UpdateFeedLoading {
    private static let defaultTimeout = PositiveDuration.fifteenSeconds

    private let allowsInsecureLoopback: Bool
    private let session: URLSession
    private let timeout: PositiveDuration

    /// Creates a URL-session feed loader with a fixed, valid 15-second timeout without throwing.
    ///
    /// - Parameter allowsInsecureLoopback: Whether HTTP initial, redirect, and final URLs may use a loopback address
    ///   for local testing.
    public convenience init(allowsInsecureLoopback: Bool = false) {
        let timeout = Self.defaultTimeout
        self.init(
            session: Self.session(timeout: timeout),
            validatedTimeout: timeout,
            allowsInsecureLoopback: allowsInsecureLoopback
        )
    }

    /// Creates a URL-session feed loader with an explicit timeout.
    ///
    /// - Parameters:
    ///   - timeout: The request and resource timeout, in seconds. The value must be finite and within the signed 64-bit
    ///     nanosecond range, from one nanosecond through about 292 years.
    ///   - allowsInsecureLoopback: Whether HTTP initial, redirect, and final URLs may use a loopback address for local
    ///     testing.
    /// - Throws: ``UpdateError/invalidConfiguration(_:)`` when `timeout` is outside the supported range.
    public convenience init(timeout: TimeInterval, allowsInsecureLoopback: Bool = false) throws {
        guard let timeout = PositiveDuration(timeout) else {
            throw UpdateError.invalidConfiguration(
                "The timeout must be finite and within the supported positive nanosecond range."
            )
        }
        self.init(
            session: Self.session(timeout: timeout),
            validatedTimeout: timeout,
            allowsInsecureLoopback: allowsInsecureLoopback
        )
    }

    convenience init(session: URLSession, timeout: TimeInterval, allowsInsecureLoopback: Bool) throws {
        guard let timeout = PositiveDuration(timeout) else {
            throw UpdateError.invalidConfiguration(
                "The timeout must be finite and within the supported positive nanosecond range."
            )
        }
        self.init(
            session: session,
            validatedTimeout: timeout,
            allowsInsecureLoopback: allowsInsecureLoopback
        )
    }

    private init(session: URLSession, validatedTimeout timeout: PositiveDuration, allowsInsecureLoopback: Bool) {
        self.session = session
        self.timeout = timeout
        self.allowsInsecureLoopback = allowsInsecureLoopback
    }

    /// Loads a feed while validating its URL and enforcing the configured timeout and response-size limit.
    ///
    /// Cancelling the operation or rejecting a response cancels the underlying URL-session task.
    ///
    /// - Parameters:
    ///   - url: The feed URL to validate and load.
    ///   - maximumResponseSize: The largest accepted response, in bytes.
    /// - Returns: The complete feed response body.
    /// - Throws: ``UpdateFeedLoaderError`` when the response is rejected, or a URL-loading error when the request
    ///   fails.
    public func load(from url: URL, maximumResponseSize: Int) async throws -> Data {
        guard maximumResponseSize > 0 else {
            throw UpdateFeedLoaderError.responseTooLarge
        }
        guard UpdateConfiguration.isAllowedFeedURL(
            url,
            allowsInsecureLoopback: allowsInsecureLoopback
        ) else {
            throw UpdateFeedLoaderError.insecureResponseURL
        }
        var request = URLRequest(url: url, timeoutInterval: timeout.seconds)
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let delegate = RedirectValidator(allowsInsecureLoopback: allowsInsecureLoopback)
        let (bytes, response) = try await session.bytes(for: request, delegate: delegate)
        defer { bytes.task.cancel() }
        guard let response = response as? HTTPURLResponse else {
            throw UpdateFeedLoaderError.invalidHTTPStatus(0)
        }
        guard (200...299).contains(response.statusCode) else {
            throw UpdateFeedLoaderError.invalidHTTPStatus(response.statusCode)
        }
        guard let finalURL = response.url,
              UpdateConfiguration.isAllowedFeedURL(
                  finalURL,
                  allowsInsecureLoopback: allowsInsecureLoopback
              )
        else {
            throw UpdateFeedLoaderError.insecureResponseURL
        }
        if response.expectedContentLength > Int64(maximumResponseSize) {
            throw UpdateFeedLoaderError.responseTooLarge
        }

        var data = Data()
        data.reserveCapacity(min(maximumResponseSize, max(0, Int(response.expectedContentLength))))
        for try await byte in bytes {
            try Task.checkCancellation()
            guard data.count < maximumResponseSize else {
                throw UpdateFeedLoaderError.responseTooLarge
            }
            data.append(byte)
        }
        return data
    }

    private static func session(timeout: PositiveDuration) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = timeout.seconds
        configuration.timeoutIntervalForResource = timeout.seconds
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        return URLSession(configuration: configuration)
    }

}

// Safety: The delegate is immutable after initialization. URLSession callbacks only read its Boolean configuration.
private final class RedirectValidator: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    let allowsInsecureLoopback: Bool

    init(allowsInsecureLoopback: Bool) {
        self.allowsInsecureLoopback = allowsInsecureLoopback
    }

    /// Allows redirects only when their destination satisfies the feed URL policy.
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping @Sendable (URLRequest?) -> Void
    ) {
        guard let url = request.url,
              UpdateConfiguration.isAllowedFeedURL(
                  url,
                  allowsInsecureLoopback: allowsInsecureLoopback
              )
        else {
            completionHandler(nil)
            return
        }
        completionHandler(request)
    }
}

enum FeedDecoder {
    static func decode(
        _ data: Data,
        installedBuild: InstalledBuild,
        currentOSVersion: OperatingSystemVersion,
        now: Date
    ) throws -> UpdateRelease? {
        let object: Any
        do {
            object = try JSONSerialization.jsonObject(with: data)
        } catch {
            throw UpdateError.malformedFeed("The response is not valid JSON.")
        }
        do {
            try JSONMemberValidator.validateUniqueObjectMemberNames(in: data)
        } catch JSONMemberValidator.ValidationError.duplicateMemberName {
            throw UpdateError.malformedFeed("The feed contains duplicate object member names.")
        } catch {
            throw UpdateError.malformedFeed("The response is not valid JSON.")
        }

        guard let root = object as? [String: Any] else {
            throw UpdateError.malformedFeed("The feed root must be an object.")
        }
        try requireKeys(root, required: ["schemaVersion", "bundleIdentifier", "release"])

        guard let schemaNumber = root["schemaVersion"] as? NSNumber,
              CFGetTypeID(schemaNumber) != CFBooleanGetTypeID(),
              !CFNumberIsFloatType(schemaNumber),
              let schemaVersion = schemaNumber as? Int
        else {
            throw UpdateError.malformedFeed("schemaVersion must be an integer.")
        }
        guard schemaVersion == 1 else {
            throw UpdateError.unsupportedSchemaVersion(schemaVersion)
        }
        guard let bundleIdentifier = root["bundleIdentifier"] as? String else {
            throw UpdateError.malformedFeed("bundleIdentifier must be a string.")
        }
        guard bundleIdentifier == installedBuild.bundleIdentifier else {
            throw UpdateError.bundleIdentifierMismatch(
                expected: installedBuild.bundleIdentifier,
                actual: bundleIdentifier
            )
        }
        guard !(root["release"] is NSNull) else { return nil }
        guard let releaseObject = root["release"] as? [String: Any] else {
            throw UpdateError.malformedFeed("release must be an object or null.")
        }
        try requireKeys(
            releaseObject,
            required: ["id", "version", "build", "minimumOSVersion", "publishedAt", "expiresAt", "manifestURL"],
            optional: ["releaseNotes"]
        )
        if releaseObject.keys.contains("releaseNotes"), !(releaseObject["releaseNotes"] is String) {
            throw UpdateError.malformedFeed("releaseNotes must be a string when present.")
        }

        let dto: ReleaseDTO
        do {
            let releaseData = try JSONSerialization.data(withJSONObject: releaseObject)
            dto = try JSONDecoder().decode(ReleaseDTO.self, from: releaseData)
        } catch {
            throw UpdateError.malformedFeed("The release contains a missing or incorrectly typed field.")
        }
        let release = try dto.validatedRelease()
        guard installedBuild.isOlder(than: release) else { return nil }
        guard release.expiresAt > now else { throw UpdateError.releaseExpired }

        guard let currentOS = VersionComponents(currentOSVersion) else {
            throw UpdateError.invalidConfiguration("The current operating system version must not be negative.")
        }
        guard release.isCompatible(with: currentOS) else {
            throw UpdateError.incompatibleOperatingSystem(minimumVersion: release.minimumOSVersion)
        }
        return release
    }

    private static func requireKeys(
        _ object: [String: Any],
        required: Set<String>,
        optional: Set<String> = []
    ) throws {
        let actual = Set(object.keys)
        guard required.isSubset(of: actual), actual.isSubset(of: required.union(optional)) else {
            throw UpdateError.malformedFeed("The feed contains missing or unrecognized fields.")
        }
    }
}

private struct ReleaseDTO: Decodable {
    let id: String
    let version: String
    let build: String
    let minimumOSVersion: String
    let publishedAt: String
    let expiresAt: String
    let manifestURL: String
    let releaseNotes: String?

    func validatedRelease() throws -> UpdateRelease {
        guard let publishedAt = WholeSecondUTCDate.parse(publishedAt),
              let expiresAt = WholeSecondUTCDate.parse(expiresAt)
        else {
            throw UpdateError.malformedFeed("Release dates must use whole-second UTC ISO 8601 format.")
        }
        guard let manifestURL = URL(string: manifestURL) else {
            throw UpdateError.insecureManifestURL
        }

        return try UpdateRelease(
            id: id,
            version: version,
            build: build,
            minimumOSVersion: minimumOSVersion,
            publishedAt: publishedAt,
            expiresAt: expiresAt,
            manifestURL: manifestURL,
            releaseNotes: releaseNotes
        )
    }
}

private enum WholeSecondUTCDate {
    static func parse(_ string: String) -> Date? {
        guard string.count == 20,
              string[string.index(string.startIndex, offsetBy: 4)] == "-",
              string[string.index(string.startIndex, offsetBy: 7)] == "-",
              string[string.index(string.startIndex, offsetBy: 10)] == "T",
              string[string.index(string.startIndex, offsetBy: 13)] == ":",
              string[string.index(string.startIndex, offsetBy: 16)] == ":",
              string.last == "Z"
        else { return nil }

        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
        formatter.isLenient = false
        guard let date = formatter.date(from: string), formatter.string(from: date) == string else {
            return nil
        }
        return date
    }
}
