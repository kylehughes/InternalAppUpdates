# Validation record

Recorded on 2026-10-14 with Xcode 26.6, Swift 6.3.3, and Python 3.11. The iOS deployment target is 16.0; simulator smoke checks used the installed iOS 26.5 runtime.

## Automated checks

| Check | Result |
| --- | --- |
| Swift package tests | 35 tests passed across 4 suites |
| Python publisher, exporter, CLI, and HTTP server tests | 25 tests passed |
| Sample Debug build for iOS Simulator | Passed, unsigned |
| Sample Release build for generic iOS device | Passed, unsigned |
| Sample launch and layout inspection | Passed on iPhone 17 Pro and iPad Pro 13-inch simulators |
| Agent skill validator | Passed |
| Python wheel build and contents | Passed; CLI entry point present, test packages excluded |

The package tests cover feed validation, version comparison, size limits, insecure URLs, coalescing, caller cancellation, background transitions, prompt persistence, expired queued handoffs, and installation completions racing newer feed state. HTTP tests exercise real loopback requests; Tailscale command tests use a mocked CLI and do not change any tailnet settings.

Run the integrated checks from the repository root:

```sh
INTERNAL_APP_UPDATES_PYTHON=/opt/homebrew/bin/python3.11 Scripts/check.sh
```

Use another Python 3.11+ path as appropriate. The device compilation command is:

```sh
xcodebuild -quiet \
  -project Examples/InternalAppUpdatesDemo/InternalAppUpdatesDemo.xcodeproj \
  -scheme InternalAppUpdatesDemo \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -derivedDataPath .build/device \
  CODE_SIGNING_ALLOWED=NO build
```

## Real signed artifact

An existing registered-device development archive was exported through the new CLI. Its exported IPA passed code-signature verification, provisioning/profile checks, and signing-certificate matching. Publication and a repeated idempotent publication both succeeded.

A real published IPA was then served through the local HTTP server. Checks confirmed the bootstrap page, manifest package URL, feed revalidation, immutable caching, complete downloaded SHA-256, and an HTTP 206 byte-range response. The test server was shut down afterward.

Local artifacts are retained outside the repository under the project's `Artifacts` directory: `ArchiveExport`, `PublisherValidation`, `FinalValidationSite`, and iPhone/iPad simulator screenshots. The fixture application is separate from the InternalAppUpdates sample; this proves the publisher accepts real signed output, not that the sample has been installed on a phone.

## External deployment workflow

Checked on 2026-10-16 with Python 3.11:

- All 71 Python tests passed, including ordered publication reports, exact file digests, interrupted-publication recovery, HTTPS verification failures, and S3 script sequencing.
- Repository checks passed: 35 Swift tests and the unsigned iOS Simulator sample build. No Swift client or feed protocol changes were required.
- The S3 example was exercised with fake AWS and CLI commands, including failed uploads, failed verification, corrupted local files, first-time report directories, and preserving a previous report after publication failure. No AWS resources were created or modified.
- The CLI validated a real signed Drawer Sheet IPA and generated its deployment report against a local copy of the existing site. The new verifier then checked the live HTTPS endpoint: five files and 1,558,377 bytes matched, including the full build 3 IPA, manifest, metadata, installation page, and feed. The existing hosted publication was not changed.
- The updated publishing skill passed validation. Documentation links and shell syntax checks passed.

Local reports and verification output are in `.build/deployment-validation/`. These results establish the artifact and script contracts. They do not establish an actual S3/CloudFront deployment or a new physical-device installation.

## Review fixes

Checked on 2026-10-17 with Xcode 26.6, Swift 6.3.3, and Python 3.11:

- All 39 Swift tests passed across 4 suites. Valid feeds for an installed or older release now report up to date even when expired or OS-incompatible; malformed feeds and invalid newer offers still fail.
- All 90 Python tests passed. Added coverage includes corrupt ZIP members and malformed bundle metadata, display-name fallback, unchanged legacy manifests, publisher error output, and withdrawal reports, upload failures, retries, and verification.
- The unsigned iOS Simulator sample build passed. Shell syntax, documentation file links, and the publishing skill validator passed.
- A real signed Drawer Sheet build 3 IPA passed signature and provisioning validation. Repeating its publication in a temporary copy of the existing site preserved every file byte, including the old manifest title. Its version 1 deployment report still validated all five files.

Withdrawal upload tests used fake AWS commands and HTTP responses; no hosted files or cloud resources changed. Logs and the legacy-publication result are in `.build/bugfix-validation/`.

## Swift hardening audit

Checked on 2026-10-17 with Xcode 26.6, Swift 6.3.3, and Python 3.11:

- The full package and test graph compiled with `swift build --build-tests`. The unsigned iOS Simulator sample also compiled against the updated API.
- `Scripts/check.sh` passed all 56 Swift tests across 5 suites, all 90 Python tests, and the unsigned sample build.
- New coverage checks release construction, canonical version comparison, interval bounds, coherent published state, expired offers, manual cancellation, and overlapping feed checks and installer handoffs. Concurrency tests await lifecycle events and clean up suspended work before returning.
- Loader tests use unfinished loopback HTTP responses to verify connection cleanup after size and status rejection. The connection-close deadline precedes the request timeout. A diagnostic run without the explicit cancellation call also passed on this toolchain, so these tests establish cleanup behavior, not a previously demonstrated network leak.
- The final audit found no unresolved correctness, type-invariant, architecture, concurrency-test, or documentation issues. Whitespace, local documentation links, and the publishing skill validator passed.

The [integration guide](integration.md#migrating-from-010) records the Combine publisher migration to `$state` and the throwing initializer for explicit loader timeouts. Ordinary `status` and `availableRelease` reads remain available. Logs are in `.build/hardening-validation/`; the independent sample compile log is `.build/hardening-sample-compile.log`.

## Swift invariant resolution

Checked on 2026-10-18 with Xcode 26.6 (17F113), Swift 6.3.3, and Python 3.11:

- `Scripts/check.sh` passed all 98 Swift tests across 10 suites, all 132 Python tests, and the unsigned iOS Simulator sample build. Its invariant stage passed two positive compiler controls, 26 expected compiler rejections, one runtime identity fixture, and 24 Python invariant tests.
- The same 98 Swift tests passed on an iPhone 17 Pro simulator running iOS 26.5. This exercises the package on iOS; it does not establish behavior on every supported OS release.
- A clean archive of baseline commit `82ea43b` passed its complete check: 79 Swift tests, 129 Python tests, the previous 28 compiler fixtures, and the unsigned sample build. Comparing public Swift API declaration trees before and after these changes found no differences after excluding the API dumper's invocation metadata.
- New regressions cover duplicate JSON members, escaped member names, null release notes, malformed prompt markers, oversized custom-loader responses, expired offers restored after cancellation, and stale operation tokens with equal diagnostic labels. The [architecture decision](0003-operation-identity-and-input-boundaries.md) records the intentional input-policy changes and remaining host responsibilities.

Logs are retained locally in `.build/invariant-resolution/`. The connected physical iPhone was unavailable, so this work adds no physical installation evidence. Controlled transition tests and compiler fixtures do not establish every asynchronous schedule; cancellation remains cooperative at injected host boundaries.

## Physical acceptance still required

The sample's complete in-app, cellular-only replacement has not been tested on a physical iPhone. No Tailscale route was created or modified during this implementation. The [device verification procedure](device-verification.md) covers the remaining proof: initial install, discover a higher build over cellular, accept iOS's prompt, reopen, and verify both the new build number and retained data.

A URL-handler success result, a simulator launch, and a successful IPA download are separate observations. None alone proves that iOS installed the update.
