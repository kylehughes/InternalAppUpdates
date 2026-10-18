@testable import InternalAppUpdates
import Foundation
import Testing

@Suite("Feed validation")
struct FeedValidationTests {
    let currentOS = OperatingSystemVersion(majorVersion: 16, minorVersion: 4, patchVersion: 0)

    @Test("A Boolean schema version is rejected")
    func booleanSchemaVersion() throws {
        let data = Data(
            #"{"schemaVersion":true,"bundleIdentifier":"com.example.App","release":null}"#.utf8
        )

        #expect(throws: UpdateError.self) {
            try FeedDecoder.decode(
                data,
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test("Expiration must follow publication")
    func chronologicalDates() throws {
        #expect(throws: UpdateError.self) {
            try FeedDecoder.decode(
                makeFeed(
                    release: makeReleaseObject(
                        publishedAt: "2027-01-01T00:00:00Z",
                        expiresAt: "2026-10-14T00:00:00Z"
                    )
                ),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test("A decimal schema version is rejected")
    func decimalSchemaVersion() throws {
        let data = Data(
            #"{"schemaVersion":1.0,"bundleIdentifier":"com.example.App","release":null}"#.utf8
        )

        #expect(throws: UpdateError.self) {
            try FeedDecoder.decode(
                data,
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test(arguments: [
        (installedVersion: "1.1", installedBuild: "1", offeredVersion: "1.0", offeredBuild: "99"),
        (installedVersion: "1.0", installedBuild: "2", offeredVersion: "1.0.0", offeredBuild: "2.0"),
        (installedVersion: "1.0", installedBuild: "3", offeredVersion: "1", offeredBuild: "2"),
    ])
    func downgradeOrInstalledIdentityIsNotOffered(
        argument: (installedVersion: String, installedBuild: String, offeredVersion: String, offeredBuild: String)
    ) throws {
        let release = try FeedDecoder.decode(
            makeFeed(release: makeReleaseObject(version: argument.offeredVersion, build: argument.offeredBuild)),
            installedBuild: makeInstalledBuild(version: argument.installedVersion, build: argument.installedBuild),
            currentOSVersion: currentOS,
            now: fixedNow
        )
        #expect(release == nil)
    }
    @Test("Expired releases are rejected")
    func expired() throws {
        #expect(throws: UpdateError.releaseExpired) {
            try FeedDecoder.decode(
                makeFeed(
                    release: makeReleaseObject(
                        publishedAt: "2025-01-01T00:00:00Z",
                        expiresAt: "2026-01-01T00:00:00Z"
                    )
                ),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test("An expired release that is not newer is ignored", arguments: ["1.0", "0.9"])
    func expiredOutdatedRelease(offeredVersion: String) throws {
        let release = try FeedDecoder.decode(
            makeFeed(
                release: makeReleaseObject(
                    version: offeredVersion,
                    publishedAt: "2025-01-01T00:00:00Z",
                    expiresAt: "2026-01-01T00:00:00Z"
                )
            ),
            installedBuild: makeInstalledBuild(),
            currentOSVersion: currentOS,
            now: fixedNow
        )

        #expect(release == nil)
    }

    @Test("A release requiring a newer OS is rejected")
    func incompatibleOS() throws {
        #expect(throws: UpdateError.incompatibleOperatingSystem(minimumVersion: "17.0")) {
            try FeedDecoder.decode(
                makeFeed(release: makeReleaseObject(minimumOSVersion: "17.0")),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test("An OS-incompatible release that is not newer is ignored", arguments: ["1.0", "0.9"])
    func incompatibleOutdatedRelease(offeredVersion: String) throws {
        let release = try FeedDecoder.decode(
            makeFeed(release: makeReleaseObject(version: offeredVersion, minimumOSVersion: "17.0")),
            installedBuild: makeInstalledBuild(),
            currentOSVersion: currentOS,
            now: fixedNow
        )

        #expect(release == nil)
    }

    @Test("A negative current OS version is rejected")
    func invalidCurrentOSVersion() throws {
        let feed = try makeFeed()
        let invalidVersion = OperatingSystemVersion(majorVersion: -1, minorVersion: 0, patchVersion: 0)

        #expect(
            throws: UpdateError.invalidConfiguration(
                "The current operating system version must not be negative."
            )
        ) {
            try FeedDecoder.decode(
                feed,
                installedBuild: makeInstalledBuild(),
                currentOSVersion: invalidVersion,
                now: fixedNow
            )
        }
    }

    @Test(arguments: [
        "http://updates.example.com/manifest.plist",
        "https:manifest.plist",
        "https://user@updates.example.com/manifest.plist",
        "https://updates.example.com/manifest.plist#fragment",
    ])
    func invalidManifestURL(_ url: String) throws {
        #expect(throws: UpdateError.insecureManifestURL) {
            try FeedDecoder.decode(
                makeFeed(release: makeReleaseObject(manifestURL: url)),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test("A malformed release that is not newer is still rejected")
    func malformedOutdatedRelease() throws {
        #expect(throws: UpdateError.self) {
            try FeedDecoder.decode(
                makeFeed(release: makeReleaseObject(version: "1.0", manifestURL: "http://updates.example.com/manifest.plist")),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test("Equivalent padded marketing versions compare by build")
    func paddedVersionComparison() throws {
        let decoded = try FeedDecoder.decode(
            makeFeed(release: makeReleaseObject(version: "1.0", build: "3")),
            installedBuild: makeInstalledBuild(version: "1", build: "2"),
            currentOSVersion: currentOS,
            now: fixedNow
        )
        let release = try #require(decoded)
        #expect(release.build == "3")
    }

    @Test("Dates require whole-second UTC form")
    func strictDates() throws {
        #expect(throws: UpdateError.self) {
            try FeedDecoder.decode(
                makeFeed(release: makeReleaseObject(publishedAt: "2026-10-14T00:00:00.000Z")),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test("Wire dates reject noncanonical digits and calendar normalization", arguments: [
        "٢٠٢٦-١٠-١٧T١٢:٣٤:٥٦Z",
        "２０２６-１０-１７T１２:３４:５６Z",
        "2026-10-17T12:34:5٦Z",
        "2026-10-17T24:00:00Z",
        "2026-02-30T12:34:56Z",
        "2026-10-17t12:34:56Z",
        "2026-10-17T12:34:56z",
    ])
    func canonicalWireDates(_ date: String) throws {
        for field in ["publishedAt", "expiresAt"] {
            var release = makeReleaseObject()
            release[field] = date
            #expect(throws: UpdateError.malformedFeed("Release dates must use whole-second UTC ISO 8601 format.")) {
                try FeedDecoder.decode(
                    makeFeed(release: release),
                    installedBuild: makeInstalledBuild(),
                    currentOSVersion: currentOS,
                    now: fixedNow
                )
            }
        }
    }

    @Test("Unknown fields are rejected")
    func unknownField() throws {
        #expect(throws: UpdateError.self) {
            try FeedDecoder.decode(
                makeFeed(extraRoot: ["future": true]),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test("Unsupported schemas are rejected")
    func unsupportedSchema() throws {
        #expect(throws: UpdateError.unsupportedSchemaVersion(2)) {
            try FeedDecoder.decode(
                makeFeed(schemaVersion: 2),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

    @Test("A valid newer release is decoded")
    func validRelease() throws {
        let release = try #require(
            FeedDecoder.decode(
                try makeFeed(),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        )

        #expect(release.id == releaseID)
        #expect(release.version == "1.1")
        #expect(release.manifestURL.scheme == "https")
    }

    @Test("A null release withdraws the offer")
    func withdrawnRelease() throws {
        let release = try FeedDecoder.decode(
            makeFeed(release: NSNull()),
            installedBuild: makeInstalledBuild(),
            currentOSVersion: currentOS,
            now: fixedNow
        )
        #expect(release == nil)
    }

    @Test("A feed for another bundle is rejected")
    func wrongBundle() throws {
        #expect(
            throws: UpdateError.bundleIdentifierMismatch(
                expected: "com.example.App",
                actual: "com.example.Other"
            )
        ) {
            try FeedDecoder.decode(
                makeFeed(bundleIdentifier: "com.example.Other"),
                installedBuild: makeInstalledBuild(),
                currentOSVersion: currentOS,
                now: fixedNow
            )
        }
    }

}

@Suite("Model validation")
struct ModelValidationTests {
    @Test("Equivalent version spellings have equal components")
    func equivalentVersionComponents() throws {
        let oneComponent = try #require(VersionComponents("1"))
        let twoComponents = try #require(VersionComponents("1.0"))
        let threeComponents = try #require(VersionComponents("1.0.0"))

        #expect(oneComponent == twoComponents)
        #expect(twoComponents == threeComponents)
        #expect(!(oneComponent < threeComponents))
        #expect(!(threeComponents < oneComponent))
    }

    @Test("Direct release construction rejects an invalid digest")
    func invalidReleaseID() {
        #expect(throws: UpdateError.self) {
            try makeRelease(id: "not-a-digest")
        }
    }

    @Test("Direct release construction rejects malformed versions")
    func malformedReleaseVersions() {
        #expect(throws: UpdateError.self) {
            try makeRelease(version: "1.beta")
        }
        #expect(throws: UpdateError.self) {
            try makeRelease(build: "1.2.3.4")
        }
        #expect(throws: UpdateError.self) {
            try makeRelease(minimumOSVersion: "16..0")
        }
    }

    @Test("Direct release construction rejects nonfinite and unordered dates")
    func releaseDates() {
        #expect(throws: UpdateError.self) {
            try makeRelease(publishedAt: Date(timeIntervalSinceReferenceDate: .infinity))
        }
        #expect(throws: UpdateError.self) {
            try makeRelease(publishedAt: fixedNow, expiresAt: fixedNow)
        }
    }

    @Test("Release equality preserves public raw version spelling")
    func releaseEqualityPreservesRawVersion() throws {
        let shortVersion = try makeRelease(version: "1")
        let paddedVersion = try makeRelease(version: "1.0")

        #expect(shortVersion != paddedVersion)
    }

    @Test("Direct release construction rejects a credentialed manifest URL")
    func releaseManifestURL() {
        #expect(throws: UpdateError.insecureManifestURL) {
            try makeRelease(manifestURL: URL(string: "https://user@updates.example.com/manifest.plist")!)
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
}

@Suite("Configuration validation")
struct ConfigurationValidationTests {
    @Test("Check intervals fit the supported nanosecond range")
    func checkIntervalRange() throws {
        let minimum: TimeInterval = 1 / 1_000_000_000
        let maximum = TimeInterval(Int64.max / 1_000_000_000)
        let minimumConfiguration = try UpdateConfiguration(
            feedURL: URL(string: "https://updates.example.com/feed.json")!,
            installedBuild: makeInstalledBuild(),
            checkInterval: minimum
        )
        let maximumConfiguration = try UpdateConfiguration(
            feedURL: URL(string: "https://updates.example.com/feed.json")!,
            installedBuild: makeInstalledBuild(),
            checkInterval: maximum
        )

        #expect(minimumConfiguration.checkInterval == minimum)
        #expect(minimumConfiguration.checkDelay > .zero)
        #expect(maximumConfiguration.checkInterval == maximum)
        #expect(maximumConfiguration.checkDelay == .seconds(maximum))
        for invalidInterval in [
            .leastNonzeroMagnitude,
            1 / 10_000_000_000,
            0,
            -1,
            .infinity,
            .nan,
            maximum.nextUp,
        ] {
            #expect(throws: UpdateError.self) {
                try UpdateConfiguration(
                    feedURL: URL(string: "https://updates.example.com/feed.json")!,
                    installedBuild: makeInstalledBuild(),
                    checkInterval: invalidInterval
                )
            }
        }
    }

    @Test("HTTP loopback requires explicit testing configuration")
    func explicitLoopback() throws {
        let url = URL(string: "http://127.0.0.1:8080/feed.json")!
        #expect(throws: UpdateError.self) {
            try UpdateConfiguration(feedURL: url, installedBuild: makeInstalledBuild())
        }
        #expect(throws: Never.self) {
            try UpdateConfiguration(
                feedURL: url,
                installedBuild: makeInstalledBuild(),
                allowsInsecureLoopback: true
            )
        }
    }

    @Test(arguments: [
        "http://updates.example.com/feed.json",
        "https:feed.json",
        "https://user@updates.example.com/feed.json",
        "https://updates.example.com/feed.json#fragment",
    ])
    func invalidFeedURL(_ url: String) throws {
        #expect(throws: UpdateError.self) {
            try UpdateConfiguration(
                feedURL: URL(string: url)!,
                installedBuild: makeInstalledBuild()
            )
        }
    }

    @Test(arguments: ["", "1.2.3.4", "1..2", "1.beta", "-1"])
    func malformedInstalledVersion(_ version: String) {
        #expect(throws: UpdateError.self) {
            try InstalledBuild(bundleIdentifier: "com.example.App", version: version, build: "1")
        }
    }
}
