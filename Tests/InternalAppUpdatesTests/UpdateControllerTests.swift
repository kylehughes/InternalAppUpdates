@testable import InternalAppUpdates
import Combine
import Foundation
import Testing

@MainActor
@Suite("Update controller")
struct UpdateControllerTests {
    let currentOS = OperatingSystemVersion(majorVersion: 16, minorVersion: 4, patchVersion: 0)

    @Test("Automatic presentation cancelled before its handler does not suppress the release")
    func automaticCancellationBeforeHandler() async throws {
        let gate = InstallationGate()
        let events = UpdateControllerEventRecorder()
        let store = MemoryPromptStore()
        let controller = try makeController(installationHandler: { await gate.install($0) }, promptStore: store)
        controller.eventHandler = events.record
        await controller.checkForUpdates()
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: { await gate.finish(with: false) }
        )

        try await scope.run {
            controller.setActive(true)
            controller.presentationPolicy = .automatic
            #expect(events.contains(.installationScheduled(1)))
            controller.setActive(false)
            try await events.wait(for: .installationFinished(1))

            #expect(await gate.urls.isEmpty)
            #expect(store.entries.isEmpty)
        }
    }

    @Test("An automatic release expiring after scheduling is not handed off or suppressed")
    func automaticExpirationBeforeHandler() async throws {
        let clock = LockedNow(fixedNow)
        let events = UpdateControllerEventRecorder()
        let gate = InstallationGate()
        let store = MemoryPromptStore()
        let controller = try makeController(
            configuration: makeConfiguration(presentation: .automatic),
            installationHandler: { await gate.install($0) },
            now: { clock.value() },
            promptStore: store
        )
        controller.eventHandler = { event in
            events.record(event)
            if case .installationScheduled = event {
                clock.setValue(Date(timeIntervalSince1970: 1_800_000_000))
            }
        }
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: { await gate.finish(with: false) }
        )

        try await scope.run {
            controller.setActive(true)
            try await events.wait(for: .installationScheduled(1))
            try await events.wait(for: .installationFinished(1))
            controller.setActive(false)

            #expect(await gate.urls.isEmpty)
            #expect(store.entries.isEmpty)
            #expect(controller.state.check == .failed(.releaseExpired))
        }
    }

    @Test("Automatic presentation happens once per release")
    func automaticOnce() async throws {
        let events = UpdateControllerEventRecorder()
        let recorder = InstallationRecorder(result: false)
        let store = MemoryPromptStore()
        let controller = try makeController(
            configuration: makeConfiguration(presentation: .automatic),
            installationHandler: { await recorder.install($0) },
            promptStore: store
        )
        controller.eventHandler = events.record

        controller.setActive(true)
        try await events.wait(for: .installationFinished(1))
        let baseline = events.snapshotIndex()
        await controller.checkForUpdates()
        try await events.wait(for: .checkFinished(2), after: baseline)
        controller.setActive(false)

        #expect(await recorder.urls.count == 1)
        #expect(events.count(.installationScheduled(1)) == 1)
        #expect(store.entries.count == 1)
    }

    @Test("Cancelling one caller cancels the shared check")
    func callerCancellation() async throws {
        let feed = try makeFeed()
        let events = UpdateControllerEventRecorder()
        let gate = LoadGate()
        let controller = try makeController(loader: gate)
        controller.eventHandler = events.record
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.checkFinished(1)],
            releaseGate: { await gate.finish(with: feed) }
        )

        try await scope.run {
            let first = scope.track(Task { await controller.checkForUpdates() })
            try await events.wait(for: .checkStarted(1))
            try await gate.waitUntilStarted()
            let cancellingWaiter = scope.track(Task { await controller.checkForUpdates() })
            try await events.wait(for: .checkJoined(1))
            cancellingWaiter.cancel()
            await gate.finish(with: feed)
            try await events.wait(for: .checkFinished(1))
            await first.value
            await cancellingWaiter.value

            #expect(await gate.observedCancellation)
            #expect(controller.state.check == .idle)
        }
    }

    @Test("Cancelling a refresh restores only an eligible prior release")
    func cancellationRevalidatesRestoredRelease() async throws {
        for shouldExpire in [false, true] {
            let clock = LockedNow(fixedNow)
            let loader = PerRequestLoadGate()
            let events = UpdateControllerEventRecorder()
            let controller = try makeController(loader: loader, now: { clock.value() })
            controller.eventHandler = events.record
            let feed = try makeFeed()
            let scope = UpdateControllerGateTestScope(
                controller: controller,
                eventRecorder: events,
                expectedFinishEvents: [],
                releaseGate: {
                    await loader.finish(request: 1, with: feed)
                    await loader.finish(request: 2, with: feed)
                }
            )

            try await scope.run {
                let initialCheck = scope.track(Task { await controller.checkForUpdates() })
                try await loader.waitUntilStarted(count: 1)
                await loader.finish(request: 1, with: feed)
                try await events.wait(for: .checkFinished(1))
                await initialCheck.value

                let refresh = scope.track(Task { await controller.checkForUpdates() })
                try await loader.waitUntilStarted(count: 2)
                if shouldExpire {
                    clock.setValue(Date(timeIntervalSince1970: 1_800_000_000))
                }
                refresh.cancel()
                await loader.finish(request: 2, with: feed)
                try await events.wait(for: .checkFinished(2))
                await refresh.value
            }

            if shouldExpire {
                #expect(controller.state.check == .failed(.releaseExpired))
                #expect(controller.availableRelease == nil)
            } else {
                #expect(controller.availableRelease?.id == releaseID)
            }
        }
    }

    @Test("Deactivating during a refresh restores only an eligible prior release")
    func deactivationRevalidatesRestoredRelease() async throws {
        for shouldExpire in [false, true] {
            let clock = LockedNow(fixedNow)
            let loader = PerRequestLoadGate()
            let events = UpdateControllerEventRecorder()
            let controller = try makeController(loader: loader, now: { clock.value() })
            controller.eventHandler = events.record
            let feed = try makeFeed()
            controller.setActive(true)
            let scope = UpdateControllerGateTestScope(
                controller: controller,
                eventRecorder: events,
                expectedFinishEvents: [.lifecycleFinished(0)],
                releaseGate: {
                    await loader.finish(request: 1, with: feed)
                    await loader.finish(request: 2, with: feed)
                }
            )

            try await scope.run {
                try await loader.waitUntilStarted(count: 1)
                await loader.finish(request: 1, with: feed)
                try await events.wait(for: .checkFinished(1))

                let refresh = scope.track(Task { await controller.checkForUpdates() })
                try await loader.waitUntilStarted(count: 2)
                if shouldExpire {
                    clock.setValue(Date(timeIntervalSince1970: 1_800_000_000))
                }
                controller.setActive(false)
                await loader.finish(request: 2, with: feed)
                try await events.wait(for: .checkFinished(2))
                try await events.wait(for: .lifecycleFinished(0))
                await refresh.value
            }

            if shouldExpire {
                #expect(controller.state.check == .failed(.releaseExpired))
                #expect(controller.availableRelease == nil)
            } else {
                #expect(controller.availableRelease?.id == releaseID)
            }
        }
    }

    @Test("A custom loader cannot exceed the controller feed-size limit")
    func customLoaderResponseSizeIsEnforced() async throws {
        let feed = try makeFeed()
        let exactLimitFeed = feed + Data(repeating: 0x20, count: UpdateController.maximumFeedSize - feed.count)
        let oversizedFeed = exactLimitFeed + Data([0x20])

        let accepted = try makeController(loader: ImmediateLoader(result: .success(exactLimitFeed)))
        await accepted.checkForUpdates()
        #expect(accepted.availableRelease?.id == releaseID)

        let rejected = try makeController(loader: ImmediateLoader(result: .success(oversizedFeed)))
        await rejected.checkForUpdates()
        guard case .failed(.feedLoadingFailed) = rejected.state.check else {
            Issue.record("The controller accepted a custom-loader response over its byte limit")
            return
        }
    }

    @Test("Cancelling an automatic handoff does not present the same release again")
    func cancelledAutomaticHandoffDoesNotReprompt() async throws {
        let events = UpdateControllerEventRecorder()
        let gate = InstallationGate()
        let store = MemoryPromptStore()
        let controller = try makeController(
            configuration: makeConfiguration(presentation: .automatic),
            installationHandler: { await gate.install($0) },
            promptStore: store
        )
        controller.eventHandler = events.record
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: { await gate.finish(with: false) }
        )

        try await scope.run {
            controller.setActive(true)
            try await gate.waitUntilStarted()
            controller.setActive(false)
            await gate.finish(with: false)
            try await events.wait(for: .installationFinished(1))
            let baseline = events.snapshotIndex()
            controller.setActive(true)
            try await events.wait(for: .checkFinished(2), after: baseline)
            controller.setActive(false)

            #expect(await gate.urls.count == 1)
            #expect(events.count(.installationScheduled(1)) == 1)
            #expect(store.entries.count == 1)
        }
    }

    @Test("Concurrent checks share one load")
    func coalescedChecks() async throws {
        let feed = try makeFeed()
        let events = UpdateControllerEventRecorder()
        let gate = LoadGate()
        let controller = try makeController(loader: gate)
        controller.eventHandler = events.record
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.checkFinished(1)],
            releaseGate: { await gate.finish(with: feed) }
        )

        try await scope.run {
            let first = scope.track(Task { await controller.checkForUpdates() })
            try await events.wait(for: .checkStarted(1))
            try await gate.waitUntilStarted()
            let second = scope.track(Task { await controller.checkForUpdates() })
            try await events.wait(for: .checkJoined(1))
            await gate.finish(with: feed)
            try await events.wait(for: .checkFinished(1))
            await first.value
            await second.value

            #expect(await gate.loadCount == 1)
            #expect(controller.availableRelease?.id == releaseID)
        }
    }

    @Test("An expired release is revalidated before handoff")
    func expirationBeforeHandoff() async throws {
        let clock = LockedNow(fixedNow)
        let recorder = InstallationRecorder(result: true)
        let controller = try makeController(installationHandler: { await recorder.install($0) }, now: { clock.value() })
        await controller.checkForUpdates()
        clock.setValue(Date(timeIntervalSince1970: 1_800_000_000))

        #expect(!controller.canRequestInstallation)
        #expect(await controller.requestInstallation() == false)
        #expect(await recorder.urls.isEmpty)
        #expect(controller.state.check == .failed(.releaseExpired))
    }

    @Test("An expired release is rejected and cleared")
    func expiredCurrentReleaseIsRejected() async throws {
        let expiredFeed = try makeFeed(release: makeReleaseObject(
            publishedAt: "2025-01-01T00:00:00Z", expiresAt: "2026-01-01T00:00:00Z"
        ))
        let controller = try makeController(loader: ImmediateLoader(result: .success(expiredFeed)))

        await controller.checkForUpdates()

        #expect(controller.state.check == .failed(.releaseExpired))
        #expect(controller.availableRelease == nil)
    }

    @Test("Concurrent installation requests are suppressed while busy")
    func installationBusyState() async throws {
        let events = UpdateControllerEventRecorder()
        let gate = InstallationGate()
        let controller = try makeController(installationHandler: { await gate.install($0) })
        controller.eventHandler = events.record
        await controller.checkForUpdates()
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: { await gate.finish(with: false) }
        )

        try await scope.run {
            let first = scope.track(Task { await controller.requestInstallation() })
            try await gate.waitUntilStarted()
            #expect(await controller.requestInstallation() == false)
            await gate.finish(with: true)
            try await events.wait(for: .installationFinished(1))

            #expect(await first.value)
            #expect(await gate.urls.count == 1)
        }
    }

    @Test("Installation completion preserves a newer feed failure while busy")
    func installationDoesNotOverwriteNewerFailure() async throws {
        let events = UpdateControllerEventRecorder()
        let loader = SequencedLoader([.success(try makeFeed()), .failure(.offline)])
        let gate = InstallationGate()
        let controller = try makeController(loader: loader, installationHandler: { await gate.install($0) })
        controller.eventHandler = events.record
        await controller.checkForUpdates()
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: { await gate.finish(with: false) }
        )

        try await scope.run {
            let installation = scope.track(Task { await controller.requestInstallation() })
            try await gate.waitUntilStarted()
            await controller.checkForUpdates()
            #expect(controller.status == .openingInstaller)
            guard case .failed(.feedLoadingFailed) = controller.state.check else {
                Issue.record("Expected the latest check failure while the installer is busy")
                return
            }
            await gate.finish(with: false)
            try await events.wait(for: .installationFinished(1))

            #expect(await installation.value == false)
            guard case .failed(.feedLoadingFailed) = controller.status else {
                Issue.record("Installation completion overwrote the newer feed failure")
                return
            }
        }
    }

    @Test("Installation completion preserves a newer withdrawn feed while busy")
    func installationDoesNotOverwriteNewerStatus() async throws {
        let withdrawnFeed = try makeFeed(release: NSNull())
        let events = UpdateControllerEventRecorder()
        let loader = SequencedLoader([.success(try makeFeed()), .success(withdrawnFeed)])
        let gate = InstallationGate()
        let controller = try makeController(loader: loader, installationHandler: { await gate.install($0) })
        controller.eventHandler = events.record
        await controller.checkForUpdates()
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: { await gate.finish(with: false) }
        )

        try await scope.run {
            let installation = scope.track(Task { await controller.requestInstallation() })
            try await gate.waitUntilStarted()
            await controller.checkForUpdates()
            #expect(controller.status == .openingInstaller)
            #expect(controller.state.check == .upToDate)
            await gate.finish(with: true)
            try await events.wait(for: .installationFinished(1))

            #expect(await installation.value)
            #expect(controller.status == .upToDate)
        }
    }

    @Test("Manual caller cancellation is forwarded to an active handler")
    func manualCallerCancellationPropagates() async throws {
        let events = UpdateControllerEventRecorder()
        let gate = InstallationGate()
        let controller = try makeController(installationHandler: { await gate.install($0) })
        controller.eventHandler = events.record
        await controller.checkForUpdates()
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: { await gate.finish(with: false) }
        )

        try await scope.run {
            let request = scope.track(Task { await controller.requestInstallation() })
            try await gate.waitUntilStarted()
            request.cancel()
            await gate.finish(with: false)
            try await events.wait(for: .installationFinished(1))
            _ = await request.value

            #expect(await gate.observedCancellation)
        }
    }

    @Test("An already-cancelled manual request does not schedule installation")
    func manualRequestAlreadyCancelledDoesNotScheduleInstallation() async throws {
        let events = UpdateControllerEventRecorder()
        let recorder = InstallationRecorder(result: true)
        let controller = try makeController(installationHandler: { await recorder.install($0) })
        controller.eventHandler = events.record
        await controller.checkForUpdates()

        let request = Task { () -> Bool in
            withUnsafeCurrentTask {
                $0?.cancel()
            }
            return await controller.requestInstallation()
        }

        #expect(await request.value == false)
        #expect(!events.contains(.installationScheduled(1)))
        #expect(await recorder.urls.isEmpty)
    }

    @Test("Manual handoff can be retried after failure")
    func manualRetry() async throws {
        let recorder = InstallationRecorder(result: false)
        let controller = try makeController(installationHandler: { await recorder.install($0) })
        await controller.checkForUpdates()

        #expect(await controller.requestInstallation() == false)
        #expect(await controller.requestInstallation() == false)
        #expect(await recorder.urls.count == 2)
    }

    @Test("Offline failures are published")
    func offlineFailure() async throws {
        let controller = try makeController(loader: ImmediateLoader(result: .failure(.offline)))
        await controller.checkForUpdates()

        guard case .failed(.feedLoadingFailed) = controller.state.check else {
            Issue.record("Expected a feed-loading failure")
            return
        }
        #expect(controller.availableRelease == nil)
    }

    @Test("A pre-seeded prompt persistence key remains readable")
    func preseededPromptPersistenceRemainsReadable() {
        let suiteName = "InternalAppUpdatesTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer {
            defaults.removePersistentDomain(forName: suiteName)
        }
        defaults.set(true, forKey: "InternalAppUpdates.presented.ZmVlZC1hbmQtYnVuZGxl.release-a")
        #expect(UserDefaultsUpdatePromptStore(defaults: defaults).hasPresentedRelease(id: "release-a", scope: "feed-and-bundle"))
    }

    @Test("Prompt persistence treats only CFBoolean values as Boolean markers")
    func promptStoreConservativelyReadsHistoricalValues() {
        let suiteName = "InternalAppUpdatesTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer { defaults.removePersistentDomain(forName: suiteName) }
        let store = UserDefaultsUpdatePromptStore(defaults: defaults)
        let scope = "fixed-historical-scope"
        let prefix = "InternalAppUpdates.presented.Zml4ZWQtaGlzdG9yaWNhbC1zY29wZQ==."

        #expect(!store.hasPresentedRelease(id: "missing", scope: scope))
        defaults.set(false, forKey: "\(prefix)false")
        defaults.set(true, forKey: "\(prefix)true")
        defaults.set(NSNumber(value: 0), forKey: "\(prefix)zero")
        defaults.set(NSNumber(value: 1), forKey: "\(prefix)number")
        defaults.set("false", forKey: "\(prefix)false-string")
        defaults.set("true", forKey: "\(prefix)string")
        defaults.set(Data([0x01]), forKey: "\(prefix)data")
        defaults.set(["marker"], forKey: "\(prefix)array")
        defaults.set(["marker": "value"], forKey: "\(prefix)dictionary")

        #expect(!store.hasPresentedRelease(id: "false", scope: scope))
        #expect(store.hasPresentedRelease(id: "true", scope: scope))
        #expect(store.hasPresentedRelease(id: "zero", scope: scope))
        #expect(store.hasPresentedRelease(id: "number", scope: scope))
        #expect(store.hasPresentedRelease(id: "false-string", scope: scope))
        #expect(store.hasPresentedRelease(id: "string", scope: scope))
        #expect(store.hasPresentedRelease(id: "data", scope: scope))
        #expect(store.hasPresentedRelease(id: "array", scope: scope))
        #expect(store.hasPresentedRelease(id: "dictionary", scope: scope))

        store.markReleasePresented(id: "round-trip", scope: scope)
        #expect(store.hasPresentedRelease(id: "round-trip", scope: scope))
    }

    @Test("Prompt persistence remembers every presented release")
    func promptStoreRetainsReleaseHistory() {
        let suiteName = "InternalAppUpdatesTests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suiteName)!
        defer {
            defaults.removePersistentDomain(forName: suiteName)
        }
        let store = UserDefaultsUpdatePromptStore(defaults: defaults)

        store.markReleasePresented(id: "release-a", scope: "feed-and-bundle")
        store.markReleasePresented(id: "release-b", scope: "feed-and-bundle")

        #expect(store.hasPresentedRelease(id: "release-a", scope: "feed-and-bundle"))
        #expect(store.hasPresentedRelease(id: "release-b", scope: "feed-and-bundle"))
        #expect(!store.hasPresentedRelease(id: "release-a", scope: "other-feed"))
    }

    @Test("A published state snapshot keeps check and installation facts coherent")
    func publishedStateIsCoherentDuringInstallation() async throws {
        var states: [UpdateState] = []
        let events = UpdateControllerEventRecorder()
        let gate = InstallationGate()
        let controller = try makeController(installationHandler: { await gate.install($0) })
        controller.eventHandler = events.record
        let subscription = controller.$state.sink {
            states.append($0)
        }
        defer {
            subscription.cancel()
        }
        await controller.checkForUpdates()
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.installationFinished(1)],
            releaseGate: { await gate.finish(with: false) }
        )

        try await scope.run {
            let installation = scope.track(Task { await controller.requestInstallation() })
            try await gate.waitUntilStarted()

            #expect(states.contains {
                if case let .updateAvailable(release) = $0.check {
                    return release.id == releaseID && $0.installingRelease?.id == releaseID
                }
                return false
            })
            await gate.finish(with: true)
            try await events.wait(for: .installationFinished(1))
            _ = await installation.value
        }
    }

    @Test("A response completing after deactivation is ignored")
    func staleCompletionAfterBackgrounding() async throws {
        let feed = try makeFeed()
        let events = UpdateControllerEventRecorder()
        let gate = LoadGate()
        let recorder = InstallationRecorder(result: true)
        let store = MemoryPromptStore()
        let controller = try makeController(
            configuration: makeConfiguration(presentation: .automatic), loader: gate,
            installationHandler: { await recorder.install($0) }, promptStore: store
        )
        controller.eventHandler = events.record
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.checkFinished(1)],
            releaseGate: { await gate.finish(with: feed) }
        )

        try await scope.run {
            controller.setActive(true)
            try await events.wait(for: .checkStarted(1))
            try await gate.waitUntilStarted()
            controller.setActive(false)
            await gate.finish(with: feed)
            try await events.wait(for: .checkFinished(1))

            #expect(controller.availableRelease == nil)
            #expect(controller.status == .idle)
            #expect(await recorder.urls.isEmpty)
            #expect(store.entries.isEmpty)
        }
    }

    private func makeController(
        configuration: UpdateConfiguration? = nil,
        loader: (any UpdateFeedLoading)? = nil,
        installationHandler: @escaping UpdateInstallationHandler = UpdateController.systemInstallationHandler,
        now: @escaping @Sendable () -> Date = { fixedNow },
        promptStore: any UpdatePromptStoring = MemoryPromptStore()
    ) throws -> UpdateController {
        let resolvedConfiguration: UpdateConfiguration
        if let configuration {
            resolvedConfiguration = configuration
        } else {
            resolvedConfiguration = try makeConfiguration()
        }
        let resolvedLoader: any UpdateFeedLoading
        if let loader {
            resolvedLoader = loader
        } else {
            resolvedLoader = ImmediateLoader(result: .success(try makeFeed()))
        }
        return UpdateController(
            configuration: resolvedConfiguration,
            loader: resolvedLoader,
            installationHandler: installationHandler,
            promptStore: promptStore,
            currentOSVersion: currentOS,
            now: now
        )
    }
}

private final class LockedNow: @unchecked Sendable {
    private let lock = NSLock()
    private var storedValue: Date

    init(_ value: Date) { storedValue = value }

    func setValue(_ value: Date) {
        lock.lock()
        storedValue = value
        lock.unlock()
    }

    func value() -> Date {
        lock.lock()
        defer { lock.unlock() }
        return storedValue
    }
}
