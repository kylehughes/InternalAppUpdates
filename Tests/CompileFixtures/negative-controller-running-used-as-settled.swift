
extension UpdateController {
    private static func compileProofCheckRunningCannotBeSettled() {
        let operation = CheckOperation(
            token: CheckToken(rawValue: 0),
            lifecycle: LifecycleToken(rawValue: 0),
            previous: .idle,
            task: Task {}
        )
        _ = Check.settled(operation)
    }
}
