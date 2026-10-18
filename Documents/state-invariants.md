# State invariants

The Swift client validates external values at construction boundaries and keeps the resulting values immutable. The controller owns checking, installation, and polling through private variants that carry the facts required by each operation. The inventory below describes the current boundaries; dated acceptance records identify the snapshots and checks they cover. The [feed protocol](feed-protocol.md) and [integration guide](integration.md) remain the public contracts.

## AC1: fixed values and construction routes

| Value | Construction and decoding routes | Preserved constraints and mutation authority | Evidence |
| --- | --- | --- | --- |
| `InstalledBuild` | Public explicit initializer; bundle initializer delegates to it. No decoding conformance or memberwise initializer. | Nonempty bundle identifier; one to three ASCII numeric version/build components within `UInt64`. Public strings and cached components are `let`. Raw spelling remains part of equality. | `ValueInvariantTests.installedBuildValues` and `bundleMetadata`; `ConfigurationValidationTests.malformedInstalledVersion`; compile fixtures reject mutation and unchecked cached-component construction. |
| `UpdateRelease` | Internal throwing initializer. `ReleaseDTO.validatedRelease()` parses wire dates/URL, then calls that initializer. The raw DTO alone synthesizes `Decodable`. | A 64-character lowercase SHA-256 ID; valid version/build/minimum OS; finite, increasing dates; HTTPS manifest with a nonempty host and no credentials or fragment. All fields and cached components are immutable. | `ModelValidationTests` and `ValueInvariantTests` direct-construction rejection tests; `FeedValidationTests` wire/schema/date/URL tests; immutable and unchecked-construction fixtures. |
| `UpdateConfiguration` | Public throwing initializer, including its default arguments. No decoder or memberwise initializer. | Allowed HTTPS feed, or explicitly enabled HTTP loopback; immutable installed build, policy, URL and loopback flag; validated positive polling duration. `checkInterval` and `checkDelay` are projections. | `ConfigurationValidationTests` URL/loopback/interval tests and `ValueInvariantTests.URLPolicies`; construction and mutation fixtures. |
| `VersionComponents` | Failable string and `OperatingSystemVersion` initializers only. | Exactly three immutable `UInt64` components. Strings accept one to three ASCII numeric components and pad missing components with zero. OS input rejects negative components. No array-backed state can carry the wrong component count. | `ValueInvariantTests.versionComponentComparison`, `malformedVersionComponents`, and `operatingSystemComponentBoundaries`; canonical comparison, malformed-version and negative-OS tests; unchecked component and mutation fixtures. |
| `PositiveDuration` | Failable seconds initializer; `fifteenSeconds` calls that same checked initializer. | Finite interval from one nanosecond through `TimeInterval(Int64.max / 1_000_000_000)` seconds, with immutable seconds and a derived `Duration`. | `ValueInvariantTests.positiveDurationBoundaries`; configuration and loader timeout tests; unchecked initializer and mutation fixtures. |

`PositiveDuration.fifteenSeconds` is the only trusted duration constant. It validates `15` through the normal boundary and traps if that literal ever stops satisfying the boundary. There is no unchecked seconds initializer. `URLSessionUpdateFeedLoader.defaultTimeout` refers to that validated constant; its private initializer accepts `PositiveDuration`, while both explicit timeout initializers validate raw seconds first.

`WholeSecondUTCDate.parse` applies the wire-format rule before constructing a release. It requires the parsed date to format back to the identical UTC string, rejecting non-ASCII digits and calendar normalization; `FeedValidationTests.canonicalWireDates` checks both wire date fields. Direct release construction preserves the existing finite/increasing `Date` contract; whole-second UTC spelling belongs to JSON parsing. Feed objects remain closed schemas, duplicate JSON member names are rejected, and a present `releaseNotes` must be a string. Structural validation precedes version comparison. A valid older release is ignored before expiry and OS eligibility checks, as required by the protocol.

These value types have no writable subscript, reference-backed field, literal/raw-value conformance, Objective-C hydration path, or synthesized `Decodable` bypass. `CheckToken`, `InstallationToken`, and `LifecycleToken` are distinct private immutable reference types. Equality compares object identity. Their integer labels are used only for test observation and can wrap without making two tokens equal; completion APIs accept the nominal types. Obsolete work retains its token until completion, preventing identity reuse while that work can still finish.

