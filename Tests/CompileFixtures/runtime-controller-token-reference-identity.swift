extension UpdateController {
    static func runtimeProofTokenReferenceIdentity() async {
        let firstCheck = CheckToken(rawValue: 7)
        let firstCheckAlias = firstCheck
        let secondCheck = CheckToken(rawValue: 7)
        precondition(firstCheck.rawValue == secondCheck.rawValue)
        precondition(firstCheck == firstCheckAlias)
        precondition(firstCheck != secondCheck)

        let firstInstallation = InstallationToken(rawValue: 7)
        let firstInstallationAlias = firstInstallation
        let secondInstallation = InstallationToken(rawValue: 7)
        precondition(firstInstallation.rawValue == secondInstallation.rawValue)
        precondition(firstInstallation == firstInstallationAlias)
        precondition(firstInstallation != secondInstallation)

        let firstLifecycle = LifecycleToken(rawValue: 7)
        let firstLifecycleAlias = firstLifecycle
        let secondLifecycle = LifecycleToken(rawValue: 7)
        precondition(firstLifecycle.rawValue == secondLifecycle.rawValue)
        precondition(firstLifecycle == firstLifecycleAlias)
        precondition(firstLifecycle != secondLifecycle)

        let configuration = try! UpdateConfiguration(
            feedURL: URL(string: "https://example.com/feed.json")!,
            installedBuild: try! InstalledBuild(bundleIdentifier: "com.example.App", version: "1", build: "1"),
            checkInterval: 60
        )
        let controller = UpdateController(configuration: configuration)
        let currentCheck = CheckToken(rawValue: 7)
        let checkTask = Task {}
        controller.check = .running(CheckOperation(
            token: currentCheck,
            lifecycle: controller.lifecycle.token,
            previous: .idle,
            task: checkTask
        ))
        controller.completeCheck(token: firstCheck, result: .success(nil), wasCancelled: false)
        guard case let .running(check) = controller.check else { preconditionFailure() }
        precondition(check.token == currentCheck)

        let release = try! UpdateRelease(
            id: String(repeating: "a", count: 64), version: "1", build: "1", minimumOSVersion: "16",
            publishedAt: .now, expiresAt: .distantFuture,
            manifestURL: URL(string: "https://example.com/manifest.plist")!, releaseNotes: nil
        )
        let currentInstallation = InstallationToken(rawValue: 7)
        let installationTask = Task { false }
        controller.installation = .running(InstallationOperation(
            token: currentInstallation,
            release: release,
            mode: .manual,
            task: installationTask
        ))
        controller.finishInstallation(token: firstInstallation)
        guard case let .running(installation) = controller.installation else { preconditionFailure() }
        precondition(installation.token == currentInstallation)

        let currentLifecycle = LifecycleToken(rawValue: 7)
        let lifecycleTask = Task {}
        controller.lifecycle = .active(PollingOperation(token: currentLifecycle, task: lifecycleTask))
        let staleLifecycleDelay = await controller.performPollingCheckAndReturnDelay(token: firstLifecycle)
        precondition(staleLifecycleDelay == nil)
        await checkTask.value
        _ = await installationTask.value
        await lifecycleTask.value
    }
}

@main
private struct RuntimeControllerTokenReferenceIdentity {
    static func main() async {
        await UpdateController.runtimeProofTokenReferenceIdentity()
    }
}
