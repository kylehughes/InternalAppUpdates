
extension UpdateController {
    private static func compileProofInstallationOperationNeedsRelease() {
        _ = InstallationOperation(token: InstallationToken(rawValue: 0), mode: .manual, task: Task { false })
    }
}