## AC2: operation ownership and publication

| Stored authority | Variants and required payload | Transition authority | Rejected representation |
| --- | --- | --- | --- |
| `check: Check` | `settled(CheckResult)` or `running(CheckOperation)`. A running check owns a check token, lifecycle token, previous settled result, and `Task<Void, Never>`. | `checkForUpdates`, `completeCheck`, deactivation, and eligibility invalidation. | Running without a task/token/previous result; saving another running check as the previous result; using an installation token as check identity. |
| `installation: Installation` | `idle` or `running(InstallationOperation)`. A running installation owns its installation token, release, mode, and `Task<Bool, Never>`. Automatic mode carries a lifecycle token. | `beginInstallation`, `finishInstallation`, and deactivation. Preparation reads the owned release and mode. | Running without a release or task; substituting a void check task or a check token; automatic mode without lifecycle identity. |
| `lifecycle: Lifecycle` | `inactive(LifecycleToken)` or `active(PollingOperation)`. Active polling owns its lifecycle token and `Task<Void, Never>`. | `setActive`. | Active without its polling task; substituting check or installation identity. |

`CheckResult` cannot describe a running check. Checking and installation are independent axes, so a completed refresh can coexist with an earlier installer handoff. Finishing either operation changes only its own axis, apart from rechecking eligibility of a settled offer. A refresh retains its prior display release while installation remains disabled.

The lifecycle epoch changes on deactivation. Activation preserves that epoch so an explicit check started while inactive can be joined by the polling loop. Each polling iteration checks cancellation and its active lifecycle token before and after awaiting the shared check. Old loops cannot resume polling after reactivation.

All controller mutations are main-actor isolated. Remaining stored properties are immutable dependencies (`configuration`, OS version, handler, loader, clock, prompt store), identity issuance counters, the host's presentation preference, a test observation closure, and the publication guard. There are no parallel task slots, active flags, or mutable release/result caches.

`state` is an immutable public projection of the private variants. After initialization, `publishState()` is its only assignment site. `isPublishingState` guards publication reentrancy, not operation lifetime: because `@Published` notifies in `willSet`, an observer can change operation state before the outer assignment completes. The publisher drains the latest projection after that assignment. Operational decisions and convenience properties read the private authority instead of the possibly in-progress published assignment.

`states()` registers an independent `AsyncStream` continuation synchronously and yields the current authoritative snapshot before returning. Each continuation uses `bufferingNewest(1)`, so it retains at most one unconsumed projection and replaces an initial or intermediate projection when a newer state arrives first. `publishState()` yields to async continuations only after its Combine publication loop and synchronous reentrancy have drained. Continuations are weakly tied to the controller through their termination handlers, and the controller finishes all remaining streams during deinitialization. Terminating one stream removes only its continuation; it does not change checking, installation, or polling authority. Failed checks remain `UpdateState` values rather than sequence failures. Creating a stream does not call `setActive`, start a check, or otherwise change lifecycle authority.

Paired compiler fixtures check payload presence and token domains. Matching a completion to the current instance of an operation remains a runtime identity comparison. Task closures capture the token created for their operation; completion methods reject stale identities. Private construction sites are part of this audit, not an assumption that private code cannot contain a bug. Eligibility checks can call the injected clock, and prompt storage can call host code. The final ownership, release, and presentation-policy guards run after those callbacks return.

## AC3: transitions and runtime behavior

