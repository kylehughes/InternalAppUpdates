# CI and package releases

This guide covers development of Internal App Updates itself. To publish an internal iOS app through the CLI, use the [deployment guide](deployment.md).

## Supported configurations

[`Config/support.json`](../Config/support.json) records the checked configurations. The support validator compares it with `Package.swift`, `pyproject.toml`, and the workflow matrices.

| Component | Declared minimum | CI configurations |
| --- | --- | --- |
| Swift | Tools 6.3, language mode 6 | Xcode 26.5 / Swift 6.3.2 and Xcode 26.6 / Swift 6.3.3, Debug and Release |
| Apple platforms | iOS 16, macOS 13 | macOS runners and an available iPhone simulator; unsigned simulator and device sample builds |
| Python | 3.11 | 3.11 and 3.15 on macOS and Ubuntu |

The Apple lanes verify the exact Xcode build and Swift version before compiling. They do not establish runtime coverage on iOS 16 or macOS 13, or compiler coverage on Swift 6.3.0. Signing validation and archive export require macOS; Linux exercises the portable publisher and its mocked platform boundaries.

## Checks and cadence

Pull requests and pushes to `main` run the [CI workflow](../.github/workflows/ci.yml). Its `CI gate` succeeds only when every prerequisite job succeeds, including matrix jobs. Failed, cancelled, or skipped prerequisites fail the gate.

| Check | Purpose | Maximum job duration |
| --- | --- | --- |
| Support declaration and workflow lint | Detect unsupported claims and invalid workflow syntax | 8 minutes |
| Apple Debug and Release matrix | Swift tests and compiler invariant fixtures on both toolchains | 35 minutes per lane |
| Current Apple Debug iOS checks | Run package tests on a simulator and compile both sample destinations | Within the Apple lane, 18-minute step |
| Python matrix | Publisher and delivery-script tests on both versions and operating systems | 15 minutes per lane |
| Python distributions | Build wheel and sdist, install each outside the checkout, and run the CLI | 15 minutes |
| Documentation archive | Compile DocC and produce a static site bundle | 20 minutes |
| Coverage | Require a generated report containing the Swift client's runtime sources | 20 minutes |
| Thread Sanitizer | Exercise controller, operation, state-stream, and feed-loader tests | 25 minutes |
| CI gate | Require successful results from all of the above | 2 minutes |

Coverage is a report and instrumentation check, not a percentage threshold. Uploads retain the coverage report, Python distributions, and documentation for inspection in the workflow run.

[Post-merge](../.github/workflows/post-merge.yml) runs the selected tests under Address Sanitizer after each push to `main`, with a 25-minute job limit. [Nightly](../.github/workflows/nightly.yml) runs three independent Thread Sanitizer processes at 09:17 UTC, each limited to 30 minutes. Both can be dispatched manually. These checks increase schedule coverage; they do not prove the absence of every race or memory error.

The reusable [Support audit](../.github/workflows/support-audit.yml) runs the CI checks for an exact commit SHA before a release. It can also be dispatched manually with a full SHA. Release and audit runs retain their results when newer commits arrive. Ordinary CI cancels superseded runs on the same branch or pull request.

Require `CI gate` for `main` in GitHub branch protection, with the branch up to date before merging. Keep the gate name unique to the primary CI workflow. Workflow files alone cannot configure that repository setting.

## Local validation

Run the complete baseline check with a supported Python interpreter:

```sh
INTERNAL_APP_UPDATES_PYTHON=python3.11 Scripts/check.sh
```

This checks the support declaration and workflow syntax, runs delivery-script tests, Swift tests, compiler invariant fixtures, and publisher tests, then regenerates and builds the simulator sample. Workflow lint uses a pinned `actionlint` release; it downloads and verifies the binary if that version is not installed.

Run the additional CI checks when changing the corresponding behavior:

```sh
swift test -c release
Scripts/test-ios.sh
python3 Scripts/check-coverage.py
Scripts/test-sanitizers.sh thread
Scripts/test-sanitizers.sh address
Scripts/build-python-distributions.sh Artifacts/python-distributions
Scripts/build-documentation.sh Artifacts/documentation
```

Artifact output directories must be absent or empty. The Python build uses temporary environments and pinned build dependencies from [`build-requirements.txt`](../build-requirements.txt). Each distribution is installed and checked outside the checkout. These build tools are not dependencies of the installed CLI.

## Documentation artifacts

The DocC catalog covers the public API and integration lifecycle. The [Build documentation workflow](../.github/workflows/documentation.yml) builds it on demand. Download its artifact from the run to obtain an Xcode-readable `.doccarchive.zip` and a static documentation bundle.

Documentation stays in this private repository's workflow artifacts and releases. There is no Pages deployment or public documentation site.

## Release a package version

The [Release workflow](../.github/workflows/release.yml) creates one Git tag and GitHub release for the Swift package, Python distributions, and documentation. It does not upload to PyPI or publish an iOS application.

1. Set `project.version` in `pyproject.toml` to the next stable version, update the relevant documentation, and merge through CI. Use a bare version such as `0.2.0`, without a `v` prefix or prerelease suffix. Versions must increase beyond existing stable tags and releases.
2. Open **Actions > Release > Run workflow**, select `main`, enter that version, and leave **dry_run** enabled. Optional notes become the release body; omitting them requests generated notes when publishing.
3. Review the exact commit SHA, support audit, and retained release-candidate artifact. The candidate manifest records the repository, version, SHA, filenames, sizes, and SHA-256 digests.
4. Dispatch the workflow with **dry_run** disabled to publish. It audits and builds the selected commit again. If `main` changes, review and dry-run the new candidate before publishing it.
5. Confirm the release tag resolves to the intended commit, all four assets are present, and the publication receipt reports `published`.

Only the final publishing job has write permission. It downloads the certified artifacts, checks them again, creates a draft release, uploads and verifies each asset's bytes, and publishes the completed release. Package builds and installations run in read-only jobs.

Dry runs use read-only credentials. GitHub limits [draft-release visibility](https://docs.github.com/en/rest/releases/releases#list-releases) to callers with push access, so the publishing job repeats the remote checks with its write token before making changes.

Each release contains a wheel, a source distribution, a DocC archive, and a static documentation bundle. Candidate manifests and publication receipts remain workflow artifacts for 90 days. Download them if longer retention is needed.

The existing `0.1.0` tag predates this workflow. It remains at its original commit; the next release requires a version bump. Dispatching `0.1.0` for a different commit fails validation.

## Recover a failed publication

Inspect the failed job, the retained candidate, and the publication receipt before retrying. The receipt records the intended mutation before each write. A network error can occur after GitHub accepted a request; the publisher reads back the release and assets to determine whether the intended result exists.

Use **Re-run failed jobs** to resume with the successful build job's retained candidate. Matching draft assets are accepted by digest, missing assets are uploaded, and a complete matching release is accepted as already published. Existing tags are never moved, and assets are never overwritten or deleted.

A new dispatch can also reuse the candidate: enter the original workflow run's numeric ID in **candidate_run_id**. This downloads and verifies the retained candidate instead of rebuilding it. The original run must be a completed Release dispatch for the same repository, commit, and version. If `main` has advanced, rerun the original run's failed jobs to retain its commit selection.

A full workflow rerun without a retained candidate rebuilds the archives. ZIP and distribution bytes can differ between builds even at the same commit. If existing assets differ, publication stops: recover the original retained candidate instead of replacing published bytes. Conflicting tags, notes, unexpected assets, or a newer stable version also stop publication and need investigation.

If the candidate artifact has expired, do not assume a fresh build is byte-identical. Preserve the existing release state and prepare a new version if the original bytes cannot be recovered.

CI and package releases do not establish over-the-air installation acceptance. Follow the [physical-device procedure](device-verification.md) for signing, cellular access, replacement, and data preservation.
