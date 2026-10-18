
extension UpdateController {
    private static func compileProofInstallationOperationTokenDomain() throws {
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
        _ = InstallationOperation(token: CheckToken(rawValue: 0), release: release, mode: .manual, task: Task { false })
    }
}
