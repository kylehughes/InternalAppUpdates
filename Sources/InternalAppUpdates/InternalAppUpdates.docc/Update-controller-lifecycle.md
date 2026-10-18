# Update controller lifecycle

Retain one ``UpdateController`` while the app participates in an update channel.

Construct the controller from the installed app metadata and a feed URL. The configuration validates the URL, installed build, and polling interval before work starts.

```swift
import Foundation
import InternalAppUpdates

@MainActor
func makeUpdates(feedURL: URL) throws -> UpdateController {
    let configuration = try UpdateConfiguration(
        feedURL: feedURL,
        installedBuild: InstalledBuild(),
        presentation: .manual,
        checkInterval: 60
    )
    return UpdateController(configuration: configuration)
}
```

Call ``UpdateController/setActive(_:)`` when the owner becomes active and inactive. Activation checks immediately and begins controller-owned polling. Deactivation cancels controller-owned work and prevents stale results from changing the published state.

Use ``UpdateController/checkForUpdates()`` when a person asks to refresh. Concurrent callers join one request; cancellation by any caller cancels that shared request. Read ``UpdateController/availableRelease`` and ``UpdateController/canRequestInstallation`` from the controller when presenting a manual install action.

Calling ``UpdateController/requestInstallation()`` asks the system to handle the release manifest URL. Its result only says whether the URL handler accepted that request. Save app state before requesting installation; iOS controls confirmation, download, signature validation, and app replacement.

Use ``UpdatePresentationPolicy/automatic`` only when a system prompt is appropriate. Automatic attempts are remembered for the feed, app, and release scope, while manual installation remains retryable.
