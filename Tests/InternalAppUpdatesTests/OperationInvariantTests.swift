@testable import InternalAppUpdates
import Combine
import Foundation
import Testing

@MainActor
@Suite("Update controller operation invariants")
struct OperationInvariantTests {
    private let currentOS = OperatingSystemVersion(majorVersion: 16, minorVersion: 4, patchVersion: 0)

    @Test("A successful refresh remains available when an earlier handoff finishes")
    func successfulRefreshDuringInstallationIsRetained() async throws {
        let firstFeed = try makeFeed()
        let newerID = String(repeating: "b", count: 64)
        let refreshedFeed = try makeFeed(release: makeReleaseObject(id: newerID, version: "1.2"))
        let loader = SequencedLoader([.success(firstFeed), .success(refreshedFeed)])
        let gate = InstallationGate()
        let events = UpdateControllerEventRecorder()
        let controller = try controller(loader: loader, installationHandler: { await gate.install($0) })
        controller.eventHandler = events.record

        await controller.checkForUpdates()
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: { await gate.finish(with: false) }
        )

        try await scope.run {
            let installation = scope.track(Task { @MainActor in await controller.requestInstallation() })
            try await gate.waitUntilStarted()
            await controller.checkForUpdates()

            guard case let .updateAvailable(refreshed) = controller.state.check else {
                Issue.record("The completed refresh did not publish its release")
                return
            }
            #expect(refreshed.id == newerID)
            #expect(controller.state.installingRelease?.id == releaseID)

            await gate.finish(with: true)
            try await events.wait(for: .installationFinished(1))
            #expect(await installation.value)
            #expect(controller.availableRelease?.id == newerID)
        }
    }

    @Test("A check cancelled before entry does not load a feed")
    func alreadyCancelledCheckDoesNotStartWork() async throws {
        let loader = CountingLoader(data: try makeFeed())
        let controller = try controller(loader: loader)
        let check = Task { @MainActor in
            withUnsafeCurrentTask { $0?.cancel() }
            await controller.checkForUpdates()
        }

        await check.value
        #expect(await loader.count == 0)
        #expect(controller.state.check == .idle)
    }

    @Test("A reentrant observer deactivation keeps the published state idle")
    func reentrantDeactivationDoesNotLoseTheCancellationState() async throws {
        let loader = CountingLoader(data: try makeFeed())
        let events = UpdateControllerEventRecorder()
        let controller = try controller(loader: loader)
        controller.eventHandler = events.record
        var didDeactivate = false
        let subscription = controller.$state.sink { state in
            guard !didDeactivate, case .checking = state.check else { return }
            didDeactivate = true
            controller.setActive(false)
        }
        defer { subscription.cancel() }
        var lifecycleStarted = false

        try await withOperationCleanup {
            controller.setActive(true)
            lifecycleStarted = true
            try await events.wait(for: .checkStarted(1))
            try await events.wait(for: .checkFinished(1))
            try await events.wait(for: .lifecycleFinished(0))

            #expect(controller.state.check == .idle)
            #expect(controller.state.status == .idle)
            #expect(controller.status == controller.state.status)
            #expect(controller.availableRelease == nil)
            #expect(controller.state.installingRelease == nil)
            #expect(await loader.count == 0)
        } cleanup: {
            controller.setActive(false)
            if events.contains(.checkStarted(1)) {
                await recordCleanupWait(events, for: .checkFinished(1))
            }
            if lifecycleStarted {
                await recordCleanupWait(events, for: .lifecycleFinished(0))
            }
        }
    }

    @Test("An old active generation cannot replace the reactivated generation")
    func staleResultAfterReactivationIsIgnored() async throws {
        let loader = PerRequestLoadGate()
        let events = UpdateControllerEventRecorder()
        let controller = try controller(loader: loader)
        controller.eventHandler = events.record
        let oldFeed = try makeFeed()
        let newerID = String(repeating: "b", count: 64)
        let newFeed = try makeFeed(release: makeReleaseObject(id: newerID, version: "1.2"))
        var firstLifecycleStarted = false
        var secondLifecycleStarted = false

        try await withOperationCleanup {
            controller.setActive(true)
            firstLifecycleStarted = true
            try await events.wait(for: .checkStarted(1))
            try await loader.waitUntilStarted(count: 1)
            controller.setActive(false)
            controller.setActive(true)
            secondLifecycleStarted = true
            try await events.wait(for: .checkStarted(2))
            try await loader.waitUntilStarted(count: 2)

            await loader.finish(request: 1, with: oldFeed)
            try await events.wait(for: .checkFinished(1))
            try await events.wait(for: .lifecycleFinished(0))
            await loader.finish(request: 2, with: newFeed)
            try await events.wait(for: .checkFinished(2))
            controller.setActive(false)
            try await events.wait(for: .lifecycleFinished(1))
        } cleanup: {
            controller.setActive(false)
            await loader.finish(request: 1, with: oldFeed)
            await loader.finish(request: 2, with: newFeed)
            if events.contains(.checkStarted(1)) {
                await recordCleanupWait(events, for: .checkFinished(1))
            }
            if firstLifecycleStarted {
                await recordCleanupWait(events, for: .lifecycleFinished(0))
            }
            if events.contains(.checkStarted(2)) {
                await recordCleanupWait(events, for: .checkFinished(2))
            }
            if secondLifecycleStarted {
                await recordCleanupWait(events, for: .lifecycleFinished(1))
            }
        }

        #expect(controller.availableRelease?.id == newerID)
    }

    @Test("Finishing installation preserves a refresh that is still checking")
    func installationCompletionRetainsAnInFlightRefresh() async throws {
        let loader = PerRequestLoadGate()
        let installationGate = InstallationGate()
        let events = UpdateControllerEventRecorder()
        let controller = try controller(loader: loader, installationHandler: { await installationGate.install($0) })
        controller.eventHandler = events.record
        let firstFeed = try makeFeed()
        let refreshedFeed = try makeFeed(release: makeReleaseObject(id: String(repeating: "b", count: 64), version: "1.2"))

        let initialCheck = Task { @MainActor in await controller.checkForUpdates() }
        try await loader.waitUntilStarted(count: 1)
        await loader.finish(request: 1, with: firstFeed)
        await initialCheck.value

        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: {
                await installationGate.finish(with: false)
                await loader.finish(request: 2, with: refreshedFeed)
            }
        )
        try await scope.run {
            let installation = scope.track(Task { @MainActor in await controller.requestInstallation() })
            try await installationGate.waitUntilStarted()
            let refresh = scope.track(Task { @MainActor in await controller.checkForUpdates() })
            try await loader.waitUntilStarted(count: 2)

            await installationGate.finish(with: true)
            try await events.wait(for: .installationFinished(1))
            #expect(await installation.value)
            guard case let .checking(previousRelease) = controller.state.check else {
                Issue.record("Installation completion replaced the in-flight refresh")
                return
            }
            #expect(previousRelease?.id == releaseID)
            #expect(controller.status == .checking)
            #expect(controller.availableRelease?.id == releaseID)

            await loader.finish(request: 2, with: refreshedFeed)
            await refresh.value
        }
    }

    @Test("A reentrant state observer can deactivate and reactivate without accepting stale work")
    func reentrantObserverPreservesReactivatedState() async throws {
        let loader = PerRequestLoadGate()
        let events = UpdateControllerEventRecorder()
        let controller = try controller(loader: loader)
        controller.eventHandler = events.record
        let newerID = String(repeating: "b", count: 64)
        var didReenter = false
        var secondLifecycleStarted = false
        let subscription = controller.$state.sink { state in
            guard !didReenter, case .updateAvailable = state.check else {
                return
            }
            didReenter = true
            controller.setActive(false)
            controller.setActive(true)
            secondLifecycleStarted = true
        }
        defer { subscription.cancel() }

        let firstFeed = try makeFeed()
        let secondFeed = try makeFeed(release: makeReleaseObject(id: newerID, version: "1.2"))
        var firstLifecycleStarted = false
        try await withOperationCleanup {
            controller.setActive(true)
            firstLifecycleStarted = true
            try await events.wait(for: .checkStarted(1))
            try await loader.waitUntilStarted(count: 1)
            await loader.finish(request: 1, with: firstFeed)
            try await events.wait(for: .checkFinished(1))
            try await events.wait(for: .lifecycleFinished(0))
            try await events.wait(for: .checkStarted(2))
            try await loader.waitUntilStarted(count: 2)
            await loader.finish(request: 2, with: secondFeed)
            try await events.wait(for: .checkFinished(2))
            controller.setActive(false)
            try await events.wait(for: .lifecycleFinished(1))
        } cleanup: {
            controller.setActive(false)
            await loader.finish(request: 1, with: firstFeed)
            await loader.finish(request: 2, with: secondFeed)
            if events.contains(.checkStarted(1)) {
                await recordCleanupWait(events, for: .checkFinished(1))
            }
            if firstLifecycleStarted {
                await recordCleanupWait(events, for: .lifecycleFinished(0))
            }
            if events.contains(.checkStarted(2)) {
                await recordCleanupWait(events, for: .checkFinished(2))
            }
            if secondLifecycleStarted {
                await recordCleanupWait(events, for: .lifecycleFinished(1))
            }
        }

        #expect(controller.availableRelease?.id == newerID)
        #expect(controller.state.check.statusIsAvailable)
    }

    @Test("The injected runtime operating system rejects an incompatible release")
    func injectedOperatingSystemVersionIsAppliedAtCheckTime() async throws {
        let feed = try makeFeed(release: makeReleaseObject(minimumOSVersion: "17.0"))
        let controller = try controller(loader: ImmediateLoader(result: .success(feed)))

        await controller.checkForUpdates()
        #expect(controller.state.check == .failed(.incompatibleOperatingSystem(minimumVersion: "17.0")))
    }

    @Test(
        "Automatic suppression survives recreated controllers but never blocks manual or distinct scopes",
        arguments: [false, true]
    )
    func persistedAutomaticSuppressionScopesByFeedAppAndRelease(malformedMarker: Bool) async throws {
        let suiteName = "InternalAppUpdatesTests.OperationInvariantTests.\(UUID().uuidString)"
        let defaults = try #require(UserDefaults(suiteName: suiteName))
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = UserDefaultsUpdatePromptStore(defaults: defaults)
        let feedURL = URL(string: "https://updates.example.com/feed.json")!
        let seededKey = "InternalAppUpdates.presented.Y29tLmV4YW1wbGUuQXBwfGh0dHBzOi8vdXBkYXRlcy5leGFtcGxlLmNvbS9mZWVkLmpzb24=.\(releaseID)"
        defaults.set(malformedMarker ? NSNumber(value: 0) : true, forKey: seededKey)
        let recorder = InstallationRecorder(result: false)
        let configuration = try UpdateConfiguration(
            feedURL: feedURL,
            installedBuild: makeInstalledBuild(),
            presentation: .automatic,
            checkInterval: 3_600
        )
        let events = UpdateControllerEventRecorder()
        let suppressed = UpdateController(
            configuration: configuration,
            loader: ImmediateLoader(result: .success(try makeFeed())),
            installationHandler: { await recorder.install($0) },
            promptStore: store,
            currentOSVersion: currentOS,
            now: { fixedNow }
        )
        suppressed.eventHandler = events.record
        try await withActivePolling(suppressed, events: events) {
            try await events.wait(for: .checkFinished(1))
        }

        #expect(await recorder.urls.isEmpty)
        #expect(await suppressed.requestInstallation() == false)
        #expect(await recorder.urls.count == 1)

        let differentReleaseEvents = UpdateControllerEventRecorder()
        let differentRelease = UpdateController(
            configuration: configuration,
            loader: ImmediateLoader(result: .success(try makeFeed(release: makeReleaseObject(id: String(repeating: "b", count: 64), version: "1.2")))),
            installationHandler: { await recorder.install($0) },
            promptStore: store,
            currentOSVersion: currentOS,
            now: { fixedNow }
        )
        differentRelease.eventHandler = differentReleaseEvents.record
        try await withActivePolling(differentRelease, events: differentReleaseEvents) {
            try await differentReleaseEvents.wait(for: .installationFinished(1))
        }

        let reloadedStore = UserDefaultsUpdatePromptStore(defaults: defaults)
        let reloadedEvents = UpdateControllerEventRecorder()
        let reloaded = UpdateController(
            configuration: configuration,
            loader: ImmediateLoader(result: .success(try makeFeed(release: makeReleaseObject(id: String(repeating: "b", count: 64), version: "1.2")))),
            installationHandler: { await recorder.install($0) },
            promptStore: reloadedStore,
            currentOSVersion: currentOS,
            now: { fixedNow }
        )
        reloaded.eventHandler = reloadedEvents.record
        try await withActivePolling(reloaded, events: reloadedEvents) {
            try await reloadedEvents.wait(for: .checkFinished(1))
        }
        #expect(await recorder.urls.count == 2)

        let differentFeedEvents = UpdateControllerEventRecorder()
        let differentFeedConfiguration = try UpdateConfiguration(
            feedURL: URL(string: "https://updates.example.com/other/feed.json")!,
            installedBuild: makeInstalledBuild(),
            presentation: .automatic,
            checkInterval: 3_600
        )
        let differentFeed = UpdateController(
            configuration: differentFeedConfiguration,
            loader: ImmediateLoader(result: .success(try makeFeed())),
            installationHandler: { await recorder.install($0) },
            promptStore: store,
            currentOSVersion: currentOS,
            now: { fixedNow }
        )
        differentFeed.eventHandler = differentFeedEvents.record
        try await withActivePolling(differentFeed, events: differentFeedEvents) {
            try await differentFeedEvents.wait(for: .installationFinished(1))
        }

        let differentAppEvents = UpdateControllerEventRecorder()
        let differentAppConfiguration = try UpdateConfiguration(
            feedURL: feedURL,
            installedBuild: InstalledBuild(bundleIdentifier: "com.example.Other", version: "1.0", build: "1"),
            presentation: .automatic,
            checkInterval: 3_600
        )
        let differentApp = UpdateController(
            configuration: differentAppConfiguration,
            loader: ImmediateLoader(result: .success(try makeFeed(bundleIdentifier: "com.example.Other"))),
            installationHandler: { await recorder.install($0) },
            promptStore: store,
            currentOSVersion: currentOS,
            now: { fixedNow }
        )
        differentApp.eventHandler = differentAppEvents.record
        try await withActivePolling(differentApp, events: differentAppEvents) {
            try await differentAppEvents.wait(for: .installationFinished(1))
        }

        #expect(await recorder.urls.count == 4)
    }

    @Test("Activating while an explicit inactive check is in flight joins that check")
    func activationJoinsAnExplicitCheckWithoutStartingAnotherLoad() async throws {
        let loader = PerRequestLoadGate()
        let events = UpdateControllerEventRecorder()
        let controller = try controller(loader: loader)
        controller.eventHandler = events.record
        let feed = try makeFeed()
        var explicitCheck: Task<Void, Never>?
        var lifecycleStarted = false

        try await withOperationCleanup {
            let check = Task { @MainActor in await controller.checkForUpdates() }
            explicitCheck = check
            try await events.wait(for: .checkStarted(1))
            try await loader.waitUntilStarted(count: 1)
            controller.setActive(true)
            lifecycleStarted = true
            try await events.wait(for: .checkJoined(1))
            await loader.finish(request: 1, with: feed)
            try await events.wait(for: .checkFinished(1))
            await check.value
            controller.setActive(false)
            try await events.wait(for: .lifecycleFinished(0))

            #expect(await loader.loadCount == 1)
            #expect(controller.availableRelease?.id == releaseID)
        } cleanup: {
            controller.setActive(false)
            await loader.finish(request: 1, with: feed)
            if events.contains(.checkStarted(1)) {
                await recordCleanupWait(events, for: .checkFinished(1))
            }
            if lifecycleStarted {
                await recordCleanupWait(events, for: .lifecycleFinished(0))
            }
            explicitCheck?.cancel()
            if let explicitCheck {
                await explicitCheck.value
            }
        }
    }

    private func controller(
        loader: any UpdateFeedLoading,
        installationHandler: @escaping UpdateInstallationHandler = UpdateController.systemInstallationHandler
    ) throws -> UpdateController {
        UpdateController(
            configuration: try makeConfiguration(),
            loader: loader,
            installationHandler: installationHandler,
            promptStore: MemoryPromptStore(),
            currentOSVersion: currentOS,
            now: { fixedNow }
        )
    }
}

