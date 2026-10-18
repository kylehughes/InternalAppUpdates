import Combine
import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// Persistence used to remember automatically presented releases.
@MainActor
public protocol UpdatePromptStoring: AnyObject {
    /// Returns whether automatic presentation should be suppressed for a release and scope.
    ///
    /// Stores retain automatic attempts and may conservatively suppress uncertain persisted records.
    func hasPresentedRelease(id: String, scope: String) -> Bool
    /// Records a release as presented for a scope.
    func markReleasePresented(id: String, scope: String)
}

/// UserDefaults-backed automatic-presentation persistence.
@MainActor
public final class UserDefaultsUpdatePromptStore: UpdatePromptStoring {
    private let defaults: UserDefaults

    /// Creates a prompt store backed by the supplied defaults database.
    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    /// Returns whether automatic presentation is suppressed for the release in the supplied scope.
    ///
    /// Missing and `false` Boolean markers allow presentation. A present marker with any other type is treated as
    /// suppressing presentation so that malformed persisted state cannot trigger an automatic retry.
    public func hasPresentedRelease(id: String, scope: String) -> Bool {
        // Missing and false Boolean values allow another automatic attempt. A malformed
        // marker suppresses it: retrying automatically is less safe than requiring a
        // manual request when persisted state cannot be interpreted.
        guard let value = defaults.object(forKey: key(for: scope, releaseID: id)) else {
            return false
        }
        guard let boolean = value as? NSNumber,
              CFGetTypeID(boolean) == CFBooleanGetTypeID()
        else {
            return true
        }
        return boolean.boolValue
    }

    /// Persists an automatic presentation attempt for this release and scope.
    public func markReleasePresented(id: String, scope: String) {
        defaults.set(true, forKey: key(for: scope, releaseID: id))
    }

    private func key(for scope: String, releaseID: String) -> String {
        let encodedScope = Data(scope.utf8).base64EncodedString()
        return "InternalAppUpdates.presented.\(encodedScope).\(releaseID)"
    }
}

/// A closure that hands an installation URL to the operating system.
///
/// Return `true` when the handler accepts the URL handoff. This does not indicate installation completion.
public typealias UpdateInstallationHandler = @MainActor @Sendable (URL) async -> Bool

/// Coordinates foreground update checks and installation handoffs.
@MainActor
public final class UpdateController: ObservableObject {
    /// The maximum accepted feed response size.
    public static let maximumFeedSize = 256 * 1024

    /// The platform-default installation handoff.
    ///
    /// On iOS this asks `UIApplication` to open the supplied URL. Other platforms return `false`.
    public static let systemInstallationHandler: UpdateInstallationHandler = { url in
        #if canImport(UIKit)
        return await UIApplication.shared.open(url)
        #else
        return false
        #endif
    }

    /// The policy used when a compatible release becomes available.
    @Published public var presentationPolicy: UpdatePresentationPolicy {
        didSet {
            beginAutomaticInstallationIfNeeded()
        }
    }
    /// The complete observable state. Observe `states()` or `$state` for coherent snapshots.
    @Published public private(set) var state = UpdateState(check: .idle)

    var eventHandler: ((UpdateControllerEvent) -> Void)?

    private let configuration: UpdateConfiguration
    private let currentOSVersion: OperatingSystemVersion
    private let installationHandler: UpdateInstallationHandler
    private let loader: any UpdateFeedLoading
    private let now: @Sendable () -> Date
    private let promptStore: any UpdatePromptStoring

    private var check: Check = .settled(.idle)
    private var installation: Installation = .idle
    private var isPublishingState = false
    private var lifecycle: Lifecycle = .inactive(LifecycleToken(rawValue: 0))
    private var nextCheckID = 0
    private var nextInstallationID = 0
    private var stateContinuations: [UUID: AsyncStream<UpdateState>.Continuation] = [:]

