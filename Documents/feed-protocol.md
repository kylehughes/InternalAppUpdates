# Feed protocol, version 1

A feed represents one app on one update channel. Serve it as JSON over HTTPS with revalidation enabled. The default client accepts at most 256 KiB.

```json
{
  "schemaVersion": 1,
  "bundleIdentifier": "com.example.YourApp",
  "release": {
    "id": "6b098f652bea692396aa33df0fd1d71bb9025ed6a3d63e58d47b1684e4c9f417",
    "version": "1.0",
    "build": "2",
    "minimumOSVersion": "16.0",
    "publishedAt": "2026-10-14T00:00:00Z",
    "expiresAt": "2027-01-01T00:00:00Z",
    "manifestURL": "https://updates.example/your-app/releases/6b098f652bea692396aa33df0fd1d71bb9025ed6a3d63e58d47b1684e4c9f417/manifest.plist",
    "releaseNotes": "Improved scrolling and search."
  }
}
```

`releaseNotes` may be omitted; when present, it must be a string, including an empty string. A present `null` is invalid. `release: null` withdraws the current offer. All other illustrated fields are required. Version 1 objects are closed: the root accepts only `schemaVersion`, `bundleIdentifier`, and `release`, while a release accepts only the illustrated fields. The client rejects unknown fields.

JSON object member names must be unique within each object. The Swift client and Python feed and release-metadata readers reject duplicates before accepting parsed fields, including names with equivalent JSON escapes such as `"id"` and `"\u0069d"`. Repeating a name with the same value is also invalid. This avoids depending on which value a decoder retains.

Dates use canonical UTC ISO 8601 with whole seconds and ASCII digits (`YYYY-MM-DDTHH:MM:SSZ`); normalized calendar values and alternate digit spellings are rejected. `id` is the lowercase SHA-256 of the complete IPA, not a bundle identifier or a build number.

`version`, `build`, and `minimumOSVersion` contain one to three ASCII numeric components separated by periods. The Swift client accepts components within UInt64. Compare versions first, then builds when versions are equal. Missing components are zero: `1`, `1.0`, and `1.0.0` are equivalent. A publisher must not reuse an equivalent version/build pair for different artifact bytes, including after withdrawal.

The bundle identifier must match the installed app exactly. The client validates the schema and release fields before comparing versions. The manifest URL requires an HTTPS scheme and nonempty host, without credentials or a fragment. A valid release with the same or older version/build means the app is up to date, even if that release has expired or requires a different OS. A strictly newer release is rejected if it has expired or requires a newer OS. Unsupported schema versions are errors. The release ID provides stable identity for prompt suppression; the app does not download the IPA to verify the hash. iOS verifies the signed package.

The publisher uses the earliest expiration among included provisioning profiles. It writes files in this order:

1. A complete `releases/<id>/` directory containing `app.ipa`, `manifest.plist`, and release metadata.
2. The bootstrap installation page, `index.html`.
3. The new `feed.json` using atomic replacement on the same filesystem.

Remote upload tools must preserve that order. Cache immutable release URLs; revalidate `feed.json` and `index.html`. Keep previous release assets available for downloads already in progress.

The installation manifest is an Apple property list with a `software-package` asset pointing to the HTTPS IPA and metadata containing its bundle identifier, version, title, and `software` kind. The handoff URL is `itms-services://?action=download-manifest&url=<percent-encoded HTTPS manifest URL>`.

Feed authentication is a host concern. A custom Swift loader may authenticate the feed separately, but the system installer must be able to fetch the manifest and IPA without borrowing the app's URLSession credentials. Do not store secrets in release notes or client logs.
