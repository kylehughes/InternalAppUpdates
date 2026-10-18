# Integrate the Swift Package

Internal App Updates checks a feed while your app is active and hands an available release to the iOS installer. Keep the updater in your internal app configuration, and retain one controller for each app/feed lifecycle. See the [README](../README.md#installation) for package installation.

Create one controller for the app's update lifecycle:

```swift
import Foundation
import InternalAppUpdates

@MainActor
func makeUpdateController(feedURL: URL) throws -> UpdateController {
    let configuration = try UpdateConfiguration(
        feedURL: feedURL,
        installedBuild: InstalledBuild(),
        presentation: .manual,
        checkInterval: 60
    )
    return UpdateController(configuration: configuration)
}
```

Feed URLs identify channels. A different app, team, or branch can have its own directory and feed. The controller rejects malformed feeds and feeds for a different bundle identifier. A valid feed for the installed build or an older release means the app is up to date. Expiration and OS compatibility are checked before offering a newer release.

## SwiftUI

Retain the controller at the scene root and connect it to scene activity. Supply your own button label, layout, and localized copy:

```swift
import InternalAppUpdates
import SwiftUI

struct InternalUpdatesView: View {
    @Environment(\.scenePhase) private var scenePhase
    @ObservedObject var updates: UpdateController

    var body: some View {
        VStack {
            Button("Check for Updates") {
                Task { await updates.checkForUpdates() }
            }
            if let release = updates.availableRelease {
                Text("Build \(release.build)")
                UpdateButton(controller: updates) {
                    Label("Install Update", systemImage: "arrow.down.circle")
                }
            }
        }
        .task { updates.setActive(scenePhase == .active) }
        .onChange(of: scenePhase) { phase in
            updates.setActive(phase == .active)
        }
    }
}
```

The scene owner should deactivate the controller when its scene disconnects. For a multiwindow app, use one owner that considers the app active while any participating scene is active; do not let one window's background transition cancel another window's updater.

Set `presentation: .automatic` to request the native installation prompt once per release while active. Change `presentationPolicy` at runtime to support a preference. Automatic attempts are remembered per feed and bundle in UserDefaults. Dismissing the system prompt does not cause repeated automatic prompts; the install button remains available. A missing or Boolean `false` marker permits an automatic attempt. A Boolean `true` or a malformed stored marker suppresses it, so a malformed marker does not trigger an automatic retry. Manual installation remains available when the release is eligible.

## UIKit and Custom Presentation

The same controller works without SwiftUI. Retain it in your scene coordinator, call `setActive(true)` from `sceneDidBecomeActive`, and call `setActive(false)` from `sceneWillResignActive` and `sceneDidDisconnect`. Scene activity controls polling; state observation only observes and does not activate polling.

Use `states()` when a UIKit view controller consumes snapshots with Swift concurrency. Store the observation task, cancel it when the view disappears or deinitializes, and create a new task when it appears again. The weak capture avoids retaining a view controller that is no longer visible. Apply the snapshot to the visible UI, then read `canRequestInstallation` from the authoritative controller when deciding whether to enable a manual install button:

```swift
private var stateTask: Task<Void, Never>?

override func viewWillAppear(_ animated: Bool) {
    super.viewWillAppear(animated)
    stateTask?.cancel()
    stateTask = Task { [weak self, updates] in
        for await state in updates.states() {
            guard let self else { return }
            self.installButton.isHidden = state.availableRelease == nil
            self.installButton.isEnabled = updates.canRequestInstallation
            // Update progress, release notes, and errors from `state`.
        }
    }
}

override func viewDidDisappear(_ animated: Bool) {
    super.viewDidDisappear(animated)
    stateTask?.cancel()
    stateTask = nil
}

deinit {
    stateTask?.cancel()
}

@IBAction private func installUpdate() {
    Task { [updates] in
        _ = await updates.requestInstallation()
    }
}
```

Every `states()` call registers an independent subscription synchronously and buffers the current authoritative snapshot. The stream retains only the newest unconsumed snapshot, including when state changes before its first consumption, so do not use it to account for every transition. Broadcasts occur after Combine publication and any synchronous observer reentrancy have drained. A failed check is delivered in an `UpdateState`; it does not throw or finish the stream. Canceling the consuming task ends that subscription only. The stream does not retain the controller, and it finishes when the controller is deallocated. Give each consumer its own stream; do not share a stream between view controllers or other consumers.

`$state` remains available for Combine consumers. Use `canRequestInstallation` to enable a custom install button. A button action can call:

```swift
Task { @MainActor in
    let handedToSystem = await updates.requestInstallation()
    // This confirms only that the URL handler accepted the request.
    // It does not mean the user confirmed, or that installation finished.
}
```

Save unsaved app state before requesting installation. For another presentation flow, use `.manual` and present your own release notes or confirmation interface. The injectable `installationHandler` receives the complete `itms-services` URL, so tests can observe requests without opening the installer.

Checks run on activation and then at the configured interval while active. Deactivation cancels work and prevents stale responses from presenting an update. `checkForUpdates()` also supports explicit checks. The default loader limits feed responses to 256 KiB and rejects insecure redirects. The controller also rejects an oversized body returned by a custom loader before decoding it. Custom `UpdateFeedLoading` implementations can supply authenticated feed requests; their credentials are not transferred to the installer.

## Lifecycle and Customization

The controller is isolated to `MainActor`. Its published `state` contains `check`, an enum carrying the latest check result or a release, and `installingRelease`, the release being handed to the installer. A check can finish during a handoff, so both facts remain available. The computed `status` gives an active handoff precedence; inspect `state.check` for a result received during that handoff.

The computed `availableRelease` retains the previous offer during a refresh. Installation is disabled until that check finishes, and expiration and operating-system eligibility are checked again before handoff. `UpdateButton` supplies this behavior; your app supplies the label and decides where it appears.

Concurrent checks share a request. Canceling one caller cancels that shared request for all callers. Cancellation and deactivation restore the prior check result, then revalidate any retained offer. An offer that expired meanwhile becomes `.failed(.releaseExpired)` instead of remaining available.

Canceling a manual installation request also cancels its handler task. Deactivating the controller cancels controller-owned work and prevents stale results from changing the offer. Cancellation observed before the handler is called prevents that call. Once the handler begins, cancellation is cooperative: the handler must observe it before performing further work, and cancellation cannot undo an installation request already handed to iOS. Expiry and cancellation checks are observations immediately before invocation, not an atomic transaction with the operating system.

The controller accepts custom feed loaders, installation handlers, prompt stores, an operating-system version, and a clock for testing. A custom loader owns transport URL validation, redirect policy, timeouts, cancellation, and bounding allocation while receiving bytes; the controller's final size check cannot undo an allocation already made by that loader. A custom prompt store must retain attempt markers for each supplied scope and release. An installation handler must return whether it accepted the URL handoff. These injected services remain host responsibilities.

Use the [sample app](../Examples/InternalAppUpdatesDemo/) to try manual and automatic presentation, release notes, and a persistent note for verifying data preservation.

For construction routes, operation ownership, and the verification matrix, see [Swift state invariants](state-invariants.md).

## Migrating from 0.1.0

Replace Combine subscriptions to `$status` and `$availableRelease` with a subscription to `$state`. The `status` and `availableRelease` properties remain available for ordinary reads. A single snapshot keeps the release and its check state consistent for subscribers.

The default `URLSessionUpdateFeedLoader()` initializer remains nonthrowing. Supplying an explicit timeout now requires `try`, because nonfinite intervals and values outside the supported range are rejected. Polling intervals have the same bounds. Both accept values from one nanosecond through `TimeInterval(Int64.max / 1_000_000_000)` seconds (about 292 years).

Feed readers reject duplicate object members. Swift also rejects a present `releaseNotes: null`, matching the Python publisher; omit the field when there are no notes. Existing Boolean automatic-attempt keys keep their format. A present non-Boolean marker now suppresses automatic presentation instead of relying on UserDefaults coercion; users can still retry manually.