    /// Creates an update controller with injectable system boundaries.
    ///
    /// - Parameters:
    ///   - configuration: The validated feed URL, installed build, presentation policy, and polling interval.
    ///   - loader: The update-feed data source. Pass `nil` to use a URL-session loader configured for the feed URL.
    ///   - installationHandler: The operation that opens the generated `itms-services` installation URL.
    ///   - promptStore: Persistence for automatic presentation attempts.
    ///   - currentOSVersion: The operating system version used for compatibility checks.
    ///   - now: The current time provider used for expiration checks.
    public init(
        configuration: UpdateConfiguration,
        loader: (any UpdateFeedLoading)? = nil,
        installationHandler: @escaping UpdateInstallationHandler = UpdateController.systemInstallationHandler,
        promptStore: any UpdatePromptStoring = UserDefaultsUpdatePromptStore(),
        currentOSVersion: OperatingSystemVersion = ProcessInfo.processInfo.operatingSystemVersion,
        now: @escaping @Sendable () -> Date = Date.init
    ) {
        self.configuration = configuration
        self.loader = loader ?? URLSessionUpdateFeedLoader(
            allowsInsecureLoopback: configuration.allowsInsecureLoopback
        )
        self.installationHandler = installationHandler
        self.promptStore = promptStore
        self.currentOSVersion = currentOSVersion
        self.now = now
        presentationPolicy = configuration.presentation
    }

    deinit {
        if case let .active(operation) = lifecycle { operation.task.cancel() }
        if case let .running(operation) = check { operation.task.cancel() }
        if case let .running(operation) = installation { operation.task.cancel() }
        for continuation in stateContinuations.values {
            continuation.finish()
        }
    }

    /// The release from the latest successful check, including during a refresh.
    ///
    /// This is a computed convenience. Observe `states()` or `$state` for changes.
    public var availableRelease: UpdateRelease? {
        snapshot.availableRelease
    }

    /// Whether installation can currently be requested, including the expiration check.
    public var canRequestInstallation: Bool {
        guard case .idle = installation,
              case let .settled(.updateAvailable(release)) = check
        else {
            return false
        }
        guard eligibilityError(for: release) == nil,
              case .idle = installation,
              case .settled(.updateAvailable(release)) = check
        else {
            return false
        }
        return true
    }

    /// A computed status that gives an active installer handoff precedence over checking.
    ///
    /// Observe `states()` or `$state` for changes. Inspect `state.check` for a result received during a handoff.
    public var status: UpdateStatus {
        snapshot.status
    }

    /// Checks the feed and updates published state.
    ///
    /// Concurrent calls share one feed request and return after its result is applied. Cancelling any caller cancels the
    /// shared request for every caller. Cancellation or lifecycle changes prevent stale results from being applied.
    /// A check may overlap an installer handoff; its result is available through `state.check` while the handoff runs.
    public func checkForUpdates() async {
        let operation: CheckOperation
        switch check {
        case let .running(current):
            operation = current
            eventHandler?(.checkJoined(current.token.rawValue))
        case let .settled(previous):
            nextCheckID &+= 1
            let token = CheckToken(rawValue: nextCheckID)
            let lifecycleToken = lifecycle.token
            let configuration = configuration
            let loader = loader
            let currentOSVersion = currentOSVersion
            let now = now
            let maximumFeedSize = Self.maximumFeedSize

            let task = Task { @MainActor [weak self] in
                let result: Result<UpdateRelease?, UpdateError>
                do {
                    try Task.checkCancellation()
                    let data = try await loader.load(
                        from: configuration.feedURL,
                        maximumResponseSize: maximumFeedSize
                    )
                    try Task.checkCancellation()
                    guard data.count <= maximumFeedSize else {
                        throw UpdateFeedLoaderError.responseTooLarge
                    }
                    result = .success(
                        try FeedDecoder.decode(
                            data,
                            installedBuild: configuration.installedBuild,
                            currentOSVersion: currentOSVersion,
                            now: now()
                        )
                    )
                } catch is CancellationError {
                    result = .failure(.feedLoadingFailed("The update check was cancelled."))
                } catch let error as UpdateError {
                    result = .failure(error)
                } catch {
                    result = .failure(.feedLoadingFailed(error.localizedDescription))
                }
                self?.completeCheck(token: token, result: result, wasCancelled: Task.isCancelled)
                self?.eventHandler?(.checkFinished(token.rawValue))
            }
            operation = CheckOperation(token: token, lifecycle: lifecycleToken, previous: previous, task: task)
            check = .running(operation)
            publishState()
            eventHandler?(.checkStarted(token.rawValue))
        }

        await withTaskCancellationHandler {
            await operation.task.value
        } onCancel: {
            operation.task.cancel()
        }
    }

