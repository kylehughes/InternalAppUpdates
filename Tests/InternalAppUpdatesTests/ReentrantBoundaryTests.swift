@testable import InternalAppUpdates
import Foundation
import Testing

@MainActor
@Suite("Reentrant installation boundaries")
struct ReentrantBoundaryTests {
    @Test("A clock changing automatic policy after persistence prevents handoff")
    func clockPolicyChangeBeforeHandoff() async throws {
        let clock = ReentrantClock()
        let events = UpdateControllerEventRecorder()
        let recorder = InstallationRecorder(result: true)
        let store = ReentrantPromptStore()
        let controller = UpdateController(
            configuration: try makeConfiguration(presentation: .automatic),
            loader: ImmediateLoader(result: .success(try makeFeed())),
            installationHandler: { await recorder.install($0) },
            promptStore: store,
            currentOSVersion: OperatingSystemVersion(majorVersion: 16, minorVersion: 4, patchVersion: 0),
            now: { MainActor.assumeIsolated { clock.value() } }
        )
        controller.eventHandler = events.record
        store.onMark = { [weak controller] in
            // Marking arms only the final eligibility check, after the initial policy guard has passed.
            clock.onRead = { [weak controller] in controller?.presentationPolicy = .manual }
        }
        let scope = UpdateControllerGateTestScope(
            controller: controller,
            eventRecorder: events,
            expectedFinishEvents: [.checkFinished(1), .installationFinished(1), .lifecycleFinished(0)],
            releaseGate: {}
        )
        try await scope.run {
            controller.setActive(true)
            try await events.wait(for: .installationFinished(1))
            #expect(controller.presentationPolicy == .manual)
            #expect(await recorder.urls.isEmpty)
            #expect(controller.state.installingRelease == nil)
        }
    }
}

@MainActor
private final class ReentrantClock {
    var onRead: (() -> Void)?

    func value() -> Date {
        let action = onRead
        onRead = nil
        action?()
        return fixedNow
    }
}

@MainActor
private final class ReentrantPromptStore: UpdatePromptStoring {
    var onMark: (() -> Void)?
    private var presented = false

    func hasPresentedRelease(id: String, scope: String) -> Bool {
        presented
    }

    func markReleasePresented(id: String, scope: String) {
        presented = true
        onMark?()
    }
}