private extension UpdateCheckState {
    var statusIsAvailable: Bool {
        if case .updateAvailable = self { return true }
        return false
    }
}

actor CountingLoader: UpdateFeedLoading {
    private let data: Data
    private(set) var count = 0

    init(data: Data) { self.data = data }

    func load(from url: URL, maximumResponseSize: Int) async throws -> Data {
        count += 1
        return data
    }
}

actor PerRequestLoadGate: UpdateFeedLoading {
    private struct StartWaiter {
        let count: Int
        let continuation: CheckedContinuation<Void, any Error>
        let timeoutTask: Task<Void, Never>
    }

    private var completedResults: [Int: Data] = [:]
    private var continuations: [Int: CheckedContinuation<Data, Never>] = [:]
    private var startWaiters: [UUID: StartWaiter] = [:]
    private(set) var loadCount = 0

    func load(from url: URL, maximumResponseSize: Int) async throws -> Data {
        loadCount += 1
        let request = loadCount
        finishStartedWaiters()
        if let completedResult = completedResults.removeValue(forKey: request) {
            return completedResult
        }
        return await withCheckedContinuation { continuations[request] = $0 }
    }

    func finish(request: Int, with data: Data) {
        if let continuation = continuations.removeValue(forKey: request) {
            continuation.resume(returning: data)
        } else {
            completedResults[request] = data
        }
    }

    func waitUntilStarted(count: Int, timeout: Duration = .seconds(1)) async throws {
        guard loadCount < count else { return }
        let id = UUID()
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, any Error>) in
                guard !Task.isCancelled else {
                    continuation.resume(throwing: CancellationError())
                    return
                }
                let timeoutTask = Task { [weak self] in
                    do {
                        try await waitForContentionScaledTimeout(timeout)
                    } catch {
                        return
                    }
                    guard !Task.isCancelled else { return }
                    await self?.finishStartWaiter(id, result: .failure(EventWaitError.timedOutStart))
                }
                startWaiters[id] = StartWaiter(count: count, continuation: continuation, timeoutTask: timeoutTask)
            }
        } onCancel: {
            Task { await self.finishStartWaiter(id, result: .failure(CancellationError())) }
        }
    }

    private func finishStartedWaiters() {
        let ids = startWaiters.compactMap { id, waiter in
            loadCount >= waiter.count ? id : nil
        }
        for id in ids { finishStartWaiter(id, result: .success(())) }
    }

    private func finishStartWaiter(_ id: UUID, result: Result<Void, any Error>) {
        guard let waiter = startWaiters.removeValue(forKey: id) else { return }
        waiter.timeoutTask.cancel()
        waiter.continuation.resume(with: result)
    }
}

@MainActor
private func withOperationCleanup<T>(
    operation: () async throws -> T,
    cleanup: () async -> Void
) async throws -> T {
    do {
        let value = try await operation()
        await cleanup()
        return value
    } catch {
        await cleanup()
        throw error
    }
}

@MainActor
private func recordCleanupWait(
    _ events: UpdateControllerEventRecorder,
    for event: UpdateControllerEvent
) async {
    do {
        try await events.wait(for: event)
    } catch {
        Issue.record("Cleanup did not observe controller event \(event): \(error)")
    }
}

@MainActor
private func withActivePolling<T>(
    _ controller: UpdateController,
    events: UpdateControllerEventRecorder,
    operation: () async throws -> T
) async throws -> T {
    controller.setActive(true)
    do {
        let value = try await operation()
        controller.setActive(false)
        try await events.wait(for: .lifecycleFinished(0))
        return value
    } catch {
        controller.setActive(false)
        await recordCleanupWait(events, for: .lifecycleFinished(0))
        throw error
    }
}
