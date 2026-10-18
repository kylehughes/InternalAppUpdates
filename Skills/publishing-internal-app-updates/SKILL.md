---
name: publishing-internal-app-updates
description: Publish signed internal iOS builds with shareable install links and HTTPS update feeds, retrieve existing links, or integrate InternalAppUpdates into an app. Supports Tailscale Serve and existing static hosting; not App Store release or remote debugging.
---

# Publish internal iOS updates

InternalAppUpdates consists of a Swift client and a Python CLI. Locate the installed `internal-app-updates` command or a checkout containing `bin/internal-app-updates`. Run `--help` and the relevant subcommand's help before scripting. If the checkout is available, read its README for the current Swift API and `Documents/feed-protocol.md` when replacing the publisher or host.

## Integrate

The generated installation page works without the Swift package. Integrate the package when the user wants discovery or installation from inside the app; publishing a clickable install link does not require changing the app.

Add the Swift package's `InternalAppUpdates` product to the internal app target. Retain one `UpdateController` for the participating scenes, with an `UpdateConfiguration` containing the feed URL and `InstalledBuild(bundle: .main)`. Drive `setActive` from app/scene activity. For multiple scenes, aggregate their active state so one scene cannot stop another's updater.

Use `.manual` for an app-owned button or release-notes interface; `.automatic` asks iOS to present once per release while active. Save app state before requesting installation. `requestInstallation()` returning true means URL handoff was accepted, not that installation succeeded. The next launch's bundle version is the evidence of replacement.

Keep the updater out of App Store configurations. Do not infer signing eligibility from `DEBUG`: a Release archive can be exported with development signing. The first install must occur through Xcode or the generated bootstrap page before the app can discover its own updates.

## Publish

Confirm the chosen local output directory actually maps to the endpoint's base URL. For an existing installed app, verify it already includes the updater and is configured to check that feed with the intended presentation policy. Publishing an IPA alone cannot retrofit update discovery into the currently installed build. If it lacks the integration, first provide a bootstrap installation. A phone using a private tailnet endpoint must keep Tailscale connected during discovery and installation, including on cellular.

Use the app's existing signing configuration. The device must be in all applicable profiles; development-signed installs require Developer Mode. Build with a new numeric version/build pair. Do not change the bundle identifier to work around an update failure, because that creates a different app and data container.

Export an existing archive using `internal-app-updates export --archive … --export-options … --output …`, or use an already exported IPA. Provisioning updates require the explicit `--allow-provisioning-updates` flag; adding that flag is not a routine retry for an unexplained signing failure.

Run `internal-app-updates publish --ipa … --output … --base-url … --json`, optionally with `--notes-file`. Publication verifies signing, writes immutable release assets, and advances the local feed last. Save its JSON report outside the served directory, and check the exit status before using it: error output is JSON too. The result includes `links.installPageURL` for people, `links.feedURL` for the app, and versioned `deployment` metadata for external upload scripts. There is no production signing bypass. `inspect --no-verify` reads metadata only.

To retrieve the links for an existing publication, use `internal-app-updates links --output … --json`. Its result has `installPageURL` and `feedURL` fields. This reads the existing site without publishing or changing files; no IPA or base URL is needed. A withdrawn publication has no current install offer and returns an error.

To stop new offers locally, run `internal-app-updates withdraw --output …`. To deploy a withdrawal, run `withdraw --output … --base-url … --json` and save the report outside the served directory; `--json` requires `--base-url`. It sets `release` to `null` and produces deployment schema version 2 with `operation: "withdraw"`. The report contains `index.html` and `feed.json` in that order, both with `no-cache` metadata. Upload the page before the feed, verify the same report, and leave immutable release files in place for downloads already in progress. A consumer that cannot support deployment schema version 2 must reject it before remote writes.

For a new private Serve host, use `internal-app-updates serve --directory … --tailscale --mount-path …` within the user's authorized hosting scope. For an existing endpoint, use its existing host configuration. Do not enable Funnel, reset Serve, or overwrite unrelated routes as a shortcut. The local server must remain running while downloads use it.

When uploading to another host, read `Documents/deployment.md` and prefer the project's existing script or CI workflow. For S3, `Examples/Deployment` supplies a copyable AWS CLI recipe. Hosting and credentials remain in the user's tools; do not invent lifecycle hooks or provision cloud infrastructure as part of a routine publication.

Use `deployment.files` in order, with its content types and cache policies. Active reports have deployment schema version 1 and upload complete release assets before `index.html`, then `feed.json`; withdrawal reports use version 2 and upload the page then the feed. Serialize the whole prepare/upload/verify workflow per app/channel, including across runners; the publisher's local lock does not protect remote uploads. Preserve the local publication history between runs. Keep prior assets available for in-progress downloads. Retry failed uploads from the same report and unchanged files, unless a newer deployment has advanced the channel. Do not regenerate an immutable manifest for a different base URL; use a separate destination or follow the publisher's error guidance.

## Verify

Run `internal-app-updates verify --publication <saved-report.json>` through the endpoint the phone uses. It checks the exact hosted files, content types, cache headers, sizes, and digests; local artifacts are not needed for this step. A successful local `publish` is not remote deployment success. If verification fails, identify the failed file or host behavior before retrying, and report that remote objects may already have changed. It does not prove every CDN edge follows its headers or that iOS installed the build. Feed success inside an app does not prove installer access: the installer does not inherit the app's authentication headers or URLSession credentials.

After publication, return the verified `installPageURL` as a clickable Markdown link with the app's version/build. Tell the user to open it in Safari and tap Install Build; mention Tailscale connectivity when the endpoint is private. Include the feed URL when needed for integration. Prefer the HTTPS installation page over a raw `itms-services` URL in chat, where custom schemes may not be clickable.

After a withdrawal, report that the hosted offer was removed only when verification succeeds. The page remains reachable but no longer offers a build; do not tell the user to install from it.

For physical acceptance, install build N, save recognizable app data, publish N+1, and let the user accept iOS's prompt. Reopen and verify the installed build and retained data. If cellular delivery is required, turn Wi-Fi off and disconnect USB before this check. Do not claim this passed based on a simulator, an HTTP check, or an `openURL` result.

After a failure, identify whether export, hosting, feed validation, URL handoff, or iOS installation failed before retrying. Do not delete the app, reset trust, or alter signing identities to manufacture a successful update. Preserve the existing app and report the narrow failing step. To stop new offers, use `withdraw`; to fix a bad build, publish a corrected higher-numbered build.
