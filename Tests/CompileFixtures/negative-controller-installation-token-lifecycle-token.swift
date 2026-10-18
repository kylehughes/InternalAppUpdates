
extension UpdateController {
    private static func compileProofInstallationTokenLifecycleToken() {
        func acceptsInstallationToken(_: InstallationToken) {}
        acceptsInstallationToken(LifecycleToken(rawValue: 0))
    }
}
