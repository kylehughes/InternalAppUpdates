# Release documentation on GitHub Pages

Status: Accepted

Date: 2026-10-18

## Context

The package initially retained DocC only as private CI artifacts and release attachments. A browsable public site is now requested, using the same release-based deployment model as Coalesced. The source repository and package release assets remain private.

## Decision

Publish the latest stable release's DocC site at `https://kylehughes.github.io/InternalAppUpdates/`. The release workflow prepares a Pages artifact from the certified documentation ZIP and deploys it after the GitHub release is published. Retained-candidate retries reuse that ZIP rather than rebuilding its contents.

Provide a separate manual Documentation workflow that runs from a release tag. Verify that the release is published, is not a prerelease, is still GitHub's latest release, and has a remote tag resolving to the certified commit. Repeat this check immediately before either deployment path writes to Pages. Serialize deployments through a shared concurrency group.

Separate building from deployment. Build jobs retain read permissions; deployment jobs receive Pages and identity-token write permissions in the `github-pages` environment. Allow `main` for release workflows and version tags for manual deployments. Keep existing repository contents permissions and branch protection unchanged.

Generate the site with the `InternalAppUpdates` hosting base path, a root redirect to the module page, and verified HTML and JSON entry points. Continue attaching the DocC archive and static ZIP to private package releases.

## Consequences

API documentation and its integration examples become public. Source code, Python distributions, and release attachments retain the repository's access controls. This supersedes the documentation visibility decision in [ADR 0004](0004-ci-and-private-releases.md); it does not introduce PyPI publication.

A failed Pages deployment does not undo a published release. The manual workflow can recover the latest release's site after its original Pages artifact expires. Older-release retries stop rather than replacing newer documentation. See the [delivery guide](delivery.md) for deployment and recovery commands.
