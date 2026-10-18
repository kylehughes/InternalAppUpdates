import Foundation

/// An error produced while configuring or using Internal App Updates.
public enum UpdateError: Error, Equatable, Sendable {
    /// A caller-provided value is invalid.
    case invalidConfiguration(String)
    /// The feed could not be loaded.
    case feedLoadingFailed(String)
    /// The feed is not valid Internal App Updates JSON.
    case malformedFeed(String)
    /// The feed uses an unsupported schema version.
    case unsupportedSchemaVersion(Int)
    /// The feed targets a different application.
    case bundleIdentifierMismatch(expected: String, actual: String)
    /// The offered release has expired.
    case releaseExpired
    /// The offered release requires a newer operating system.
    case incompatibleOperatingSystem(minimumVersion: String)
    /// The manifest URL is not an allowed credential-free HTTPS URL.
    case insecureManifestURL
}

extension UpdateError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case let .invalidConfiguration(message): message
        case let .feedLoadingFailed(message): message
        case let .malformedFeed(message): message
        case let .unsupportedSchemaVersion(version):
            "Unsupported feed schema version: \(version)."
        case let .bundleIdentifierMismatch(expected, actual):
            "The feed targets \(actual), but the app uses \(expected)."
        case .releaseExpired:
            "The available release has expired."
        case let .incompatibleOperatingSystem(minimumVersion):
            "The available release requires operating system version \(minimumVersion) or later."
        case .insecureManifestURL:
            "The release manifest URL must use HTTPS, include a host, and omit credentials and fragments."
        }
    }
}

/// The installed application identity used when evaluating releases.
public struct InstalledBuild: Equatable, Sendable {
    /// The application's bundle identifier.
    public let bundleIdentifier: String
    /// The installed marketing version.
    public let version: String
    /// The installed build number.
    public let build: String

    fileprivate let buildComponents: VersionComponents
    fileprivate let versionComponents: VersionComponents

    /// Creates an installed build from explicit application metadata.
    ///
    /// - Throws: ``UpdateError/invalidConfiguration(_:)`` when a value is empty or a version is malformed.
    public init(bundleIdentifier: String, version: String, build: String) throws {
        guard !bundleIdentifier.isEmpty else {
            throw UpdateError.invalidConfiguration("The bundle identifier must not be empty.")
        }
        guard let versionComponents = VersionComponents(version) else {
            throw UpdateError.invalidConfiguration("The version must contain one to three numeric components.")
        }
        guard let buildComponents = VersionComponents(build) else {
            throw UpdateError.invalidConfiguration("The build must contain one to three numeric components.")
        }

        self.bundleIdentifier = bundleIdentifier
        self.version = version
        self.build = build
        self.buildComponents = buildComponents
        self.versionComponents = versionComponents
    }

    /// Creates an installed build from an application bundle.
    ///
    /// - Parameter bundle: The bundle containing the application's identity and version metadata.
    /// - Throws: ``UpdateError/invalidConfiguration(_:)`` when required bundle metadata is absent or malformed.
    public init(bundle: Bundle = .main) throws {
        guard let bundleIdentifier = bundle.bundleIdentifier else {
            throw UpdateError.invalidConfiguration("The bundle does not contain a bundle identifier.")
        }
        guard let version = bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String else {
            throw UpdateError.invalidConfiguration("The bundle does not contain a marketing version.")
        }
        guard let build = bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String else {
            throw UpdateError.invalidConfiguration("The bundle does not contain a build number.")
        }

        try self.init(bundleIdentifier: bundleIdentifier, version: version, build: build)
    }

    func isOlder(than release: UpdateRelease) -> Bool {
        if versionComponents < release.versionComponents {
            return true
        }
        if release.versionComponents < versionComponents {
            return false
        }
        return buildComponents < release.buildComponents
    }
}

