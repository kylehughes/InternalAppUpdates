# Hosting update files

Publish into a dedicated directory for each app/channel. The output is portable: a JSON feed, an HTML bootstrap page, and immutable release directories. No database or server API is required.

## Private Tailscale Serve

`internal-app-updates serve --directory Site --tailscale --mount-path /your-app/` starts a loopback HTTP server and configures a private HTTPS proxy route. It requires an already connected Tailscale client, HTTPS availability for the tailnet, and permission to use Serve. If Tailscale requests account setup, complete that separately and rerun the command.

The route persists after the process exits. Keep the server alive while the feed is in use. Closing a terminal or sleeping the Mac can make an update unavailable. For regular use, run the command through your existing launchd or service-management setup. To restart only the backend, omit `--tailscale` and reuse the exact loopback address and port targeted by the route. For a route configured on port 8765:

```sh
internal-app-updates serve --directory Site --bind 127.0.0.1 --port 8765
```

The command refuses conflicting HTTPS routes and prints a path-specific removal command. It never uses `tailscale serve reset`, enables Funnel, or changes tailnet policy. Removing the route stops new downloads; it does not uninstall an app or remove a feed URL saved on a phone.

It checks foreground routes as well as background routes. If Funnel is already enabled on port 443, it refuses to configure or reuse the route: an existing matching route can be public, and changing that listener can affect other routes. Configure a separate private host or resolve that conflict before rerunning the command. The command validates the node's DNS name before writing a route.

The phone needs tailnet access to this host and HTTPS port. Tailnet connectivity protects access to the update artifacts. Signed apps may still contain internal code or development assets; choose the audience of the hosting endpoint accordingly.

## Another HTTPS host

Serve the output directory at the exact `--base-url` used for publication. An ordinary reverse proxy can forward to `internal-app-updates serve`; a static host can serve the files directly.

For repeatable uploads, use a project-owned script and the ordered metadata from `publish --json`. The [deployment guide](deployment.md) defines the report, retry behavior, and verification command; the [S3 example](../Examples/Deployment/README.md) uses the ordinary AWS CLI. The package does not run deployment hooks or configure cloud infrastructure.

Required behavior:

- Use a certificate trusted by the phone and preserve the full URL path.
- Return the IPA as `application/octet-stream`, plist as `text/xml`, and feed as `application/json`.
- Support ordinary GET requests. Byte-range support is recommended for IPA downloads.
- Revalidate `feed.json` and `index.html`; release directories can use long immutable cache lifetimes.
- Upload complete immutable release directories before replacing the feed.

Keep the system installer's network access in mind. A successful authenticated feed fetch inside the app says nothing about whether the separate installer can retrieve the IPA. An HTML login form is not an artifact endpoint. For short-lived signed URLs, both manifest and IPA must remain usable for the whole installation, and the feed must stop offering them before they expire. The built-in publisher uses stable HTTPS URLs, not expiring access tokens.

For local server testing, use HTTP on loopback. The Swift configuration requires `allowsInsecureLoopback: true` to accept a loopback HTTP feed. Installation manifests still require HTTPS. This test option does not make a development machine's `localhost` address reachable from a phone.

## Withdrawal and recovery

```sh
internal-app-updates withdraw --output /path/to/site/your-app
```

Withdrawal sets `release` to null and removes the bootstrap offer; it leaves immutable artifacts in place. A client clears the offer on its next successful check. A device that already opened the installer can still finish downloading the old artifact.

For an HTTPS host, create a withdrawal report outside the served directory and deploy it with the same project-owned uploader used for publication:

```sh
internal-app-updates withdraw \
  --output /path/to/site/your-app \
  --base-url https://updates.example/your-app/ \
  --json > /path/to/reports/withdrawal.json
```

The report contains only `index.html` and `feed.json`. Upload the page before the feed with their declared `no-cache` metadata, then verify the report through the endpoint. Do not delete old release directories during withdrawal.

To repair a bad build, publish a corrected build with a higher build number. Publishing an older build is intentionally rejected. To restore an accidentally interrupted publication, repeat the same publish command with the same IPA and base URL. Do not edit files inside an existing release directory.
