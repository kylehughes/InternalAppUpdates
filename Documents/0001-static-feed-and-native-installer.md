# Static feeds and native installation

Status: Accepted

Date: 2026-10-14

## Context

Internal app development needs a way to deliver new signed builds when a phone is away from the development Mac. The app should offer an install button or automatically request installation when a build becomes available. The same integration needs to work across apps and hosting environments.

## Decision

Use three independent components: a Swift client, a publisher that produces static files, and an optional local file server. Tailscale Serve is a hosting adapter on the server, not a dependency of the app or feed protocol. A developer can replace both the publisher and server while retaining the JSON and installation-manifest contracts.

The client checks a single configured feed. Channels are separate feed URLs. It compares the marketing version and build number with the installed bundle, validates compatibility and expiration, and hands an `itms-services` URL to iOS. iOS owns download, verification, confirmation, and replacement. A successful handoff is not reported as a successful installation.

The publisher validates a signed IPA, writes immutable assets identified by the IPA hash, and atomically advances the feed last. Version/build pairs cannot be reused for different artifacts. This makes the ordinary bundle version fields sufficient to tell whether an update is newer; apps do not need a generated release-identity file.

The client owns foreground polling and cancels it when inactive. Automatic prompts are persisted per feed and app, and attempted once per release. A manual action can retry. This avoids repeated interruptions after the user dismisses a system prompt. Apps control the policy and supply their own interface and copy.

## Consequences

Static hosting works on private tailnets and ordinary HTTPS hosts without a permanent build-management service. Publishing is explicit, so an incomplete archive or half-uploaded artifact cannot become the latest build merely by appearing in a watched folder.

An initial installation is required. The system installer must independently reach the manifest and IPA; cookies, headers, and client identities used by the app's feed loader are not automatically available to it. A private network endpoint can protect all three files without adding app-level authentication.

The publisher's signing checks are useful preflight checks, not a replacement for iOS validation. Provisioning can expire, a device can be absent from a profile, and certificate trust can change. The exact development-signed cellular update flow requires a physical-device acceptance test.

This design does not create an Xcode debugging transport. It also does not infer internal-distribution eligibility from the Swift `DEBUG` condition: build optimization and signing are separate choices.
