@testable import InternalAppUpdates
import Foundation
import Testing

@Suite("Value invariants")
struct ValueInvariantTests {
    private let currentOS = OperatingSystemVersion(majorVersion: 16, minorVersion: 4, patchVersion: 0)

    @Test("Version components always compare as three fixed components")
    func versionComponentComparison() throws {
        let one = try #require(VersionComponents("1"))
        let oneZeroOne = try #require(VersionComponents("1.0.1"))
        let oneOne = try #require(VersionComponents("1.1"))
        let two = try #require(VersionComponents("2"))

        #expect(one < oneZeroOne)
        #expect(oneZeroOne < oneOne)
        #expect(oneOne < two)
    }

    @Test("Version components accept UInt64 bounds and reject malformed digits", arguments: [
        "18446744073709551616",
        "١",
        "1.٢",
        "1．2",
    ])
    func malformedVersionComponents(_ value: String) throws {
        #expect(VersionComponents("18446744073709551615") != nil)
        #expect(VersionComponents(value) == nil)
    }

    @Test("Operating-system components reject each negative position")
    func operatingSystemComponentBoundaries() {
        for invalidVersion in [
            OperatingSystemVersion(majorVersion: -1, minorVersion: 0, patchVersion: 0),
            OperatingSystemVersion(majorVersion: 0, minorVersion: -1, patchVersion: 0),
            OperatingSystemVersion(majorVersion: 0, minorVersion: 0, patchVersion: -1),
        ] {
            #expect(VersionComponents(invalidVersion) == nil)
        }

        let maximum = OperatingSystemVersion(majorVersion: .max, minorVersion: .max, patchVersion: .max)
        #expect(VersionComponents(maximum) != nil)
    }

    @Test("Positive durations are created only through their checked boundary")
    func positiveDurationBoundaries() {
        let largest = TimeInterval(Int64.max / 1_000_000_000)

        #expect(PositiveDuration(0) == nil)
        #expect(PositiveDuration(-1) == nil)
        #expect(PositiveDuration(.nan) == nil)
        #expect(PositiveDuration(.infinity) == nil)
        #expect(PositiveDuration(0.000_000_001.nextDown) == nil)
        #expect(PositiveDuration(largest.nextUp) == nil)
        #expect(PositiveDuration(0.000_000_001)?.seconds == 0.000_000_001)
        #expect(PositiveDuration(largest)?.seconds == largest)
        #expect(PositiveDuration.fifteenSeconds.seconds == 15)
        #expect(PositiveDuration.fifteenSeconds.duration == .seconds(15))
    }

