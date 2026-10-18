# Operation identity and input boundaries

Status: Accepted

Date: 2026-10-18

## Context

The invariant assessment found that operation identity depended on wrapping integer counters. It also found unspecified handling of duplicate JSON members and corrupt prompt markers, and a difference between Swift and Python acceptance of null release notes. The cancellation documentation omitted eligibility revalidation of a restored offer.

## Decision

Use a distinct immutable reference token for each check, installation, and lifecycle epoch. Equality compares object identity. Tasks retain their tokens until completion, so a replacement operation cannot acquire the same identity while an obsolete task still holds it. Separate token classes prevent exchanging identity domains. Integer labels remain available for test events; their values do not decide whether a completion belongs to an operation.

Reject duplicate JSON members in feed and release-metadata input before accepting parsed fields, including equivalent escaped names. Keep schema version 1 and require optional release notes to be strings when present. The publisher already emits that form. Swift's syntax parser remains responsible for JSON validity; a separate pass over the original payload detects duplicate names hidden by dictionary conversion. Python uses its JSON parser's object-pairs hook.

Preserve Boolean prompt markers and their existing keys. Treat a present non-Boolean marker as an already-recorded attempt. This favors avoiding repeated automatic interruptions when persistence is damaged; an eligible release remains manually retryable. Custom prompt stores remain responsible for their own persistence policy.

Enforce the complete response-size limit in the controller as well as the default loader. Custom loaders still own bounded receiving, transport security, timeouts, and cancellation.

Restore a canceled check's prior result, then revalidate a retained offer. Document that an expired offer becomes a failure. Cancellation checks prevent invocation when cancellation is observed before the handler starts; they cannot provide an atomic guarantee against cancellation or clock changes during an external handoff.

## Consequences

Operation tokens require a small allocation per operation or lifecycle epoch. Their lifetime follows the work that uses them. No counter exhaustion or probabilistic identifier assumption is needed for completion matching.

Duplicate members and null release notes are stricter input rejections. Unique-member feeds with omitted or string notes retain the version 1 shape. Existing persisted Boolean values retain their meaning; corrupt values can suppress automatic presentation until the host repairs storage, while manual retry remains available.

Compiler fixtures check token domains and payload presence. Runtime regressions exercise equal diagnostic labels, stale completion, expiry on cancellation, persistence restoration, and raw JSON boundaries. These checks do not establish every possible schedule or physical installation; the [device acceptance procedure](device-verification.md) remains required for replacement and data preservation.