    /// Hands the available release's manifest to the operating system.
    ///
    /// Returns `false` when no eligible release is ready, a check or another handoff is in progress, the caller is
    /// observed as cancelled before handoff, or the handler declines it. Caller cancellation is forwarded to the
    /// handler's task; it cannot undo a URL handoff the operating system already accepted.
    /// - Returns: `true` when the handler accepts the URL handoff, not installation progress or completion.
    public func requestInstallation() async -> Bool {
        guard !Task.isCancelled, let task = beginInstallation(mode: .manual) else {
            return false
        }
        return await withTaskCancellationHandler {
            await task.value
        } onCancel: {
            task.cancel()
        }
    }

    /// Starts or stops controller-owned foreground polling.
    ///
    /// Activating performs an immediate check. Deactivating cancels controller-owned work and prevents stale responses
    /// from changing state or opening the installer. A cancelled refresh restores its prior completed result, then
    /// revalidates that result's eligibility before retaining it.
    public func setActive(_ active: Bool) {
        guard lifecycle.isActive != active else {
            return
        }
        if case let .active(operation) = lifecycle {
            operation.task.cancel()
        }
        let token = lifecycle.token

        guard active else {
            lifecycle = .inactive(LifecycleToken(rawValue: token.rawValue &+ 1))
            if case let .running(operation) = check {
                operation.task.cancel()
                check = .settled(operation.previous)
            }
            if case let .running(operation) = installation {
                operation.task.cancel()
                installation = .idle
            }
            invalidateIneligibleRelease()
            publishState()
            return
        }

        let task = Task { [weak self] in
            defer { self?.eventHandler?(.lifecycleFinished(token.rawValue)) }
            while !Task.isCancelled {
                guard let delay = await self?.performPollingCheckAndReturnDelay(token: token) else {
                    return
                }
                do {
                    try await Task.sleep(for: delay)
                } catch {
                    return
                }
            }
        }
        lifecycle = .active(PollingOperation(token: token, task: task))
    }