/// A validated release offered by an update feed.
public struct UpdateRelease: Equatable, Sendable {
    /// The lowercase SHA-256 digest of the IPA.
    public let id: String
    /// The release's marketing version.
    public let version: String
    /// The release's build number.
    public let build: String
    /// The oldest operating system version supported by the release.
    public let minimumOSVersion: String
    /// The time at which the release was published.
    public let publishedAt: Date
    /// The time after which the release must no longer be installed.
    public let expiresAt: Date
    /// The credential-free HTTPS manifest used for the iOS installation handoff.
    public let manifestURL: URL
    /// Optional release notes supplied by the publisher.
    public let releaseNotes: String?

    fileprivate let buildComponents: VersionComponents
    fileprivate let minimumOSVersionComponents: VersionComponents
    fileprivate let versionComponents: VersionComponents

    init(
        id: String,
        version: String,
        build: String,
        minimumOSVersion: String,
        publishedAt: Date,
        expiresAt: Date,
        manifestURL: URL,
        releaseNotes: String?
    ) throws {
        guard id.count == 64,
              id.allSatisfy({ $0.isASCII && ($0.isNumber || ("a"..."f").contains(String($0))) })
        else {
            throw UpdateError.malformedFeed("The release ID must be a 64-character lowercase SHA-256 digest.")
        }
        guard let versionComponents = VersionComponents(version) else {
            throw UpdateError.malformedFeed("The release version is malformed.")
        }
        guard let buildComponents = VersionComponents(build) else {
            throw UpdateError.malformedFeed("The release build is malformed.")
        }
        guard let minimumOSVersionComponents = VersionComponents(minimumOSVersion) else {
            throw UpdateError.malformedFeed("The minimum operating system version is malformed.")
        }
        guard publishedAt.timeIntervalSinceReferenceDate.isFinite,
              expiresAt.timeIntervalSinceReferenceDate.isFinite
        else {
            throw UpdateError.malformedFeed("Release dates must be finite.")
        }
        guard publishedAt < expiresAt else {
            throw UpdateError.malformedFeed("The release expiration must follow its publication date.")
        }
        guard UpdateConfiguration.isAllowedFeedURL(manifestURL, allowsInsecureLoopback: false) else {
            throw UpdateError.insecureManifestURL
        }

        self.id = id
        self.version = version
        self.build = build
        self.minimumOSVersion = minimumOSVersion
        self.publishedAt = publishedAt
        self.expiresAt = expiresAt
        self.manifestURL = manifestURL
        self.releaseNotes = releaseNotes
        self.buildComponents = buildComponents
        self.minimumOSVersionComponents = minimumOSVersionComponents
        self.versionComponents = versionComponents
    }

    func isCompatible(with operatingSystemVersion: VersionComponents) -> Bool {
        minimumOSVersionComponents <= operatingSystemVersion
    }
}

/// A summary of the controller's check state and installer handoff activity.
///
/// An active handoff takes precedence. Use ``UpdateState/check`` to inspect checking independently.
public enum UpdateStatus: Equatable, Sendable {
    /// No update operation has run.
    case idle
    /// The controller is checking the feed.
    case checking
    /// A newer compatible release is available.
    case updateAvailable
    /// The feed contains no newer compatible release.
    case upToDate
    /// The controller is handing the manifest URL to the operating system.
    case openingInstaller
    /// The update check failed validation or loading.
    case failed(UpdateError)
}

/// The policy used to present an available release.
public enum UpdatePresentationPolicy: Equatable, Sendable {
    /// The host application initiates installation from its own interface.
    case manual
    /// The controller opens the native iOS installation prompt once per release.
    case automatic
}

/// Configuration for an update controller.
public struct UpdateConfiguration: Sendable {
    /// The allowed JSON feed URL: HTTPS, or HTTP loopback when local testing is explicitly enabled.
    public let feedURL: URL
    /// The application build against which releases are compared.
    public let installedBuild: InstalledBuild
    /// The initial presentation policy.
    public let presentation: UpdatePresentationPolicy
    /// Whether an HTTP loopback feed is accepted for local testing.
    public let allowsInsecureLoopback: Bool

