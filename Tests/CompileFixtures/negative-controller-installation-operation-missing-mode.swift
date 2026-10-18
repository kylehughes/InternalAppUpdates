
extension UpdateController {
    private static func compileProofInstallationOperationNeedsMode() throws {
        let release = try compileProofRelease()
        _ = InstallationOperation(token: InstallationToken(rawValue: 0), release: release, task: Task { false })
    }

    private static func compileProofRelease() throws -> UpdateRelease {
        try UpdateRelease(
            id: String(repeating: "a", count: 64), version: "1", build: "1", minimumOSVersion: "16",
            publishedAt: .now, expiresAt: .now.addingTimeInterval(1),
            manifestURL: URL(string: "https://example.com/manifest.plist")!, releaseNotes: nil
        )
    }
}