    /// Creates an independent stream of the current state and subsequent state changes.
    ///
    /// Calling this method registers observation synchronously and buffers the current snapshot. Only the newest
    /// unconsumed snapshot is retained, so a slow consumer may skip intermediate states, including the initial one.
    /// Check failures arrive as state values; they do not end the stream. Observation does not start polling.
    ///
    /// Create a separate stream for each consumer. Cancel the consuming task when observation is no longer needed.
    /// Cancellation ends only that subscription; it does not cancel checks or installation. The stream does not
    /// retain the controller and finishes when the controller is deallocated.
    public func states() -> AsyncStream<UpdateState> {
        AsyncStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            let id = UUID()
            continuation.onTermination = { [weak self] _ in
                Task { @MainActor [weak self] in
                    self?.stateContinuations[id] = nil
                }
            }
            stateContinuations[id] = continuation
            continuation.yield(snapshot)
        }
    }

    private var promptScope: String {
        "\(configuration.installedBuild.bundleIdentifier)|\(configuration.feedURL.absoluteString)"
    }

    private var snapshot: UpdateState {
        let installingRelease: UpdateRelease?
        switch installation {
        case .idle: installingRelease = nil
        case let .running(operation): installingRelease = operation.release
        }
        let checkState: UpdateCheckState
        switch check {
        case let .settled(result): checkState = result.snapshot
        case let .running(operation):
            checkState = .checking(previousRelease: UpdateState(check: operation.previous.snapshot).availableRelease)
        }
        return UpdateState(check: checkState, installingRelease: installingRelease)
    }

    private static func installationURL(for manifestURL: URL) -> URL? {
        var components = URLComponents()
        components.scheme = "itms-services"
        components.host = ""
        components.queryItems = [
            URLQueryItem(name: "action", value: "download-manifest"),
            URLQueryItem(name: "url", value: manifestURL.absoluteString),
        ]
        return components.url
    }

    private func beginAutomaticInstallationIfNeeded() {
        guard case let .active(operation) = lifecycle, presentationPolicy == .automatic else {
            return
        }
        _ = beginInstallation(mode: .automatic(operation.token))
    }

    private func beginInstallation(mode: InstallationMode) -> Task<Bool, Never>? {
        invalidateIneligibleRelease()
        publishState()
        guard case .idle = installation,
              case let .settled(.updateAvailable(release)) = check,
              let installationURL = Self.installationURL(for: release.manifestURL)
        else {
            return nil
        }
        if case .automatic = mode,
           promptStore.hasPresentedRelease(id: release.id, scope: promptScope) {
            return nil
        }
        // A custom prompt store can synchronously reenter the controller.
        guard case .idle = installation,
              case .settled(.updateAvailable(release)) = check,
              permitsInstallation(mode: mode)
        else {
            return nil
        }

        nextInstallationID &+= 1
        let token = InstallationToken(rawValue: nextInstallationID)
        let task = Task { @MainActor [weak self, installationHandler] in
            defer {
                self?.finishInstallation(token: token)
                self?.eventHandler?(.installationFinished(token.rawValue))
            }
            guard !Task.isCancelled,
                  self?.prepareInstallation(token: token) == true,
                  !Task.isCancelled
            else {
                return false
            }
            return await installationHandler(installationURL)
        }
        installation = .running(InstallationOperation(token: token, release: release, mode: mode, task: task))
        publishState()
        eventHandler?(.installationScheduled(token.rawValue))
        return task
    }

    private func completeCheck(
        token: CheckToken,
        result: Result<UpdateRelease?, UpdateError>,
        wasCancelled: Bool
    ) {
        guard case let .running(operation) = check,
              operation.token == token,
              operation.lifecycle == lifecycle.token
        else {
            return
        }
        if wasCancelled {
            check = .settled(operation.previous)
            invalidateIneligibleRelease()
        } else {
            switch result {
            case let .success(release):
                check = .settled(release.map(CheckResult.updateAvailable) ?? .upToDate)
            case let .failure(error):
                check = .settled(.failed(error))
            }
        }
        publishState()
        if !wasCancelled, case .success = result {
            beginAutomaticInstallationIfNeeded()
        }
    }

    private func eligibilityError(for release: UpdateRelease) -> UpdateError? {
        guard release.expiresAt > now() else {
            return .releaseExpired
        }
        guard let currentOS = VersionComponents(currentOSVersion) else {
            return .invalidConfiguration("The current operating system version must not be negative.")
        }
        guard release.isCompatible(with: currentOS) else {
            return .incompatibleOperatingSystem(minimumVersion: release.minimumOSVersion)
        }
        return nil
    }

    private func finishInstallation(token: InstallationToken) {
        guard case let .running(operation) = installation, operation.token == token else {
            return
        }
        installation = .idle
        invalidateIneligibleRelease()
        publishState()
    }

    private func invalidateIneligibleRelease() {
        guard case let .settled(.updateAvailable(release)) = check,
              let error = eligibilityError(for: release),
              case .settled(.updateAvailable(release)) = check
        else {
            return
        }
        check = .settled(.failed(error))
    }

    private func performPollingCheckAndReturnDelay(token: LifecycleToken) async -> Duration? {
        guard !Task.isCancelled, case let .active(operation) = lifecycle, operation.token == token else {
            return nil
        }
        await checkForUpdates()
        guard !Task.isCancelled, case let .active(operation) = lifecycle, operation.token == token else {
            return nil
        }
        return configuration.checkDelay
    }

    private func permitsInstallation(mode: InstallationMode) -> Bool {
        switch mode {
        case .manual:
            return true
        case let .automatic(token):
            return lifecycle.isActive && lifecycle.token == token && presentationPolicy == .automatic
        }
    }

    private func prepareInstallation(token: InstallationToken) -> Bool {
        invalidateIneligibleRelease()
        publishState()
        guard case let .running(operation) = installation,
              operation.token == token,
              case .settled(.updateAvailable(operation.release)) = check,
              permitsInstallation(mode: operation.mode)
        else {
            return false
        }
        if case .automatic = operation.mode {
            guard !promptStore.hasPresentedRelease(id: operation.release.id, scope: promptScope),
                  isCurrentInstallation(operation)
            else {
                return false
            }
            promptStore.markReleasePresented(id: operation.release.id, scope: promptScope)
        }
        return isCurrentInstallation(operation)
    }

    private func isCurrentInstallation(_ operation: InstallationOperation) -> Bool {
        // The injected clock can reenter too; establish eligibility before the final ownership and policy checks.
        guard eligibilityError(for: operation.release) == nil,
              case let .running(current) = installation,
              current.token == operation.token,
              case .settled(.updateAvailable(operation.release)) = check,
              permitsInstallation(mode: operation.mode)
        else {
            return false
        }
        return true
    }

    private func publishState() {
        // Published sends from willSet. Reentrant observers may transition operations before that assignment returns.
        // Drain their projections afterward so an outer publication cannot overwrite a newer authoritative state.
        guard !isPublishingState, state != snapshot else { return }
        isPublishingState = true
        defer { isPublishingState = false }
        while state != snapshot {
            state = snapshot
        }
        // Async consumers receive the settled projection after synchronous observers have finished reentering.
        for continuation in stateContinuations.values {
            continuation.yield(state)
        }
    }
}

