
extension UpdateController {
    private static func compileProofCheckTokenInstallationToken() {
        func acceptsCheckToken(_: CheckToken) {}
        acceptsCheckToken(InstallationToken(rawValue: 0))
    }
}
