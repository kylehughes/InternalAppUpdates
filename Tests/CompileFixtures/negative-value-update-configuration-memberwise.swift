
import Foundation

let uncheckedConfiguration = UpdateConfiguration(
    feedURL: URL(string: "https://example.com/feed.json")!,
    installedBuild: try InstalledBuild(bundleIdentifier: "com.example.App", version: "1", build: "1"),
    presentation: .manual,
    allowsInsecureLoopback: false,
    pollingInterval: PositiveDuration(1)!
)
