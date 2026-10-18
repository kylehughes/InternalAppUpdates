# Deploy from your own script

Use a project-owned shell script or CI job to connect Internal App Updates to your HTTPS host. The publisher prepares signed files and describes how to upload them. Your existing tools own credentials, cloud configuration, access control, and upload operations.

## Prepare, upload, verify

Prepare the local site and save the publication report outside the served directory:

```sh
mkdir -p Artifacts
internal-app-updates publish \
  --ipa App.ipa \
  --output Artifacts/updates-site \
  --base-url https://updates.example/your-app/ \
  --json > Artifacts/publication.json
```

Continue only when the command exits successfully. The CLI also writes errors as JSON, so the existence of the report file is not proof of success. The report contains local filesystem paths; it is workflow metadata and should not be uploaded.

The upload step reads `deployment.files` in order. Each entry gives a local relative `path`, destination `url`, `contentType`, `cacheControl`, byte `size`, and `sha256`. Use your uploader's equivalent of Content-Type and Cache-Control object metadata. Preserve paths relative to `deployment.directory` at `deployment.baseURL`.

After the upload succeeds, verify what the host actually serves:

```sh
internal-app-updates verify --publication Artifacts/publication.json
```

Successful verification prints the version/build, installation page URL, and feed URL. Add `--json` for structured results. The verifier downloads all five files, including the complete IPA, comparing byte sizes and SHA-256 digests and checking response types and cache headers. It streams the IPA instead of loading it into memory. `--timeout 30` sets the timeout for each network operation; it is not a total transfer deadline.

## Withdraw, upload, verify

A local withdrawal needs only its output directory:

```sh
internal-app-updates withdraw --output Artifacts/updates-site
```

To update a remote host, supply the same HTTPS base URL and save the withdrawal report outside the served directory:

```sh
internal-app-updates withdraw \
  --output Artifacts/updates-site \
  --base-url https://updates.example/your-app/ \
  --json > Artifacts/withdrawal.json
```

`withdraw --json` requires `--base-url` and rejects the command before changing the local site when it is absent. The report has `release: null` and lists only `index.html` followed by `feed.json`. Upload the page first and the feed last, using each entry's declared `Content-Type` and `Cache-Control`. Do not delete release assets: a device that began downloading the old build can still need them.

Run the same verification command against the withdrawal report:

```sh
internal-app-updates verify --publication Artifacts/withdrawal.json
```

It verifies the two mutable files and reports a verified withdrawal. If an upload or verification fails, retry the same report only while its local files remain unchanged. Preserve the site's release history. To restore an offer, republish the same verified IPA with its original base URL and release notes, or publish a newer build.

Verification uses ordinary HTTPS GET requests with the system trust store and follows HTTPS redirects. It does not supply app-specific login credentials or bypass stale caches with a query parameter. It can run on a different machine without the local site directory, provided it has the saved report and network access to the host.

A successful check establishes that this endpoint served the expected bytes and headers at that time. It does not establish iOS installation, device eligibility, or whether every CDN edge honors the configured caching policy.

## Copyable S3 example

The [deployment examples](../Examples/Deployment/) contain a wrapper and an uploader that use the normal AWS CLI. Copy both into your project and adapt them to your conventions. The bucket, HTTPS endpoint, and credentials must already exist. The example does not create infrastructure, modify bucket policies, enable public access, or manage credentials.

The uploader validates all local files before its first remote write, uploads each file with its declared metadata, and runs endpoint verification. A failed upload stops before the remaining files; a failed verification exits unsuccessfully. Only successful verification produces the final installation link.

S3's website endpoint does not support HTTPS. Use an HTTPS delivery endpoint such as CloudFront with an S3 origin. Configure the page/feed cache behavior with a minimum TTL of zero so that revalidation headers can take effect. A positive CloudFront minimum TTL can override the origin's `no-cache` policy. See [AWS website endpoint guidance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/WebsiteEndpoints.html) and [CloudFront cache policy semantics](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cache-key-understand-cache-policy.html).

