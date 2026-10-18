# Verify an update on a physical iPhone

This procedure verifies the part that unit tests and the simulator cannot: iOS accepts a signed update, the app is replaced, and its data survives. No device automation is needed.

## Prepare

Use a registered iPhone, a signing team and profiles covering that phone, and Developer Mode for development-signed builds. Use the same bundle identifier and compatible signing identity for both builds. Configure a trusted HTTPS host that the system installer can reach. For private Serve, connect Tailscale on the phone.

Open the sample Xcode project and select the signing team and app identifier. Set `INTERNAL_APP_UPDATES_FEED_URL` to the published feed URL if you want a preconfigured app; otherwise enter it in the sample after launch.

Archive build 1 using Xcode, then export it for development or registered-device testing. An equivalent archive command, with values replaced for your environment, is:

```sh
xcodebuild archive \
  -project Examples/InternalAppUpdatesDemo/InternalAppUpdatesDemo.xcodeproj \
  -scheme InternalAppUpdatesDemo \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath /path/to/Build1.xcarchive \
  DEVELOPMENT_TEAM=YOURTEAMID \
  PRODUCT_BUNDLE_IDENTIFIER=com.yourteam.InternalAppUpdatesDemo \
  CURRENT_PROJECT_VERSION=1 \
  INTERNAL_APP_UPDATES_FEED_URL=https://your-host.example/your-app/feed.json
```

Export with your Xcode-generated export-options plist using `internal-app-updates export`. The exporter does not contact Apple to update provisioning unless explicitly asked. If the initial build cannot install through ordinary Xcode signing, resolve that before diagnosing the updater.

## Bootstrap

1. Publish build 1 with the intended base URL. Open `index.html` on the phone and accept installation, or install that exact build through Xcode.
2. Launch the sample. Confirm it displays build 1 and connects to the configured feed.
3. Enter a recognizable saved note. Leave automatic presentation off.
4. Check for updates. The feed offers the installed build, so the app should say it is up to date.

## Manual update over cellular

1. Make a visible change or update the build setting to 2. Archive, export, and publish build 2 without changing the bundle identifier or signing team.
2. Turn Wi-Fi off in Settings and disconnect USB. Leave cellular and Tailscale active if the host requires the tailnet.
3. Return to the sample and tap **Check for Updates**. Confirm build 2 and its release notes appear.
4. Tap **Install Update**. Confirm iOS presents installation UI. Cancel it, then tap again to confirm manual retry works.
5. Accept installation. Wait for iOS to finish, then reopen the sample from the Home Screen.
6. Confirm build 2 is displayed and the saved note is unchanged. Record the OS version, signing/export method, host type, network state, and observed build number.

Only step 6 establishes that the replacement succeeded. The SDK's `requestInstallation()` result cannot establish it.

## Automatic presentation

1. Enable automatic presentation in build 2, then publish build 3.
2. Keep the app active or return to it. Confirm one installation prompt appears when the new build is discovered.
3. Cancel the prompt. Background and foreground the app and allow another check. Confirm it does not repeatedly interrupt for build 3.
4. Use **Install Update** to retry, accept, and verify build 3 and the retained note.

## Failure checks

- Stop the backend temporarily. A check should report a loading failure without requesting installation. Restore the backend and retry.
- Withdraw the release. The next successful check should remove the offer.
- Point the sample to a feed for another bundle identifier. It should report a mismatch.
- Test leaving the app during a delayed check. A late response must not open installation UI in the background.

Keep the prior IPA and archive while validating. Do not delete the installed app to recover from an update failure if preservation of its data matters. Publish a corrected higher-numbered build; use Xcode diagnostics if iOS rejects the signing or installation manifest.
