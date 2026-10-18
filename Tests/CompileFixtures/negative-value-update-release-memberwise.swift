
import Foundation

let uncheckedRelease = UpdateRelease(
    id: String(repeating: "a", count: 64),
    version: "1",
    build: "1",
    minimumOSVersion: "16",
    publishedAt: .now,
    expiresAt: .now,
    manifestURL: URL(string: "https://example.com/manifest.plist")!,
    releaseNotes: nil,
    buildComponents: VersionComponents("1")!,
    minimumOSVersionComponents: VersionComponents("16")!,
    versionComponents: VersionComponents("1")!
)