    @Test("Installed build validation preserves immutable public values")
    func installedBuildValues() throws {
        let installedBuild = try InstalledBuild(bundleIdentifier: "com.example.App", version: "1.0", build: "2")

        #expect(installedBuild.bundleIdentifier == "com.example.App")
        #expect(installedBuild.version == "1.0")
        #expect(installedBuild.build == "2")
        #expect(throws: UpdateError.self) {
            try InstalledBuild(bundleIdentifier: "", version: "1.0", build: "2")
        }
        #expect(throws: UpdateError.self) {
            try InstalledBuild(bundleIdentifier: "com.example.App", version: "1.0", build: "2.beta")
        }
    }

    @Test("Bundle metadata delegates to installed-build validation")
    func bundleMetadata() throws {
        let validBundle = try makeBundle(info: [
            "CFBundleIdentifier": "com.example.App",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "2",
        ])
        defer { try? FileManager.default.removeItem(at: validBundle.url) }
        let installedBuild = try InstalledBuild(bundle: validBundle.bundle)
        #expect(installedBuild.bundleIdentifier == "com.example.App")
        #expect(installedBuild.version == "1.0")
        #expect(installedBuild.build == "2")

        for missingMetadata in [
            [
                "CFBundleShortVersionString": "1.0",
                "CFBundleVersion": "2",
            ],
            [
                "CFBundleIdentifier": "com.example.App",
                "CFBundleVersion": "2",
            ],
            [
                "CFBundleIdentifier": "com.example.App",
                "CFBundleShortVersionString": "1.0",
            ],
        ] {
            let bundle = try makeBundle(info: missingMetadata)
            defer { try? FileManager.default.removeItem(at: bundle.url) }
            #expect(throws: UpdateError.self) {
                try InstalledBuild(bundle: bundle.bundle)
            }
        }

        let malformedBuildBundle = try makeBundle(info: [
            "CFBundleIdentifier": "com.example.App",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "2.beta",
        ])
        defer { try? FileManager.default.removeItem(at: malformedBuildBundle.url) }
        #expect(throws: UpdateError.self) {
            try InstalledBuild(bundle: malformedBuildBundle.bundle)
        }
    }

    @Test("Release digest validation rejects invalid fixed forms", arguments: [
        String(repeating: "a", count: 63),
        String(repeating: "a", count: 65),
        String(repeating: "A", count: 64),
        String(repeating: "g", count: 64),
        String(repeating: "é", count: 64),
    ])
    func invalidReleaseDigest(_ id: String) {
        #expect(throws: UpdateError.self) {
            try makeRelease(id: id)
        }
    }

    @Test("Release dates require finite ordered values")
    func releaseDateBoundaries() throws {
        let validPublishedAt = Date(timeIntervalSinceReferenceDate: 1)
        let validExpiresAt = Date(timeIntervalSinceReferenceDate: 2)
        let release = try makeRelease(publishedAt: validPublishedAt, expiresAt: validExpiresAt)
        #expect(release.publishedAt == validPublishedAt)
        #expect(release.expiresAt == validExpiresAt)

        for invalidDate in [
            Date(timeIntervalSinceReferenceDate: .nan),
            Date(timeIntervalSinceReferenceDate: .infinity),
            Date(timeIntervalSinceReferenceDate: -.infinity),
        ] {
            #expect(throws: UpdateError.self) {
                try makeRelease(publishedAt: invalidDate, expiresAt: validExpiresAt)
            }
            #expect(throws: UpdateError.self) {
                try makeRelease(publishedAt: validPublishedAt, expiresAt: invalidDate)
            }
        }
        #expect(throws: UpdateError.self) {
            try makeRelease(publishedAt: validPublishedAt, expiresAt: validPublishedAt)
        }
        #expect(throws: UpdateError.self) {
            try makeRelease(publishedAt: validExpiresAt, expiresAt: validPublishedAt)
        }
    }

    @Test("Feed and manifest URLs preserve their distinct validated policies")
    func URLPolicies() throws {
        let installedBuild = try InstalledBuild(bundleIdentifier: "com.example.App", version: "1.0", build: "1")
        for url in [
            URL(string: "https://user:password@updates.example.com/feed.json")!,
            URL(string: "https://updates.example.com/feed.json#fragment")!,
            URL(string: "https:/feed.json")!,
        ] {
            #expect(throws: UpdateError.self) {
                try UpdateConfiguration(feedURL: url, installedBuild: installedBuild)
            }
            #expect(throws: UpdateError.insecureManifestURL) {
                try makeRelease(manifestURL: url)
            }
        }

        let localFeed = URL(string: "http://127.0.0.1:8080/feed.json")!
        #expect(throws: Never.self) {
            try UpdateConfiguration(
                feedURL: localFeed,
                installedBuild: installedBuild,
                allowsInsecureLoopback: true
            )
        }
        #expect(throws: UpdateError.self) {
            try UpdateConfiguration(feedURL: localFeed, installedBuild: installedBuild)
        }
        #expect(throws: UpdateError.self) {
            try UpdateConfiguration(
                feedURL: URL(string: "http://updates.example.com/feed.json")!,
                installedBuild: installedBuild,
                allowsInsecureLoopback: true
            )
        }
        #expect(throws: UpdateError.insecureManifestURL) {
            try makeRelease(manifestURL: localFeed)
        }
    }

    @Test("Direct and decoded releases share validation boundaries")
    func directAndDecodedReleaseValidation() throws {
        for releaseObject in [
            makeReleaseObject(id: String(repeating: "A", count: 64)),
            makeReleaseObject(version: "1.beta"),
            makeReleaseObject(publishedAt: "2027-01-01T00:00:00Z", expiresAt: "2026-10-14T00:00:00Z"),
        ] {
            #expect(throws: UpdateError.self) {
                try FeedDecoder.decode(
                    try makeFeed(release: releaseObject),
                    installedBuild: try InstalledBuild(bundleIdentifier: "com.example.App", version: "1.0", build: "1"),
                    currentOSVersion: currentOS,
                    now: fixedNow
                )
            }
        }

        #expect(throws: UpdateError.self) {
            try makeRelease(id: String(repeating: "A", count: 64))
        }
        #expect(throws: UpdateError.self) {
            try makeRelease(version: "1.beta")
        }
        #expect(throws: UpdateError.self) {
            try makeRelease(
                publishedAt: Date(timeIntervalSince1970: 1_800_000_000),
                expiresAt: Date(timeIntervalSince1970: 1_792_000_000)
            )
        }
    }

    private func makeRelease(
        id: String = releaseID,
        version: String = "1.1",
        build: String = "1",
        minimumOSVersion: String = "16.0",
        publishedAt: Date = fixedNow,
        expiresAt: Date = Date(timeIntervalSince1970: 1_800_000_000),
        manifestURL: URL = URL(string: "https://updates.example.com/manifest.plist")!
    ) throws -> UpdateRelease {
        try UpdateRelease(
            id: id,
            version: version,
            build: build,
            minimumOSVersion: minimumOSVersion,
            publishedAt: publishedAt,
            expiresAt: expiresAt,
            manifestURL: manifestURL,
            releaseNotes: nil
        )
    }

    private func makeBundle(info: [String: Any]) throws -> (url: URL, bundle: Bundle) {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension("bundle")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        let infoURL = url.appendingPathComponent("Info.plist")
        let data = try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
        try data.write(to: infoURL)
        guard let bundle = Bundle(path: url.path) else {
            throw CocoaError(.fileNoSuchFile)
        }
        return (url, bundle)
    }
}
