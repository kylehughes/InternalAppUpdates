# CI and private package releases

Status: Accepted

Date: 2026-10-18

## Context

The package has a Swift client, Python publishing tools, and a sample iOS app. A single macOS check covered the baseline tests and sample build, but did not verify distribution installation, documentation, multiple supported toolchains, or release publication. The repository is private.

## Decision

Declare the checked support configurations in `Config/support.json` and validate them against the package metadata and workflow matrices. Test Apple Debug and Release builds, actual iOS simulator execution, Python on macOS and Linux, distributions, DocC, coverage output, and selected concurrency tests under Thread Sanitizer. Run Address Sanitizer after merge and three independent Thread Sanitizer processes nightly. Keep job and step limits explicit.

Use one required `CI gate` that accepts only successful prerequisite results. This gives branch protection a stable check name while preserving visibility into individual lanes. Keep broader runtime and physical-device acceptance claims separate from the tested build configurations.

Release only an explicitly selected `main` commit and a matching canonical stable version. Audit the exact candidate, build artifacts in read-only jobs, and verify their manifest after download. Give write permission only to the publishing job. It creates a draft, verifies the bytes of all four assets, and publishes only the complete release. Record intended writes before sending them and reconcile ambiguous responses by reading remote state. Never move an existing tag or overwrite an asset.

Store documentation and Python distributions as private workflow artifacts and GitHub release assets. Public Pages and PyPI publication would require a separate distribution decision.

## Consequences

The wider CI matrix costs more runner time than the original baseline check. Separate jobs identify failures, bounded sanitizer selection limits routine cost, and a support file makes the checked versions explicit. Hosted runner upgrades require maintenance rather than silently changing the certified toolchain.

Release retries require the original candidate bytes. Rebuilding the same source can change archive metadata, so a conflicting rebuild stops instead of replacing an asset. Retained manifests and receipts support recovery, but workflow artifacts expire and must be downloaded for longer retention.

The release workflow defaults to a dry run. No package release is created merely by merging code or pushing a tag. See the [delivery guide](delivery.md) for commands, bounds, and recovery.
