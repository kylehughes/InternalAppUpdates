
extension UpdateController {
    private static func compileProofCheckOperationNeedsPreviousResult() {
        _ = CheckOperation(token: CheckToken(rawValue: 0), lifecycle: LifecycleToken(rawValue: 0), task: Task {})
    }
}