The phone's installer must reach the manifest and IPA without borrowing the app's credentials. Bucket privacy and viewer authentication are separate concerns; upload credentials do not grant the phone download access. See [hosting access requirements](hosting.md#another-https-host).

## Ordering, retries, and concurrent runs

Serialize the entire prepare/upload/verify operation for each app/channel, including across CI runners. Use your CI system's concurrency control or existing deployment lock. The local publisher lock ends before the upload starts and cannot protect the remote feed. Independent workspaces must also preserve or restore the channel's local publication history before preparing the next release; a new empty directory cannot detect an older release already on the host.

For an active publication, upload the three immutable release files first, the installation page next, and `feed.json` last. For a withdrawal, upload the installation page and then `feed.json`. Stop on the first error. Feed-last keeps the updater from seeing a release whose files have not uploaded. The page can offer the new build slightly before the feed does; they are separate object writes, not a remote transaction.

Keep the report and prepared directory unchanged until deployment succeeds. If an upload fails, rerun the uploader with that same report. Do not rebuild for an upload retry, edit immutable artifacts, delete previous releases, or upload the entire directory with an unordered sync. Older releases may still have downloads in progress. If another deployment has advanced the channel, do not retry an older report over its feed.

If uploading succeeds but verification fails, some or all files are already on the host. Inspect the reported file and failure: stale caches, content-type metadata, or incomplete uploads need different fixes. Correct the host or retry the same files; verification failure does not automatically roll back remote objects.

## Publication report contract

The existing top-level `schemaVersion`, `bundleIdentifier`, `release`, and `links` fields remain intact. `schemaVersion` identifies the feed protocol. `deployment.schemaVersion` independently versions the deployment metadata: active publications use `1`, and withdrawals use `2` with `operation: "withdraw"`.

| Field | Meaning |
| --- | --- |
| `deployment.directory` | Absolute local directory containing the prepared site. |
| `deployment.baseURL` | Destination HTTPS base URL, including a trailing slash. |
| `deployment.files` | Ordered array of the current release's files and mutable entry points. |
| `files[].path` | Relative path beneath the directory and base URL. |
| `files[].url` | Complete HTTPS URL the verifier requests. |
| `files[].role` | `release`, `install-page`, or `feed`. |
| `files[].sha256` | Lowercase SHA-256 of the exact file bytes. |
| `files[].size` | File length in bytes. |
| `files[].contentType` | Content-Type to set when uploading. |
| `files[].cacheControl` | Cache-Control to set when uploading. |

Version 1 contains these five active-publication entries, in order:

| Path | Role | Content-Type | Cache-Control |
| --- | --- | --- | --- |
| `releases/<id>/app.ipa` | `release` | `application/octet-stream` | `public, max-age=31536000, immutable` |
| `releases/<id>/manifest.plist` | `release` | `text/xml` | `public, max-age=31536000, immutable` |
| `releases/<id>/release.json` | `release` | `application/json` | `public, max-age=31536000, immutable` |
| `index.html` | `install-page` | `text/html` | `no-cache` |
| `feed.json` | `feed` | `application/json` | `no-cache` |

Version 2 represents a withdrawal. It has `release: null`, `deployment.operation: "withdraw"`, and these two entries, in order:

| Path | Role | Content-Type | Cache-Control |
| --- | --- | --- | --- |
| `index.html` | `install-page` | `text/html` | `no-cache` |
| `feed.json` | `feed` | `application/json` | `no-cache` |

Uploaders and verifiers must support the report version they accept, or reject unsupported deployment schema versions before remote writes. An active report may retain the legacy `operation: "publish"` extension; contradictory operation values are rejected. Ignore other additional object fields; preserve the supplied file order. The report is an expected-byte contract, not an authorization credential or a signed attestation.

JSON object member names must be unique, including names with equivalent JSON escapes. The saved-report reader rejects duplicates before validating expected bytes and deployment metadata.

The verifier accepts content-type parameters and `application/xml` for the manifest. Release responses need unqualified `public` and `immutable`, a `max-age` of at least 31536000 seconds, and no `private`, `no-cache`, or `no-store`. An explicit `s-maxage` must also meet that lifetime. Mutable responses need unqualified `no-cache`, `no-store`, or `max-age=0, must-revalidate` without a positive `s-maxage`. Duplicate cache directives are rejected as ambiguous. These checks inspect returned headers; they cannot inspect CDN configuration.

Reports from earlier CLI versions lack `deployment` and cannot be verified by this command. Generate a new report by repeating `publish --json` with the same IPA, existing output directory, and base URL. Repeating an identical publication preserves the release and its files.