| Contract | Construction/transition rule | Deterministic test evidence |
| --- | --- | --- |
| Shared checks and caller cancellation | A running check is joined; caller cancellation cancels its owned task. Cancellation restores the previous settled result, then replaces any now-ineligible offer with its eligibility failure. | `coalescedChecks`, `callerCancellation`, `alreadyCancelledCheckDoesNotStartWork`, `cancellationRevalidatesRestoredRelease`. |
| Independent overlapping operations | A check result cannot clear the installation payload; installation completion cannot restore an older check result. | `successfulRefreshDuringInstallationIsRetained`, `installationDoesNotOverwriteNewerFailure`, `installationDoesNotOverwriteNewerStatus`, `publishedStateIsCoherentDuringInstallation`, and `installationCompletionRetainsAnInFlightRefresh`. |
| Deactivation, reactivation, stale results | Deactivation cancels owned tasks, restores and revalidates the previous check result, clears installation, and changes lifecycle identity before publishing. Old completions cannot mutate replacement operations. | `staleCompletionAfterBackgrounding`, `staleResultAfterReactivationIsIgnored`, `reentrantObserverPreservesReactivatedState`, `activationJoinsAnExplicitCheckWithoutStartingAnotherLoad`, `reentrantDeactivationDoesNotLoseTheCancellationState`, `deactivationRevalidatesRestoredRelease`. |
| Installation cancellation and retry | Cancellation observed before handler invocation skips handoff; cancellation during handoff reaches the owned handler task. The finish transition releases the installation slot. | `manualRequestAlreadyCancelledDoesNotScheduleInstallation`, `manualCallerCancellationPropagates`, `manualRetry`, `automaticCancellationBeforeHandler`. |
| Expiry and OS eligibility | Feed decoding checks eligibility for newer offers. `eligibilityError(for:)` rechecks expiry and OS before handoff; a scheduled task must still own the current release and operation. | `expirationBeforeHandoff`, `automaticExpirationBeforeHandler`, `expiredCurrentReleaseIsRejected`, `incompatibleOS`, `invalidCurrentOSVersion`, `injectedOperatingSystemVersionIsAppliedAtCheckTime`, `ReentrantBoundaryTests.clockPolicyChangeBeforeHandoff`. |
| Automatic-attempt persistence | Preparation consults and marks the original feed/app/release scope. Automatic suppression leaves manual installation available. | `automaticOnce`, `cancelledAutomaticHandoffDoesNotReprompt`, `preseededPromptPersistenceRemainsReadable`, `promptStoreRetainsReleaseHistory`, `persistedAutomaticSuppressionScopesByFeedAppAndRelease`, `promptStoreConservativelyReadsHistoricalValues`. |
| Response-size boundary | The default loader bounds receipt; the controller rejects an oversized complete body from any loader before decoding. | `URLSessionUpdateFeedLoaderTests.declaredSizeLimit`, `streamedSizeLimit`, `UpdateControllerTests.customLoaderResponseSizeIsEnforced`. |

Readiness comes from attached load/installation gates and synchronous controller event recording. Completion comes from awaited tasks or explicit finish events, including polling completion. Test cleanup cancels owned work, releases gates, and awaits completion even when assertions throw. Timeout sleeps are failure bounds; no test uses sleeps or fixed yield counts to infer progress.

Expiry, current operation identity, cancellation, OS eligibility, and persisted suppression are dynamic conditions. They cannot be fixed at release construction. Cancellation and expiry checks immediately before invocation are not atomic with an external handler's effects. The transition tests exercise controlled interleavings; they are not an exhaustive traversal of all possible schedules.

Prompt stores and feed loaders remain injectable host boundaries. The controller rejects a custom loader's oversized returned body before decoding, but only the loader can bound allocation during receipt and enforce its transport policy. The built-in prompt store reads historical Boolean markers and suppresses automatic attempts when an existing marker is non-Boolean; manual retry is independent of that marker. Custom stores must honor the supplied feed/app/release scope and retain attempts themselves. A successful handler return confirms URL handoff only; it does not prove native confirmation, download, signature verification, or installation. Physical acceptance still follows [device verification](device-verification.md).

## Verification and compatibility

Run from the repository root:

```sh
Scripts/check-invariants.sh
INTERNAL_APP_UPDATES_PYTHON=python3.11 Scripts/check.sh
git diff --check
```

The fixture script type-checks two positive controls and 26 negative fixtures, and compiles and executes `runtime-controller-token-reference-identity.swift`. The complete `Scripts/check.sh` also runs these fixtures. Each negative fixture must fail for its declared diagnostic. Private operation fixtures are appended to a temporary copy of the production controller in the same file; production access control and constructors are not rewritten. Value fixtures use the production declarations. A generic compiler failure is not sufficient evidence.

