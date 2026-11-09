# Internal App Updates

[![Provenance: Software Factory](https://img.shields.io/badge/Provenance-Software_Factory-007AFF)](#provenance)
[![Platform Versions](https://img.shields.io/endpoint?url=https%3A%2F%2Fswiftpackageindex.com%2Fapi%2Fpackages%2Fkylehughes%2FInternalAppUpdates%2Fbadge%3Ftype%3Dplatforms)](https://swiftpackageindex.com/kylehughes/InternalAppUpdates)
[![Swift Versions](https://img.shields.io/endpoint?url=https%3A%2F%2Fswiftpackageindex.com%2Fapi%2Fpackages%2Fkylehughes%2FInternalAppUpdates%2Fbadge%3Ftype%3Dswift-versions)](https://swiftpackageindex.com/kylehughes/InternalAppUpdates)
[![CI](https://github.com/kylehughes/InternalAppUpdates/actions/workflows/ci.yml/badge.svg)](https://github.com/kylehughes/InternalAppUpdates/actions/workflows/ci.yml)

*Publish signed internal iOS builds, share installation links, and offer updates inside your app.*

## About

Internal App Updates combines a Swift package for discovering updates with a command-line publisher for preparing signed builds. Each publication includes an installation page and an update feed. Share the page with someone, or integrate the package so the app can offer an install button or request the system installation prompt automatically.

The publisher generates static files. Host them with Tailscale Serve, S3 behind an HTTPS endpoint, or your existing file server. The Swift package has no Tailscale dependency, and the publisher does not require a build-management service.

### Capabilities

- [x] Share a clickable installation page without integrating the Swift package.
- [x] Check for updates while the app is active.
- [x] Offer manual installation through SwiftUI, UIKit, or a custom interface.
- [x] Request the native installation prompt automatically once per release.
- [x] Validate application identity, versions, operating-system compatibility, and release expiration.
- [x] Export existing Xcode archives and verify signed development and registered-device builds.
- [x] Generate deployment reports for your own upload scripts and verify the hosted files.
- [x] Serve builds privately through Tailscale, including over cellular.

## Supported Platforms

- iOS 16.0+ and iPadOS 16.0+ for in-app updates.
- macOS 13.0+ for package tests. The default installation handler returns `false` outside UIKit platforms.

The publisher's signing validation and archive export require macOS. The Python CLI has no third-party runtime dependencies.

## Requirements

- Swift 6.3+ and an Xcode version with that toolchain for the Swift package.
- Python 3.11+ for the CLI.
- A signed development or registered-device IPA, with provisioning profiles that cover the intended devices.
- Developer Mode on devices installing development-signed builds.
- A trusted HTTPS endpoint that both the app and the iOS installer can reach.

## Documentation

[Read the DocC documentation on GitHub Pages.](https://kylehughes.github.io/InternalAppUpdates/)

- [Swift package integration](Documents/integration.md): ownership, scene activity, SwiftUI, UIKit, and customization.
- [Hosting](Documents/hosting.md): Tailscale Serve, HTTPS endpoints, caching, and installer access.
- [Deployment](Documents/deployment.md): publication reports, upload order, verification, and retries.
- [Feed protocol](Documents/feed-protocol.md): the JSON and installation-manifest contracts.
- [Device verification](Documents/device-verification.md): verify installation and data preservation on a physical device.
- [CI and package releases](Documents/delivery.md): supported test configurations, documentation deployment, and release procedures.

## Installation

### Swift Package Manager

```swift
dependencies: [
    .package(url: "https://github.com/kylehughes/InternalAppUpdates.git", from: "1.0.0"),
]
```

Add `.product(name: "InternalAppUpdates", package: "InternalAppUpdates")` to your target's dependencies. In Xcode, choose **File > Add Package Dependencies**, enter the repository URL, and select the `InternalAppUpdates` product.

This repository is private. Xcode or your Git credentials need access to it.

### Command-Line Tools

From a checkout of this repository:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Use a Python 3.11+ interpreter. You can also run `bin/internal-app-updates` directly from the checkout. These instructions describe the current checkout. See the [migration notes](Documents/integration.md#migrating-from-010) when updating from the Swift package's `0.1.0` release.

## Quick Start

### Publish an Installation Link

Export a signed IPA using your app's existing signing setup, then prepare the installation page and feed:

```sh
internal-app-updates publish \
  --ipa /path/to/YourApp.ipa \
  --output Site/your-app \
  --base-url https://updates.example/your-app/ \
  --notes "Improved scrolling and search."
```

The command prints both URLs:

```console
published 1.0 (2)
install: https://updates.example/your-app/index.html
feed: https://updates.example/your-app/feed.json
```

Serve that directory at the configured HTTPS URL. The recipient opens the installation page in Safari and taps **Install Build**. Installing from the page does not require the Swift package in the app.

`publish` prepares local files. If your host is elsewhere, your script uploads them before the links become usable. See [Deployment](#deployment) for the upload contract and verification command.

### Add In-App Updates

Create a controller using the published feed URL and the installed app's bundle metadata:

```swift
import Foundation
import InternalAppUpdates

@MainActor
func makeUpdateController(feedURL: URL) throws -> UpdateController {
    let configuration = try UpdateConfiguration(
        feedURL: feedURL,
        installedBuild: InstalledBuild(),
        presentation: .automatic
    )
    return UpdateController(configuration: configuration)
}
```

Retain the controller in your app or scene coordinator. Call `setActive(true)` when the app becomes active and `setActive(false)` when it becomes inactive. It checks immediately on activation, then every 60 seconds while active by default. For a multiwindow app, keep it active while any participating scene is active.

With `.automatic`, a newer compatible release requests the native installation prompt once per release. Use `.manual` when your interface should initiate installation. Install the first integrated build through Xcode or the installation page; publishing an IPA cannot add update discovery to an older build already on the device.

## Usage

### Manual Installation

Use `checkForUpdates()` for a check button. To show an install button only when an update is available, use `availableRelease` for visibility and `canRequestInstallation` for whether it is enabled. The app owns and retains `updates`, with scene activity connected as described above.

In a SwiftUI view with `@ObservedObject var updates: UpdateController`, conditionally include your own button:

```swift
if updates.availableRelease != nil {
    Button {
        Task { await updates.requestInstallation() }
    } label: {
        Label("Install Update", systemImage: "arrow.down.circle")
    }
    .disabled(!updates.canRequestInstallation)
}
```

You can also use `UpdateButton(controller: updates) { ... }` inside the same condition. It supplies the installation action and disabled state; your app supplies the label and styling. During a refresh, the previous offer remains visible while installation is disabled.

In UIKit, observe `states()` to update your existing `UIButton`. Store the observation task in a `Task<Void, Never>?` property on the view controller and start it when the view appears:

```swift
stateTask?.cancel()
installButton.isHidden = true
stateTask = Task { @MainActor [weak self, updates] in
    for await state in updates.states() {
        guard let self else { return }
        self.installButton.isHidden = state.availableRelease == nil
        self.installButton.isEnabled = updates.canRequestInstallation
    }
}
```

Cancel `stateTask` when the view disappears and in `deinit`. Each `states()` call creates an independent stream with the newest pending snapshot, so a slow consumer can skip intermediate transitions. Check failures arrive as state values, and observation does not start polling. `$state` remains available for Combine consumers. The [integration guide](Documents/integration.md#uikit-and-custom-presentation) includes the complete observation lifecycle.

Connect the UIKit button's action to the same installation request:

```swift
Task { @MainActor in
    let handedToSystem = await updates.requestInstallation()
}
```

A `true` result means the system accepted the URL handoff. It does not establish that the user confirmed installation or that the app was replaced. Save unsaved state before requesting installation.

### Automatic Presentation

Set `presentation: .automatic` during configuration, or change `presentationPolicy` at runtime. Automatic attempts are remembered in UserDefaults per feed and bundle. Dismissing the system prompt does not cause repeated automatic prompts for that release; manual installation remains available.

### Update Channels

Each feed URL identifies a channel. Give different apps, teams, or branches separate directories and feed URLs. The controller rejects malformed feeds and mismatched bundle identifiers. If the release is already installed or older, the app is up to date. A newer release must be unexpired and support the current operating system.

Increment `CFBundleVersion` for each build. The publisher rejects older releases and different IPA bytes that reuse a version/build pair within its local publication history. Keep that history between publications.

### Archive Export

Export an existing archive with your Xcode export-options plist:

```sh
internal-app-updates export \
  --archive /path/to/YourApp.xcarchive \
  --export-options /path/to/ExportOptions.plist \
  --output /path/to/export
```

This runs `xcodebuild -exportArchive` using your signing configuration. Provisioning updates require the explicit `--allow-provisioning-updates` option. Use `inspect --ipa /path/to/YourApp.ipa --json` to validate a signed artifact without publishing it.

### Tailscale Serve

On a Mac connected to your tailnet:

```sh
internal-app-updates serve \
  --directory Site/your-app \
  --port 8765 \
  --tailscale \
  --mount-path /your-app/
```

Use the private HTTPS base URL it prints for publication. Keep the server running and Tailscale connected on the phone during discovery and installation. The command configures the specified Serve route and leaves it in place when the server stops. It refuses conflicting routes and does not enable Funnel or reset your Serve configuration. See [Hosting](Documents/hosting.md) for service management and scoped removal.

### Deployment

Use your existing upload tools from a project-owned script or CI job. Save a publication report outside the served directory:

```sh
mkdir -p Artifacts
internal-app-updates publish \
  --ipa /path/to/YourApp.ipa \
  --output Artifacts/updates-site \
  --base-url https://updates.example/your-app/ \
  --json > Artifacts/publication.json
```

Continue only after publication exits successfully. Upload `deployment.files` in order with the supplied content types and cache policies, then verify the result:

```sh
internal-app-updates verify --publication Artifacts/publication.json
```

Verification downloads the hosted files and checks their bytes, content types, and cache headers. It prints the installation link after all checks pass. The [S3 example](Examples/Deployment/README.md) uses the ordinary AWS CLI; the [deployment guide](Documents/deployment.md) describes the contract for other uploaders.

Serialize the complete workflow per app/channel. Preserve the prepared files and report for retries, and keep older release assets available for downloads already in progress.

### Retrieve Links or Withdraw an Offer

Get the links from an existing local publication without rebuilding or publishing again:

```sh
internal-app-updates links --output Site/your-app
```

Add `--json` for `installPageURL` and `feedURL` fields. To remove the current offer:

```sh
internal-app-updates withdraw --output Site/your-app
```

Withdrawal updates the local page and feed while preserving immutable release assets. A local-only withdrawal does not need a base URL. To deploy and verify the withdrawal on a remote host, write a report outside the served directory and use the same uploader workflow:

```sh
internal-app-updates withdraw \
  --output Site/your-app \
  --base-url https://updates.example/your-app/ \
  --json > Artifacts/withdrawal.json
```

The withdrawal report lists `index.html` then `feed.json`, both with `Cache-Control: no-cache`; upload them in that order. The S3 example accepts the same report. After upload, verify the hosted files:

```sh
internal-app-updates verify --publication Artifacts/withdrawal.json
```

A device already downloading the old build can still finish.

## Important Behavior

- iOS asks the user to confirm installation. This package does not silently install apps or provide remote debugging.
- Keep the updater out of App Store configurations. A Release archive can use development signing; `DEBUG` does not establish signing eligibility.
- Publication verifies signatures and registered-device provisioning. It rejects simulator, App Store, and enterprise artifacts. iOS remains the final authority on installation.
- An authenticated feed request inside the app does not authenticate the system installer. The manifest and IPA must be reachable independently.
- Website installation with development-signed builds has worked in the prototype. The complete in-app, cellular-only replacement and data-preservation procedure remains a [physical-device acceptance check](Documents/device-verification.md); a successful URL handoff or download alone does not prove it.

## Development

Open the [sample app](Examples/InternalAppUpdatesDemo/InternalAppUpdatesDemo.xcodeproj) to try manual checks, automatic presentation, and a saved note for testing data preservation. Select your signing team and app identifier before installing it on a device.

Run the support checks, workflow lint, test suites, and unsigned sample build:

```sh
Scripts/check.sh
```

Set `INTERNAL_APP_UPDATES_PYTHON` if your default Python is older than 3.11. The [validation record](Documents/validation.md) documents completed checks and remaining device acceptance work.

CI also checks both declared Apple toolchains in Debug and Release, runs iOS simulator tests, tests Python on macOS and Linux, builds installable distributions and DocC, and runs selected sanitizer checks. The manual release workflow defaults to a dry run. Publishing a release attaches the packages and documentation archives to the private GitHub release, then deploys its DocC site to GitHub Pages. See [CI and package releases](Documents/delivery.md) for the matrix, local commands, and release recovery.

For agent-assisted integration and publication, use [publishing-internal-app-updates](Skills/publishing-internal-app-updates/SKILL.md). Design decisions are recorded in [Static Feed and Native Installer](Documents/0001-static-feed-and-native-installer.md), [External Deployment Scripts](Documents/0002-external-deployment-scripts.md), [Operation Identity and Input Boundaries](Documents/0003-operation-identity-and-input-boundaries.md), [CI and Private Package Releases](Documents/0004-ci-and-private-releases.md), and [Release Documentation on GitHub Pages](Documents/0005-release-documentation-on-github-pages.md).

## Contributions

[Report bugs on GitHub.](https://github.com/kylehughes/InternalAppUpdates/issues) Include the affected build, signing/export method, hosting setup, and the step that failed.

## Provenance

Internal App Updates was conceived, implemented, tested, and documented by an automated software factory. No human has
reviewed its source code.

## "Author"

[Kyle Hughes](https://kylehugh.es)

[![Bluesky][bluesky_image]][bluesky_url]<br>
[![LinkedIn][linkedin_image]][linkedin_url]<br>
[![Mastodon][mastodon_image]][mastodon_url]

[bluesky_image]: https://img.shields.io/badge/Bluesky-0285FF?logo=bluesky&logoColor=fff
[bluesky_url]: https://bsky.app/profile/kylehugh.es
[linkedin_image]: https://img.shields.io/badge/LinkedIn-0A66C2?logo=linkedin&logoColor=fff
[linkedin_url]: https://www.linkedin.com/in/kyle-hughes
[mastodon_image]: https://img.shields.io/mastodon/follow/109356914477272810?domain=https%3A%2F%2Fmister.computer&style=social
[mastodon_url]: https://mister.computer/@kyle
