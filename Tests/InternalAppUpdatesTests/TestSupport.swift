@testable import InternalAppUpdates
import Foundation
import Testing

let releaseID = String(repeating: "a", count: 64)
let fixedNow = Date(timeIntervalSince1970: 1_792_000_000)

func waitForContentionScaledTimeout(_ timeout: Duration) async throws {
    var remaining = timeout
    while remaining > .zero {
        let slice = min(remaining, .milliseconds(25))
        try await Task.sleep(for: slice)
        remaining -= slice
    }
}

@MainActor
final class UpdateControllerGateTestScope {
    private let controller: UpdateController
    private let eventRecorder: UpdateControllerEventRecorder
    private let expectedFinishEvents: [UpdateControllerEvent]
    private let releaseGate: @MainActor () async -> Void
    private var awaitTasks: [@MainActor () async -> Void] = []
    private var cancelTasks: [@MainActor () -> Void] = []

    init(
        controller: UpdateController,
        eventRecorder: UpdateControllerEventRecorder,
        expectedFinishEvents: [UpdateControllerEvent],
        releaseGate: @escaping @MainActor () async -> Void
    ) {
        self.controller = controller
        self.eventRecorder = eventRecorder
        self.expectedFinishEvents = expectedFinishEvents
        self.releaseGate = releaseGate
    }

    func run<T>(_ operation: () async throws -> T) async throws -> T {
        let outcome: Result<T, any Error>
        do {
            outcome = .success(try await operation())
        } catch {
            outcome = .failure(error)
        }

        // An unstructured task starts uncancelled, so teardown still runs when the
        // test operation failed with cancellation. Awaiting it keeps teardown scoped.
        await Task { @MainActor in
            await self.cleanUp()
        }.value
        return try outcome.get()
    }

    func track<Success: Sendable>(_ task: Task<Success, Never>) -> Task<Success, Never> {
        cancelTasks.append {
            task.cancel()
        }
        awaitTasks.append {
            _ = await task.value
        }
        return task
    }

    private func cleanUp() async {
        for cancelTask in cancelTasks {
            cancelTask()
        }
        controller.setActive(false)
        await releaseGate()
        for event in expectedFinishEvents {
            do {
                try await eventRecorder.wait(for: event)
            } catch {
                Issue.record("Cleanup did not observe controller event \(event): \(error)")
            }
        }
        for awaitTask in awaitTasks {
            await awaitTask()
        }
    }
}

func makeFeed(
    schemaVersion: Int = 1,
    bundleIdentifier: String = "com.example.App",
    release: Any? = nil,
    extraRoot: [String: Any] = [:]
) throws -> Data {
    var object: [String: Any] = [
        "schemaVersion": schemaVersion,
        "bundleIdentifier": bundleIdentifier,
        "release": release ?? makeReleaseObject(),
    ]
    object.merge(extraRoot) { _, new in new }
    return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
}

func makeReleaseObject(
    id: String = releaseID,
    version: String = "1.1",
    build: String = "1",
    minimumOSVersion: String = "16.0",
    publishedAt: String = "2026-10-14T00:00:00Z",
    expiresAt: String = "2027-01-01T00:00:00Z",
    manifestURL: String = "https://updates.example.com/app/manifest.plist",
    releaseNotes: Any? = nil,
    extra: [String: Any] = [:]
) -> [String: Any] {
    var object: [String: Any] = [
        "id": id,
        "version": version,
        "build": build,
        "minimumOSVersion": minimumOSVersion,
        "publishedAt": publishedAt,
        "expiresAt": expiresAt,
        "manifestURL": manifestURL,
    ]
    if let releaseNotes { object["releaseNotes"] = releaseNotes }
    object.merge(extra) { _, new in new }
    return object
}

func makeInstalledBuild(version: String = "1.0", build: String = "1") throws -> InstalledBuild {
    try InstalledBuild(bundleIdentifier: "com.example.App", version: version, build: build)
}

func makeConfiguration(
    installedBuild: InstalledBuild? = nil,
    presentation: UpdatePresentationPolicy = .manual
) throws -> UpdateConfiguration {
    try UpdateConfiguration(
        feedURL: URL(string: "https://updates.example.com/feed.json")!,
        installedBuild: installedBuild ?? makeInstalledBuild(),
        presentation: presentation,
        checkInterval: 3_600
    )
}

struct ImmediateLoader: UpdateFeedLoading {
    let result: Result<Data, StubError>

    func load(from url: URL, maximumResponseSize: Int) async throws -> Data {
        try result.get()
    }
}

enum StubError: Error, Sendable {
    case offline
}

actor LoadGate: UpdateFeedLoading {
    private struct StartWaiter {
        let continuation: CheckedContinuation<Void, any Error>
        let count: Int
        let timeoutTask: Task<Void, Never>
    }

    private(set) var loadCount = 0
    private(set) var observedCancellation = false
    private var completedResult: Data?
    private var resultWaiters: [CheckedContinuation<Data, Never>] = []
    private var startWaiters: [UUID: StartWaiter] = [:]

    func finish(with data: Data) {
        guard completedResult == nil else {
            return
        }
        completedResult = data
        let waiters = resultWaiters
        resultWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: data)
        }
    }

    func load(from url: URL, maximumResponseSize: Int) async throws -> Data {
        loadCount += 1
        finishStartedWaiters()
        if let completedResult {
            observedCancellation = Task.isCancelled
            return completedResult
        }
        let data = await withCheckedContinuation {
            resultWaiters.append($0)
        }
        observedCancellation = Task.isCancelled
        return data
    }

    func waitUntilStarted(count: Int = 1, timeout: Duration = .seconds(1)) async throws {
        guard loadCount < count else {
            return
        }
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
                    await self?.finishStartWaiter(id, result: .failure(EventWaitError.timedOutStart))
                }
                startWaiters[id] = StartWaiter(continuation: continuation, count: count, timeoutTask: timeoutTask)
            }
        } onCancel: {
            Task {
                await self.finishStartWaiter(id, result: .failure(CancellationError()))
            }
        }
    }

    private func finishStartWaiter(_ id: UUID, result: Result<Void, any Error>) {
        guard let waiter = startWaiters.removeValue(forKey: id) else {
            return
        }
        waiter.timeoutTask.cancel()
        waiter.continuation.resume(with: result)
    }

    private func finishStartedWaiters() {
        let readyIDs = startWaiters.compactMap { id, waiter in
            loadCount >= waiter.count ? id : nil
        }
        for id in readyIDs {
            finishStartWaiter(id, result: .success(()))
        }
    }
}