The public initializer and method signatures, snapshot cases, and persisted key format remain compatible with `6edc2a3`. Prompt scope is still `<bundleIdentifier>|<feedURL.absoluteString>` and the UserDefaults key is still `InternalAppUpdates.presented.<base64 UTF-8 scope>.<releaseID>`. Swift tools 6.3, Swift 6 mode, iOS 16, macOS 13, and Python 3.11 remain unchanged. The selected validation environment is Xcode 26.6 (17F113), Swift 6.3.3, and iOS Simulator SDK 26.5.

The independent review uses this matrix and the changed Swift sources. Acceptance requires resolving demonstrated counterexamples within the inspected ordinary APIs and recording the remaining evidence limits. Unsafe memory fabrication and arbitrary behavior inside injected host services are outside that boundary. The [input-boundary decision](0003-operation-identity-and-input-boundaries.md) records the stricter JSON and persistence policies.

## 2026-10-17 independent review disposition

An independent reviewer audited this matrix and the changed Swift code after implementation. The review found two concrete counterexamples:

1. An injected clock could switch automatic presentation to manual after the final policy guard. Eligibility now runs before the final ownership, release, and policy checks. `clockPolicyChangeBeforeHandoff` passes; an isolated negative control using the prior guard ordering fails its no-handoff assertion.
2. `DateFormatter` accepted Unicode digits in wire dates. The decoder now requires an exact parse/format round-trip. `canonicalWireDates` rejects Arabic, fullwidth, and mixed digits plus noncanonical calendar/time forms for both date fields.

The reviewer reran both focused regressions and inspected construction routes, token domains, payload requirements, every stored authority and transition, publication, lifecycle cancellation, overlap, reentrancy, eligibility, and persistence. Final disposition: **zero unresolved AC1–AC3 counterexamples through ordinary APIs**. The review introduced no public API or persistence changes.

## Recorded acceptance evidence

Validation on 2026-10-17 used the selected toolchain above:

| Command or inspection | Result |
| --- | --- |
| Baseline `INTERNAL_APP_UPDATES_PYTHON=python3.11 Scripts/check.sh` at `6edc2a3`, before edits | Exit 0: 58 Swift tests, 90 Python tests, unsigned iOS Simulator build. |
| Final `INTERNAL_APP_UPDATES_PYTHON=python3.11 Scripts/check.sh` | Exit 0: 79 Swift tests in 9 suites, 28 compiler fixtures, 90 Python tests, unsigned iOS Simulator build. No new or worsened failures. |
| `Scripts/check-invariants.sh` (standalone and in the complete check) | Exit 0: 2 positive controls and 26 negative fixtures, each matched to its intended diagnostic. |
| `git diff --check` | Exit 0. |
| Public API inspection | Baseline and final modules emitted with `swiftc -emit-module -swift-version 6 -target arm64-apple-macosx13.0`; `swift-api-digester -dump-sdk` outputs are byte-identical, and `swift-api-digester -diagnose-sdk` exits 0 with no differences. |
| Persistence compatibility | Exact legacy key seed remains readable; automatic writes survive recreated stores/controllers; feed, app, and release scope remain independent; manual retry remains available. |

Package checks and the simulator build do not establish physical over-the-air installation. No device installation was attempted for this invariant change.

## Python publication invariants

The Python goal covers the publication carriers present at `6edc2a3` and their CLI and S3 adapters. The accepted Swift design above remains unchanged. The Python models describe validated values and expected file contracts. They do not attest that a signature, local file, or remote endpoint will remain valid after inspection.

### AC1–AC2: construction and correlated variants

