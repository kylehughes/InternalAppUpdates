/// An immutable snapshot of feed checking and installation handoff activity.
///
/// A check may finish while an installer handoff is still running. Both facts remain available in the same snapshot.
public struct UpdateState: Equatable, Sendable {
    /// The latest feed-check state, including its validated release when available.
    public let check: UpdateCheckState
    /// The release currently being handed to the operating system, if any.
    public let installingRelease: UpdateRelease?

    init(check: UpdateCheckState, installingRelease: UpdateRelease? = nil) {
        self.check = check
        self.installingRelease = installingRelease
    }

    /// The release from the latest successful check, including during a refresh.
    public var availableRelease: UpdateRelease? {
        switch check {
        case let .checking(previousRelease): previousRelease
        case let .updateAvailable(release): release
        case .idle, .upToDate, .failed: nil
        }
    }

    /// A convenience status that gives an in-progress installer handoff precedence over checking.
    public var status: UpdateStatus {
        if installingRelease != nil {
            return .openingInstaller
        }
        return switch check {
        case .idle: .idle
        case .checking: .checking
        case .updateAvailable: .updateAvailable
        case .upToDate: .upToDate
        case let .failed(error): .failed(error)
        }
    }
}