actor SequencedLoader: UpdateFeedLoading {
    private var results: [Result<Data, StubError>]

    init(_ results: [Result<Data, StubError>]) {
        self.results = results
    }

    func load(from url: URL, maximumResponseSize: Int) async throws -> Data {
        try results.removeFirst().get()
    }
}

actor InstallationRecorder {
    private(set) var urls: [URL] = []
    let result: Bool

    init(result: Bool) {
        self.result = result
    }

    func install(_ url: URL) -> Bool {
        urls.append(url)
        return result
    }
}

actor InstallationGate {
    private struct StartWaiter {
        let continuation: CheckedContinuation<Void, any Error>
        let count: Int
        let timeoutTask: Task<Void, Never>
    }

    private(set) var observedCancellation = false
    private(set) var urls: [URL] = []
    private var completedResult: Bool?
    private var resultWaiters: [CheckedContinuation<Bool, Never>] = []
    private var startWaiters: [UUID: StartWaiter] = [:]

    func finish(with result: Bool) {
        guard completedResult == nil else {
            return
        }
        completedResult = result
        let waiters = resultWaiters
        resultWaiters.removeAll()
        for waiter in waiters {
            waiter.resume(returning: result)
        }
    }

    func install(_ url: URL) async -> Bool {
        urls.append(url)
        finishStartedWaiters()
        if let completedResult {
            observedCancellation = Task.isCancelled
            return completedResult
        }
        let result = await withCheckedContinuation {
            resultWaiters.append($0)
        }
        observedCancellation = Task.isCancelled
        return result
    }

    func waitUntilStarted(count: Int = 1, timeout: Duration = .seconds(1)) async throws {
        guard urls.count < count else {
            return
        }
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
                    await self?.finishStartWaiter(id, result: .failure(EventWaitError.timedOutStart))
                }
                startWaiters[id] = StartWaiter(continuation: continuation, count: count, timeoutTask: timeoutTask)
            }
        } onCancel: {
            Task {
                await self.finishStartWaiter(id, result: .failure(CancellationError()))
            }
        }
    }

    private func finishStartWaiter(_ id: UUID, result: Result<Void, any Error>) {
        guard let waiter = startWaiters.removeValue(forKey: id) else {
            return
        }
        waiter.timeoutTask.cancel()
        waiter.continuation.resume(with: result)
    }

    private func finishStartedWaiters() {
        let readyIDs = startWaiters.compactMap { id, waiter in
            urls.count >= waiter.count ? id : nil
        }
        for id in readyIDs {
            finishStartWaiter(id, result: .success(()))
        }
    }
}

enum EventWaitError: Error {
    case timedOut(UpdateControllerEvent)
    case timedOutStart
}

@MainActor
final class UpdateControllerEventRecorder {
    private struct Waiter {
        let after: Int
        let continuation: CheckedContinuation<Void, any Error>
        let event: UpdateControllerEvent
        let timeoutTask: Task<Void, Never>
    }

    private var events: [UpdateControllerEvent] = []
    private var waiters: [UUID: Waiter] = [:]

    func contains(_ event: UpdateControllerEvent, after index: Int = 0) -> Bool {
        events.dropFirst(index).contains(event)
    }

    func count(_ event: UpdateControllerEvent) -> Int {
        events.filter { $0 == event }.count
    }

    func record(_ event: UpdateControllerEvent) {
        events.append(event)
        let readyIDs = waiters.compactMap { id, waiter in
            waiter.event == event && events.dropFirst(waiter.after).contains(event) ? id : nil
        }
        for id in readyIDs {
            finishWaiter(id, result: .success(()))
        }
    }

    func snapshotIndex() -> Int {
        events.count
    }

    func wait(
        for event: UpdateControllerEvent,
        after index: Int = 0,
        timeout: Duration = .seconds(1)
    ) async throws {
        guard !contains(event, after: index) else {
            return
        }
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
                    self?.finishWaiter(id, result: .failure(EventWaitError.timedOut(event)))
                }
                waiters[id] = Waiter(
                    after: index,
                    continuation: continuation,
                    event: event,
                    timeoutTask: timeoutTask
                )
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                self?.finishWaiter(id, result: .failure(CancellationError()))
            }
        }
    }

    private func finishWaiter(_ id: UUID, result: Result<Void, any Error>) {
        guard let waiter = waiters.removeValue(forKey: id) else {
            return
        }
        waiter.timeoutTask.cancel()
        waiter.continuation.resume(with: result)
    }
}

@MainActor
final class MemoryPromptStore: UpdatePromptStoring {
    private(set) var entries: Set<String> = []

    func hasPresentedRelease(id: String, scope: String) -> Bool {
        entries.contains("\(scope)|\(id)")
    }

    func markReleasePresented(id: String, scope: String) {
        entries.insert("\(scope)|\(id)")
    }
}