    private let pollingInterval: PositiveDuration

    /// Creates update-controller configuration.
    ///
    /// - Parameters:
    ///   - feedURL: The update feed location. The URL must include a host and omit credentials and fragments.
    ///   - installedBuild: The currently installed application build.
    ///   - presentation: The initial presentation policy.
    ///   - checkInterval: The interval between foreground checks, in seconds. The value must be finite and within the
    ///     signed 64-bit nanosecond range, from one nanosecond through about 292 years.
    ///   - allowsInsecureLoopback: Whether to permit HTTP for localhost and numeric loopback addresses.
    /// - Throws: ``UpdateError/invalidConfiguration(_:)`` for an insecure feed or invalid interval.
    public init(
        feedURL: URL,
        installedBuild: InstalledBuild,
        presentation: UpdatePresentationPolicy = .manual,
        checkInterval: TimeInterval = 60,
        allowsInsecureLoopback: Bool = false
    ) throws {
        guard Self.isAllowedFeedURL(feedURL, allowsInsecureLoopback: allowsInsecureLoopback) else {
            throw UpdateError.invalidConfiguration(
                "The feed URL must use HTTPS, include a host, and omit credentials and fragments. HTTP is available "
                    + "only for loopback testing when explicitly enabled."
            )
        }
        guard let pollingInterval = PositiveDuration(checkInterval) else {
            throw UpdateError.invalidConfiguration(
                "The check interval must be finite and within the supported positive nanosecond range."
            )
        }

        self.feedURL = feedURL
        self.installedBuild = installedBuild
        self.presentation = presentation
        self.allowsInsecureLoopback = allowsInsecureLoopback
        self.pollingInterval = pollingInterval
    }

    /// The interval between checks while the controller is active, in seconds.
    public var checkInterval: TimeInterval {
        pollingInterval.seconds
    }

    var checkDelay: Duration {
        pollingInterval.duration
    }

    static func isAllowedFeedURL(_ url: URL, allowsInsecureLoopback: Bool) -> Bool {
        guard url.user == nil,
              url.password == nil,
              url.fragment == nil,
              let host = url.host?.lowercased(),
              !host.isEmpty
        else { return false }
        if url.scheme?.lowercased() == "https" { return true }
        guard allowsInsecureLoopback, url.scheme?.lowercased() == "http" else {
            return false
        }
        return host == "localhost" || host == "127.0.0.1" || host == "::1"
    }
}

struct VersionComponents: Comparable, Sendable {
    private let major: UInt64
    private let minor: UInt64
    private let patch: UInt64

    init?(_ string: String) {
        let parts = string.split(separator: ".", omittingEmptySubsequences: false)
        guard (1...3).contains(parts.count) else { return nil }
        var values: [UInt64] = []
        for part in parts {
            guard !part.isEmpty,
                  part.allSatisfy({ $0.isASCII && $0.isNumber }),
                  let value = UInt64(part)
            else { return nil }
            values.append(value)
        }

        switch values.count {
        case 1:
            major = values[0]
            minor = 0
            patch = 0
        case 2:
            major = values[0]
            minor = values[1]
            patch = 0
        case 3:
            major = values[0]
            minor = values[1]
            patch = values[2]
        default:
            return nil
        }
    }

    init?(_ operatingSystemVersion: OperatingSystemVersion) {
        guard operatingSystemVersion.majorVersion >= 0,
              operatingSystemVersion.minorVersion >= 0,
              operatingSystemVersion.patchVersion >= 0
        else {
            return nil
        }

        major = UInt64(operatingSystemVersion.majorVersion)
        minor = UInt64(operatingSystemVersion.minorVersion)
        patch = UInt64(operatingSystemVersion.patchVersion)
    }

    static func < (lhs: Self, rhs: Self) -> Bool {
        if lhs.major != rhs.major { return lhs.major < rhs.major }
        if lhs.minor != rhs.minor { return lhs.minor < rhs.minor }
        return lhs.patch < rhs.patch
    }
}