| Carrier | Construction boundary and stored authority | Test evidence |
| --- | --- | --- |
| `IPAInfo` | The public constructor validates identity, title, numeric versions, digest, optional profile date, and optional local path. `MetadataInspection` requires absent signing expiry; `VerifiedIPA` requires it. The latter records inspector or trusted injected-verifier output, not an unforgeable attestation. | `PublicationModelInvariantTests.test_ipa_constraints_and_output_detachment`, `test_signing_outcome_variants_cannot_exchange_metadata`; `PublishingTests.test_metadata_only_inspection_cannot_be_published`; `SigningInspectionTests`. |
| `Release`, `Feed`, `Links` | Closed feed/release parsing calls the same constructors used internally. Releases retain canonical UTC dates, ordered expiry, numeric components bounded by `UInt64`, lowercase SHA-256, and an allowed HTTPS manifest URL. Links derive both entry points from one validated base URL. | `PublicationModelInvariantTests.test_release_constraints_reject_the_same_bad_values_at_every_construction_boundary`, `test_version_date_and_url_boundary_matrix`, `test_feed_decoding_is_closed_and_nested_children_are_validated`, `test_valid_values_are_immutable_and_round_trip_through_feed_json_shape`. |
| Release history | `_release_history` decodes every persisted `release.json` into a `Release` and returns a tuple. Version comparisons use `Release.key`. | `PublishingTests.test_idempotence_and_version_conflict`, `test_withdrawal_preserves_version_history`; `PythonCompatibilityTests.test_legacy_publish_withdraw_and_links_keep_dict_outputs_and_history`. |
| `FileSpec`, `DigestSize`, `FileEntry` | Constructors validate path/role/header contracts, lowercase digest, and integer byte size from 1 through 2 GiB. Expected files are immutable tuples of validated entries. | `DeploymentInvariantTests.test_file_spec_and_digest_constructors_and_replacements_reject_every_invalid_scalar`, `test_file_spec_requires_concrete_release_names_but_keeps_generic_specs_valid`, `test_file_entry_construction_and_replacement_rejects_each_contract_field`, `test_decoding_rejects_every_file_field_and_schema_operation_category`, `test_file_entry_direct_replacement_and_decoding_correlate_ipa_digest_and_query_free_url`. |
| `ActiveDeployment`, `WithdrawalDeployment` | Separate types derive schema 1 and schema 2/`withdraw`. Each owns its exact ordered file contract. Schema 1 parsing also accepts the existing `operation: "publish"` extension; contradictory operations are rejected. | `DeploymentInvariantTests.test_deployment_constructors_and_replacements_reject_all_structure_categories`, `test_decoding_rejects_every_file_field_and_schema_operation_category`. |
| `ActivePublicationReport`, `WithdrawalPublicationReport` | Constructors correlate release presence, deployment variant, links, manifest location, ordered release paths, and IPA digest. | `DeploymentInvariantTests.test_valid_active_and_withdrawal_reports_round_trip`, `test_reports_reject_wrong_children_and_correlations_by_constructor_and_replacement`, `test_decoding_rejects_correlated_mismatches`. |
| `ActiveVerification`, `WithdrawnVerification` | Separate outcomes require the appropriate release presence and links. File counts are derived as five or two; byte totals are bounded integers. Active manifest and links must share a base. | `VerificationInvariantTests.test_construction_and_replacement_reject_invalid_payloads_and_totals`, `test_outcomes_and_nested_values_are_immutable_and_serialization_is_detached`, `test_decoding_rejects_outcome_presence_counts_links_and_metadata_mismatches`, `test_active_and_withdrawn_round_trip_preserve_public_shapes`. |

Every model uses `validated_model` over a frozen, slotted dataclass. Replacements call its checked constructor. The wrapper rejects a second `__init__` call before field writes and rejects subclasses that could override validation. Models retain no dictionaries or lists; serialized dictionaries are detached copies. Tests exercise ordinary attribute assignment, nested mutation, replacement, reinitialization, and subclass creation. Deliberate `object.__setattr__`, private `Path` internals, monkey-patching model classes, custom allocation, and unsafe reflection are outside this proof boundary.

### AC3: adapters and core consumers

