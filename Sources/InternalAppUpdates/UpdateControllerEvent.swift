/// Internal observation points for proving owned-task attachment and completion in tests.
enum UpdateControllerEvent: Equatable, Sendable {
    case checkFinished(Int)
    case checkJoined(Int)
    case checkStarted(Int)
    case installationFinished(Int)
    case installationScheduled(Int)
    case lifecycleFinished(Int)
}
