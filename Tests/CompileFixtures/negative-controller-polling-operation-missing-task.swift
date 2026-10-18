
extension UpdateController {
    private static func compileProofPollingOperationNeedsTask() {
        _ = PollingOperation(token: LifecycleToken(rawValue: 0))
    }
}
