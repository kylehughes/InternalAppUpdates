# ``InternalAppUpdates``

Check a private update feed and ask iOS to install a signed internal build.

Internal App Updates validates a feed before it offers a release and passes an accepted manifest URL to the system installer. The installer independently downloads, validates, and replaces the app; a successful handoff does not confirm installation.

## Overview

Create one ``UpdateController`` for an app and feed lifecycle. Its ``UpdateController/state`` is the authoritative snapshot for checking and handoff activity. Use ``UpdateController/checkForUpdates()`` for an explicit refresh and ``UpdateController/requestInstallation()`` only after a compatible release is available.

The controller defaults to the credential-free HTTPS policy required for iOS installer URLs. A custom ``UpdateFeedLoading`` can authenticate the feed request, but its credentials are never transferred to the installer.

## Topics

### Integration

- <doc:Update-controller-lifecycle>
- <doc:SwiftUI-integration>
- <doc:UIKit-integration>

### Controller and state

- ``UpdateController``
- ``UpdateConfiguration``
- ``UpdateState``
- ``UpdateCheckState``
- ``UpdateRelease``
- ``UpdateStatus``

### Feed loading

- ``UpdateFeedLoading``
- ``URLSessionUpdateFeedLoader``
- ``UpdateFeedLoaderError``

### Presentation and persistence

- ``UpdateButton``
- ``UpdatePresentationPolicy``
- ``UpdatePromptStoring``
- ``UserDefaultsUpdatePromptStore``
