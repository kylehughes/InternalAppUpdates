
extension UpdateController {
    private static func compileProofCheckOperationTaskResult() {
        let wrongTask: Task<Bool, Never> = Task { false }
        _ = CheckOperation(
            token: CheckToken(rawValue: 0),
            lifecycle: LifecycleToken(rawValue: 0),
            previous: .idle,
            task: wrongTask
        )
    }
}
