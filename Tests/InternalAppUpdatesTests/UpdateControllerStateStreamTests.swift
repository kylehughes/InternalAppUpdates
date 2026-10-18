@testable import InternalAppUpdates
import Combine
import Foundation
import Testing

@MainActor
@Suite("Update controller state streams", .timeLimit(.minutes(1)))
struct UpdateControllerStateStreamTests {
    private let currentOS = OperatingSystemVersion(majorVersion: 16, minorVersion: 4, patchVersion: 0)

    @Test("A stream synchronously buffers the current state and receives later transitions")
    func buffersCurrentStateAndReceivesTransitions() async throws {
        let feed = try makeFeed()
        let gate = LoadGate()
        let events = UpdateControllerEventRecorder()
        let controller = try makeController(loader: gate)
        controller.eventHandler = events.record
        let stream = controller.states()
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.checkFinished(1)],
            releaseGate: { await gate.finish(with: feed) }
        )

        try await scope.run {
            let initialValue = try await nextValue(from: stream)
            let initial = try #require(initialValue)
            #expect(initial.check == .idle)

            let check = scope.track(Task { @MainActor in await controller.checkForUpdates() })
            try await events.wait(for: .checkStarted(1))
            let checkingValue = try await nextValue(from: stream)
            let checking = try #require(checkingValue)
            guard case .checking(previousRelease: nil) = checking.check else {
                Issue.record("The stream did not publish the started check")
                return
            }

            await gate.finish(with: feed)
            try await events.wait(for: .checkFinished(1))
            let completedValue = try await nextValue(from: stream)
            let completed = try #require(completedValue)
            guard case let .updateAvailable(release) = completed.check else {
                Issue.record("The stream did not publish the completed check")
                return
            }
            #expect(release.id == releaseID)
            await check.value
        }
    }

    @Test("A slow consumer receives only the newest state")
    func conflatesUnconsumedStates() async throws {
        let feed = try makeFeed()
        let gate = LoadGate()
        let events = UpdateControllerEventRecorder()
        let controller = try makeController(loader: gate)
        controller.eventHandler = events.record
        let stream = controller.states()
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.checkFinished(1)],
            releaseGate: { await gate.finish(with: feed) }
        )

        try await scope.run {
            let check = scope.track(Task { @MainActor in await controller.checkForUpdates() })
            try await events.wait(for: .checkStarted(1))
            await gate.finish(with: feed)
            try await events.wait(for: .checkFinished(1))

            let newestValue = try await nextValue(from: stream)
            let newest = try #require(newestValue)
            guard case let .updateAvailable(release) = newest.check else {
                Issue.record("A slow consumer received a stale state")
                return
            }
            #expect(release.id == releaseID)
            await check.value
        }
    }

    @Test("A new stream starts from the latest completed state")
    func startsFromCurrentCompletedState() async throws {
        let controller = try makeController()
        await controller.checkForUpdates()

        let stream = controller.states()
        let currentValue = try await nextValue(from: stream)
        let current = try #require(currentValue)
        guard case let .updateAvailable(release) = current.check else {
            Issue.record("The initial stream snapshot was not the completed check state")
            return
        }
        #expect(release.id == releaseID)
    }

    @Test("Check failures are values and a later check can recover")
    func publishesFailureAndRecovery() async throws {
        let feed = try makeFeed()
        let loader = SequencedLoader([.failure(.offline), .success(feed)])
        let controller = try makeController(loader: loader)
        let stream = controller.states()
        _ = try await nextValue(from: stream)

        await controller.checkForUpdates()
        let failedValue = try await nextValue(from: stream)
        let failed = try #require(failedValue)
        guard case .failed = failed.check else {
            Issue.record("A failed check did not remain a stream value")
            return
        }

        await controller.checkForUpdates()
        let recoveredValue = try await nextValue(from: stream)
        let recovered = try #require(recoveredValue)
        guard case let .updateAvailable(release) = recovered.check else {
            Issue.record("The stream did not recover after a successful check")
            return
        }
        #expect(release.id == releaseID)
    }

    @Test("Cancelling one consumer does not stop another subscription")
    func consumerCancellationIsIndependent() async throws {
        let feed = try makeFeed()
        let gate = LoadGate()
        let events = UpdateControllerEventRecorder()
        let controller = try makeController(loader: gate)
        controller.eventHandler = events.record
        let cancelledStream = controller.states()
        let survivingStream = controller.states()
        let (readiness, ready) = AsyncStream<Void>.makeStream(bufferingPolicy: .bufferingNewest(1))
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.checkFinished(1)],
            releaseGate: { await gate.finish(with: feed) }
        )
        let cancelledConsumer = scope.track(Task.detached { () -> UpdateState? in
            _ = try? await nextValue(from: cancelledStream)
            ready.yield()
            return try? await nextValue(from: cancelledStream)
        })

        try await scope.run {
            let initialValue = try await nextValue(from: survivingStream)
            let initial = try #require(initialValue)
            #expect(initial.check == .idle)
            let readyValue: Void? = try await nextValue(from: readiness)
            _ = try #require(readyValue)
            cancelledConsumer.cancel()
            #expect(await cancelledConsumer.value == nil)

            let check = scope.track(Task { @MainActor in await controller.checkForUpdates() })
            try await events.wait(for: .checkStarted(1))
            let checkingValue = try await nextValue(from: survivingStream)
            let checking = try #require(checkingValue)
            guard case .checking(previousRelease: nil) = checking.check else {
                Issue.record("The remaining consumer did not receive the state change")
                return
            }
            await gate.finish(with: feed)
            try await events.wait(for: .checkFinished(1))
            await check.value
        }
    }

    @Test("A stream does not retain its controller and finishes when it is released")
    func controllerDeallocationFinishesStream() async throws {
        var controller: UpdateController? = try makeController()
        weak var weakController: UpdateController?
        weakController = controller
        let stream = controller!.states()

        controller = nil
        #expect(weakController == nil)

        let initial = try await nextValue(from: stream)
        #expect(initial?.check == .idle)
        let end = try await nextValue(from: stream)
        #expect(end == nil)
    }

    @Test("A stream registered during a reentrant Combine callback starts from the settled snapshot")
    func reentrantCombineObserverReceivesSettledInitialSnapshot() async throws {
        let loader = CountingLoader(data: try makeFeed())
        let events = UpdateControllerEventRecorder()
        let controller = try makeController(loader: loader)
        controller.eventHandler = events.record
        var stream: AsyncStream<UpdateState>?
        var didDeactivate = false
        let subscription = controller.$state.sink { state in
            guard !didDeactivate, case .checking = state.check else {
                return
            }
            didDeactivate = true
            controller.setActive(false)
            stream = controller.states()
        }
        defer { subscription.cancel() }

        controller.setActive(true)
        try await events.wait(for: .checkStarted(1))
        try await events.wait(for: .checkFinished(1))
        try await events.wait(for: .lifecycleFinished(0))

        let stateStream = try #require(stream)
        let initialValue = try await nextValue(from: stateStream)
        let initial = try #require(initialValue)
        #expect(initial.check == .idle)
        #expect(controller.state == initial)
        #expect(await loader.count == 0)
    }

    private func makeController(loader: (any UpdateFeedLoading)? = nil) throws -> UpdateController {
        let resolvedLoader: any UpdateFeedLoading
        if let loader {
            resolvedLoader = loader
        } else {
            resolvedLoader = ImmediateLoader(result: .success(try makeFeed()))
        }
        return UpdateController(
            configuration: try makeConfiguration(),
            loader: resolvedLoader,
            currentOSVersion: currentOS,
            now: { fixedNow }
        )
    }
}

private enum StateStreamWaitError: Error {
    case timedOut
}

private func nextValue<Element: Sendable>(
    from stream: AsyncStream<Element>,
    timeout: Duration = .seconds(1)
) async throws -> Element? {
    try await withThrowingTaskGroup(of: Element?.self) { group in
        group.addTask {
            var iterator = stream.makeAsyncIterator()
            return await iterator.next()
        }
        group.addTask {
            try await waitForContentionScaledTimeout(timeout)
            throw StateStreamWaitError.timedOut
        }
        defer { group.cancelAll() }
        return try await group.next()!
    }
}
