
extension UpdateController {
    private static func compileProofCheckOperationNeedsToken() {
        _ = CheckOperation(lifecycle: LifecycleToken(rawValue: 0), previous: .idle, task: Task {})
    }
}