extension UpdateController {
    private enum Check: Sendable {
        case running(CheckOperation)
        case settled(CheckResult)
    }
}

extension UpdateController {
    private struct CheckOperation: Sendable {
        let token: CheckToken
        let lifecycle: LifecycleToken
        let previous: CheckResult
        let task: Task<Void, Never>
    }
}

extension UpdateController {
    private enum CheckResult: Sendable {
        case failed(UpdateError)
        case idle
        case upToDate
        case updateAvailable(UpdateRelease)

        var snapshot: UpdateCheckState {
            switch self {
            case let .failed(error): .failed(error)
            case .idle: .idle
            case .upToDate: .upToDate
            case let .updateAvailable(release): .updateAvailable(release)
            }
        }
    }
}

extension UpdateController {
    private final class CheckToken: Equatable, Sendable {
        let rawValue: Int

        init(rawValue: Int) {
            self.rawValue = rawValue
        }

        static func == (lhs: CheckToken, rhs: CheckToken) -> Bool {
            lhs === rhs
        }
    }
}

extension UpdateController {
    private enum Installation: Sendable {
        case idle
        case running(InstallationOperation)
    }
}

extension UpdateController {
    private enum InstallationMode: Sendable {
        case automatic(LifecycleToken)
        case manual
    }
}

extension UpdateController {
    private struct InstallationOperation: Sendable {
        let token: InstallationToken
        let release: UpdateRelease
        let mode: InstallationMode
        let task: Task<Bool, Never>
    }
}

extension UpdateController {
    private final class InstallationToken: Equatable, Sendable {
        let rawValue: Int

        init(rawValue: Int) {
            self.rawValue = rawValue
        }

        static func == (lhs: InstallationToken, rhs: InstallationToken) -> Bool {
            lhs === rhs
        }
    }
}

extension UpdateController {
    private enum Lifecycle: Sendable {
        case active(PollingOperation)
        case inactive(LifecycleToken)

        var isActive: Bool {
            if case .active = self { return true }
            return false
        }

        var token: LifecycleToken {
            switch self {
            case let .active(operation): operation.token
            case let .inactive(token): token
            }
        }
    }
}

extension UpdateController {
    private final class LifecycleToken: Equatable, Sendable {
        let rawValue: Int

        init(rawValue: Int) {
            self.rawValue = rawValue
        }

        static func == (lhs: LifecycleToken, rhs: LifecycleToken) -> Bool {
            lhs === rhs
        }
    }
}

extension UpdateController {
    private struct PollingOperation: Sendable {
        let token: LifecycleToken
        let task: Task<Void, Never>
    }
}
