
extension UpdateController {
    private static func compileProofCheckTokenLifecycleToken() {
        func acceptsCheckToken(_: CheckToken) {}
        acceptsCheckToken(LifecycleToken(rawValue: 0))
    }
}
