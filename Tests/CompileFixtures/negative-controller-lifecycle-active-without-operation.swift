
extension UpdateController {
    private static func compileProofLifecycleNeedsPollingOperation() {
        _ = Lifecycle.active(LifecycleToken(rawValue: 0))
    }
}
