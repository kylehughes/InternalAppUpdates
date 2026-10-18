
extension UpdateController {
    private static func compileProofPositive() throws {
        let lifecycleToken = LifecycleToken(rawValue: 1)
        let checkToken = CheckToken(rawValue: 1)
        let installationToken = InstallationToken(rawValue: 1)
        let checkTask = Task<Void, Never> {}
        let pollingTask = Task<Void, Never> {}
        let installationTask = Task<Bool, Never> { false }
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

        let checkOperation = CheckOperation(
            token: checkToken,
            lifecycle: lifecycleToken,
            previous: .idle,
            task: checkTask
        )
        let installationOperation = InstallationOperation(
            token: installationToken,
            release: release,
            mode: .automatic(lifecycleToken),
            task: installationTask
        )
        let pollingOperation = PollingOperation(token: lifecycleToken, task: pollingTask)

        let check: Check = .running(checkOperation)
        let installation: Installation = .running(installationOperation)
        let lifecycle: Lifecycle = .active(pollingOperation)
        _ = (check, installation, lifecycle)
    }
}
