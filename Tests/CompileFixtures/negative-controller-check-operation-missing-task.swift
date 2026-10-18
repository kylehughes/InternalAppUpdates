
extension UpdateController {
    private static func compileProofCheckOperationNeedsTask() {
        _ = CheckOperation(token: CheckToken(rawValue: 0), lifecycle: LifecycleToken(rawValue: 0), previous: .idle)
    }
}