| Allowed raw boundary | Conversion and consumers |
| --- | --- |
| JSON/plist input and output | Feed and history parsing constructs `Feed`/`Release`; report parsing constructs the appropriate report variant. `to_dict` and manifest serialization allocate wire values. Raw signing subprocess output, profiles, entitlements, and HTTP response headers remain external inputs. |
| Existing publishing APIs | `publish` and `withdraw` return the established feed dictionaries; `links_for_base_url` and `publication_links` return the existing link keys; `inspect_ipa` returns `IPAInfo`. `_publish`, `_withdraw`, feed/history decisions, HTML generation, and release recovery consume models. The explicit injected `verifier` remains a trusted compatibility boundary. |
| Existing deployment APIs | `publication_report`, `load_publication`, `validate_publication`, and `validate_local_files` keep their signatures and dictionary shapes. `_publication_report`, `_load_publication`, `_validate_generated_files`, and `_validate_local_files` consume typed values. Public report loading preserves additional object fields; the core discards unrecognized report fields at parsing. Feed JSON remains a closed schema. |
| Existing verification API | `verify_publication` serializes the typed outcome from `_verify_publication`. The core iterates `FileEntry` values and validates actual response bytes and headers. |
| CLI serialization | `cli.py` formats the existing feed/report/inspection/result shapes. Its public API calls cross the compatibility adapters above. JSON field names and human output remain compatible. |
| S3 report input/output | `Examples/Deployment/upload-s3.py` consumes the public report adapter, validates all local files before its first write, and retains report order and metadata. Its raw report representation is the project-owned uploader boundary. |

`PythonCompatibilityTests` verifies public parameter names/defaults, the `IPAInfo` constructor fields, feed/history/report round trips, retained report extensions, and the verification result shape. `CLITests`, `DeploymentTests.test_cli_adds_report_without_altering_feed_schema`, and the withdrawal CLI tests cover serialization.

The core does not turn models back into dictionaries to make publication, history, deployment, or verification decisions. Public dictionary compatibility does not weaken internal constructors: changing an adapter result cannot change an existing model.

### AC4: runtime proof matrix

These checks retain filesystem and HTTP defenses after construction. Each named test is part of the complete Python suite.

| Required runtime fact | Test evidence |
| --- | --- |
| Publish the verified snapshot | `PublishingTests.test_publishes_the_snapshot_verified_by_the_verifier`. |
| Equivalent version/build cannot change bytes | `PublishingTests.test_idempotence_and_version_conflict`, `test_withdrawal_preserves_version_history`. |
| Withdrawal retains history and assets | `PublishingTests.test_withdrawal_preserves_version_history`; `WithdrawalTests.test_report_updates_only_page_then_feed_and_preserves_assets`. |
| Active upload order | `S3DeploymentExampleTests.test_uploads_ordered_files_with_report_headers_then_verifies`. |
| Withdrawal upload order | `S3DeploymentExampleTests.test_withdrawal_uploads_page_then_feed_and_preserves_release_assets`. |
| Altered local artifacts block upload | `DeploymentTests.test_changed_missing_or_escaping_files_prevent_upload_preflight`, `test_reports_do_not_bless_edited_generated_artifacts`; `S3DeploymentExampleTests.test_changed_local_file_means_no_remote_writes`; `PublishingTests.test_idempotent_publish_rejects_missing_or_changed_release_metadata`. |
| Corrupt, short, or oversized remote IPA | `VerificationTests.test_rejects_corrupt_ipa_of_expected_size`, `test_rejects_short_and_oversized_ipa`, `test_streams_large_files_with_a_bounded_read`. |
| Stale active and withdrawn feeds | `VerificationTests.test_rejects_stale_feed`; `WithdrawalTests.test_verifier_rejects_stale_feed_and_cached_withdrawal`. |
| Wrong returned Content-Type or Cache-Control | `VerificationTests.test_rejects_wrong_content_type`, `test_enforces_immutable_and_mutable_cache_policies`. |
| Unsafe report paths | `DeploymentInvariantTests.test_file_spec_and_digest_constructors_and_replacements_reject_every_invalid_scalar`, `test_decoding_rejects_every_file_field_and_schema_operation_category`; `DeploymentTests.test_changed_missing_or_escaping_files_prevent_upload_preflight`. |
| HTTPS redirects | `VerificationTests.test_redirect_handler_rejects_downgrades_and_credentials`, `test_redirect_handler_allows_https`. |
| Signing command failure | `SigningInspectionTests.test_codesign_failure_is_reported_before_profile_processing`, `test_codesign_os_failure_is_a_structured_error`. |
| Expired profile and identity mismatch | `SigningInspectionTests.test_expired_profile_is_rejected`, `test_signed_identifier_mismatch_is_rejected`. |
| Metadata differs from verified signing | `SigningInspectionTests.test_metadata_only_inspection_never_runs_signing_commands`, `test_verified_inspection_extracts_the_ipa_and_returns_profile_expiry`, `test_verified_inspection_requires_macos`, `test_nested_bundle_without_a_profile_is_rejected`. |

