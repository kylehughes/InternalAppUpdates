# AGENTS.md

Internal App Updates provides a Swift client and Python publishing tools for installing signed internal iOS app updates through the system installer. Start with [README.md](README.md) for usage and [Documents/integration.md](Documents/integration.md) for the client lifecycle.

## Repository structure

- `Sources/InternalAppUpdates/` contains the Swift client; `Tests/InternalAppUpdatesTests/` contains its Swift Testing tests.
- `tools/internal_app_updates/` contains the Python CLI; `tools/tests/` contains its `unittest` suite. Run the checkout CLI with `bin/internal-app-updates`.
- `Examples/InternalAppUpdatesDemo/` contains the sample app, which depends on the local Swift package. `Examples/Deployment/` contains external deployment examples.
- `Documents/` contains protocol contracts, operational guides, and numbered architecture decision records. `Skills/publishing-internal-app-updates/` contains the reusable publishing skill.

Keep the Swift client, static-file publisher, and optional local server independent. Tailscale Serve is an optional hosting adapter. Preserve support for arbitrary HTTPS hosts. The architectural boundaries are recorded in [ADR 0001](Documents/0001-static-feed-and-native-installer.md) and [ADR 0002](Documents/0002-external-deployment-scripts.md).

## Development and validation

Preserve the declared compatibility: Swift tools 6.3, Swift 6 language mode, iOS 16+, macOS 13+, and Python 3.11+. The Python tools have no third-party runtime dependencies. Do not raise minimum versions or add dependencies incidentally.

Run commands from the repository root. The complete check requires macOS and a selected Xcode with the required Swift toolchain and iOS Simulator SDK:

```sh
Scripts/check.sh
```

Set `INTERNAL_APP_UPDATES_PYTHON` if `python3` is not a supported interpreter:

```sh
INTERNAL_APP_UPDATES_PYTHON=python3.11 Scripts/check.sh
```

The script validates support declarations and workflows, runs delivery-script and product test suites, regenerates the sample project, and builds the unsigned iOS Simulator app. See [delivery.md](Documents/delivery.md) for the CI matrix, additional checks, and private package releases. For focused iteration:

```sh
swift test
PYTHONPATH=tools "${INTERNAL_APP_UPDATES_PYTHON:-python3}" -m unittest discover -s tools/tests -v
```

Run the complete check before finishing code changes. For documentation-only changes, check factual claims, relative links, and whitespace; rebuilding is unnecessary.

### Generated sample files

[`generate_project.py`](Examples/InternalAppUpdatesDemo/generate_project.py) generates the sample's `project.pbxproj`, shared scheme, and `Info.plist`. Change the generator, rerun it, and include the generated outputs. Do not hand-edit those outputs: `Scripts/check.sh` overwrites them. Files under the sample's `App/` directory are hand-maintained.

## Swift client invariants

- Model valid states and values in types. Keep validated values immutable, validate at construction or decoding boundaries, and avoid unchecked constructors added for tests.
- Keep `UpdateController` state coherent through its published `state` snapshot. Checking and installer handoff can overlap; completing one operation must preserve the other's result. Derive presentation and installation eligibility from authoritative state.
- Preserve actor isolation, cancellation propagation, shared-check behavior, and protection against stale asynchronous completions. Test lifecycle transitions with explicit readiness and completion signals, bounded waits, and awaited cleanup rather than sleeps or fixed yield counts.
- Preserve automatic-attempt persistence across launches and its feed, app, and release scope. Automatic presentation is limited per release; manual installation remains retryable.
- Keep UI composition and copy under the host app's control. Keep updater integration out of App Store configurations; `DEBUG` alone does not establish signing eligibility.
- Treat a successful installer URL handoff as a request, not evidence that installation completed. iOS owns confirmation, download, signature verification, and replacement.

## Feed and publication contracts

Use [feed-protocol.md](Documents/feed-protocol.md) as the contract for both implementations. Keep Swift decoding, Python output, and tests consistent. Preserve closed schemas, structural validation before version comparison, canonical version comparison, expiry handling, and withdrawal behavior. Feed and deployment-report schemas are independently versioned.

Publish immutable assets identified by the complete IPA's SHA-256 digest. Never reuse an equivalent version/build pair for different bytes, including after withdrawal. Publish assets before the installation page and advance `feed.json` last. Withdrawal updates the page before the feed and preserves existing assets.

The system installer must reach the manifest and IPA independently; authentication supplied to a custom feed loader is not transferred to iOS. Preserve signing validation and do not change app identity, delete the installed app, or reset trust to manufacture a successful update.

Keep cloud uploads in project-owned scripts. Follow [deployment.md](Documents/deployment.md): upload `deployment.files` in order with their declared content types and cache policies, then verify. Serialize preparation, upload, and verification per app/channel across runners. Preserve prepared artifacts, reports, and publication history for retries; never replay an older report over a newer deployment.

Keep reports outside the served directory because they contain local paths. Keep credentials, signing material, IPAs, generated sites, and local reports out of Git. Scope hosting changes to the requested deployment and preserve unrelated routes and services.

## Documentation and device acceptance

Update the relevant guides and tests when changing public APIs or contracts. Record architectural decisions in `Documents/`. Update the [publishing skill](Skills/publishing-internal-app-updates/SKILL.md) when its workflow changes.

Use [device-verification.md](Documents/device-verification.md) for physical installation acceptance. Package tests, an unsigned simulator build, hosted-file verification, and a successful URL handoff do not prove an over-the-air update. Report exactly what was verified and retain any device-testing limitations.
