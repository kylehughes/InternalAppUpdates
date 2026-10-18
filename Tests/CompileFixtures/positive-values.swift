
import Foundation

func compileProofPositiveValues() throws {
    let version = VersionComponents("1.0.0")!
    let duration = PositiveDuration(1)!
    let build = try InstalledBuild(bundleIdentifier: "com.example.App", version: "1", build: "1")
    let release = try UpdateRelease(
        id: String(repeating: "a", count: 64),
        version: "1",
        build: "1",
        minimumOSVersion: "16",
        publishedAt: .now,
        expiresAt: .now.addingTimeInterval(1),
        manifestURL: URL(string: "https://example.com/manifest.plist")!,
        releaseNotes: nil
    )
    let configuration = try UpdateConfiguration(
        feedURL: URL(string: "https://example.com/feed.json")!,
        installedBuild: build,
        checkInterval: duration.seconds
    )
    _ = (version, duration, release, configuration.checkInterval)
}
