/// The result or progress of checking an update feed.
public enum UpdateCheckState: Equatable, Sendable {
    /// A check is running, optionally retaining the previous release for display.
    case checking(previousRelease: UpdateRelease?)
    /// Loading or validation failed; there is no available release.
    case failed(UpdateError)
    /// No check has completed.
    case idle
    /// The feed contains no newer release.
    case upToDate
    /// A newer release passed validation and eligibility checks.
    case updateAvailable(UpdateRelease)
}