Signing tests use synthetic archives and mocked `codesign`/`security` output. HTTP tests use mocked responses, and S3 tests use fake commands. They establish code paths and rejection behavior, not real signing validity, a live deployment, or physical installation. Real signing, endpoint, and device acceptance remain separate checks; no OTA installation is claimed.

### Python review and acceptance record

Baseline at `8e25e13`, before Python edits: `INTERNAL_APP_UPDATES_PYTHON=python3.11 Scripts/check.sh` exited 0 with 79 Swift tests in 9 suites, 28 compiler fixtures, 90 Python tests, and an unsigned iOS Simulator build. The selected Python interpreter was 3.11.16.

An independent reviewer examined this matrix, every scoped model and consumer, compatibility adapters, CLI/S3 boundaries, and the fixed runtime scenarios. The review found three construction/runtime counterexamples:

1. `FileEntry` accepted a query-bearing deployment URL. Its constructor now applies the query-free base URL policy to the complete file URL, including an empty `?` suffix.
2. An IPA entry could carry a digest different from the release ID in its path. The entry constructor now checks that correlation before the value can enter a deployment.
3. Repeating an identical publication could succeed after its `release.json` was removed or changed. The current-release branch now decodes that file into a `Release` and requires equality with the feed before returning. This was a pre-existing gap in the scoped runtime path.

`DeploymentInvariantTests.test_file_entry_direct_replacement_and_decoding_correlate_ipa_digest_and_query_free_url` proves the first two repairs at all three construction routes. `PublishingTests.test_idempotent_publish_rejects_missing_or_changed_release_metadata` covers missing, malformed, and changed metadata without advancing the feed. The reviewer reran the original reproducers, all 24 Python invariant tests, and all 129 Python tests. Final review disposition: **zero unresolved ordinary-API AC1–AC4 counterexamples**. Deliberate reflection or unsafe deserialization is not an ordinary construction route.

The compatibility suite also caught a renamed public `base_url` keyword; it was restored. Snapshot digest validation now runs before publication. Conflict/history tests use changed bytes with matching digests and assert the conflict-specific errors, so an earlier snapshot rejection cannot satisfy those tests. Mutable HTTP responses with positive `s-maxage` are rejected even when they also contain `no-cache` or `no-store`.

Final validation on 2026-10-17 used Xcode 26.6 (17F113), Swift 6.3.3, and iOS Simulator SDK 26.5. No Swift source, compatibility declaration, dependency, or generated sample output changed.

| Command or inspection | Result |
| --- | --- |
| `Scripts/check-invariants.sh` | Exit 0: 28 Swift compiler fixtures and 24 Python invariant tests. The standalone command used default Python 3.14.7. |
| `INTERNAL_APP_UPDATES_PYTHON=python3.11 Scripts/check.sh` | Exit 0: 79 Swift tests in 9 suites, 28 compiler fixtures, 24 Python invariant tests, all 129 Python tests, and the unsigned iOS Simulator build. Python 3.11.16; no new or worsened baseline failures. |
| `PYTHONPATH=tools python3.11 -m unittest tools.tests.test_python_compatibility -v` | Exit 0: all 5 compatibility tests. These also pass within the complete check. |
| Independent review: `PYTHONPATH=tools python3.11 -m unittest discover -s tools/tests -v` | Exit 0: all 129 tests; the reviewer separately reran the 24 invariant tests and three original counterexamples. |
| `git diff --check` | Exit 0. |
| Documentation inspection | Relative links resolve; every Python test named in this matrix exists. |

AC1 and AC2 are covered by the 24 construction/variant invariant tests and constructor inspection. AC3 is covered by the adapter inventory, five compatibility tests, CLI/S3 tests, and consumer review. AC4 has the named runtime evidence above, including eight mocked signing tests. AC5 has no unresolved review findings. AC6 passes both required scripts and whitespace validation. Physical installation and live hosting remain untested.

## 2026-10-18 non-Swift boundary checks

This assessment started from `423c210` and covered the Python modules, their tests, the CLI launcher, deployment examples, check scripts, sample generator and generated configuration, package metadata, and CI configuration. Swift implementation was outside the assessment. The baseline Python suite passed 132 tests.

The construction inventory included direct constructors, dictionary and JSON decoding, replacement, ordinary mutation, and mutable aliases. The transition inventory included publication retries, withdrawal, history recovery, upload order, response verification, local serving, and archive export. No Python type-correctness companion was installed; source inspection and executable boundary tests supplied the evidence.

The assessment found and repaired these counterexamples:

| Boundary | Counterexample and resulting check | Regression evidence |
| --- | --- | --- |
| Active publication retry | Changed release notes were silently ignored. A retry now compares the requested immutable release metadata with the current release, retaining its publication timestamp. | `PublishingTests.test_idempotent_publish_requires_the_original_release_notes` |
| Active withdrawal | A different deployment base could rewrite the page and feed. The supplied base must match the active release's manifest before either file changes. | `PublishingTests.test_withdraw_requires_the_active_publication_base_url` |
| Publication history | Deleting a digest directory's metadata allowed equivalent version/build reuse after withdrawal. Digest-named directories now require readable metadata with a matching release ID. | `PublishingTests.test_incomplete_release_history_cannot_enable_version_reuse` |
| Signing metadata | Empty team identity and Boolean device entries could pass the mocked signing path. Identity fields and device entries require nonempty strings; decoded plist containers are checked before use. | `SigningInspectionTests.test_malformed_profile_identity_and_device_values_are_rejected` |
| Saved deployment report | Escaped-equivalent duplicate digest members were accepted by selecting the last value. The reader rejects duplicate JSON names before model validation. | `DeploymentTests.test_report_read_rejects_duplicate_expected_byte_members` |
| Hosted verification | Malformed Cache-Control quoting could satisfy a cache policy. Quote-aware parsing rejects these forms while accepting empty list elements and quoted commas or escapes. Nonnumeric timeout inputs that raised `TypeError` now enter the publication error boundary. | `VerificationTests.test_enforces_immutable_and_mutable_cache_policies`, `test_rejects_invalid_timeouts_before_networking` |
| S3 destination | Empty path components could be normalized away or used in object keys. The uploader rejects them before invoking AWS; one optional trailing slash remains accepted. | `S3DeploymentExampleTests.test_destination_rejects_empty_prefix_components_before_remote_writes` |
| Local HTTP response | Atomic feed replacement between metadata lookup and opening the file mixed old headers with new bytes. Metadata and streaming now use one open file descriptor. | `ServerTests.test_atomic_replacement_keeps_response_metadata_and_bytes_together` |
| Tailscale configuration | A background Web block hid foreground conflicts, and an existing Funnel listener could be reused or altered. The adapter checks foreground handlers and refuses enabled Funnel on port 443. It validates the backend port and node DNS name before writing a route. | `TailscaleTests` conflict, Funnel, port, DNS, and private-route controls |
| Archive export | A successful command status could return a nonexistent output path, and an existing dangling output symlink bypassed the overwrite guard. Export now rejects that symlink and requires a resulting directory. | `ExportingTests` missing-directory, symlink, and successful-export controls |

Malformed UTF-8 feed files also produce `PublicationError` through the existing local-feed error boundary. The tests pair rejected inputs with valid controls; they do not establish a named error for every possible Python object or external failure.

These checks assume ordinary Python APIs, trusted injected verifiers and command runners, and the documented external serialization of deployments. Local publication history cannot detect a release after its entire directory has been deleted. The server response check covers atomic replacement, not concurrent in-place edits. Signing and external-command tests use mocks; live hosting, real signing, cross-runner coordination, and physical installation remain separate acceptance work. The evidence does not establish every possible input or interleaving.

Final `INTERNAL_APP_UPDATES_PYTHON=python3.11 Scripts/check.sh` exited 0 with 98 Swift tests, 29 compiler/runtime fixtures, 24 focused Python invariant tests, all 146 Python tests, sample regeneration, and the unsigned iOS Simulator build. The environment was Python 3.11.16, Xcode 26.6 (17F113), and Swift 6.3.3. `git diff --check`, changed-document relative links, and shell syntax checks passed. No Swift source or generated sample output changed.
