# Publish through the AWS CLI

Copy `publish-s3.sh` and `upload-s3.py` into your app's scripts directory. They prepare a publication, upload its files to an existing S3 bucket in order, and verify the HTTPS endpoint before reporting success.

Install Internal App Updates from a checkout containing the deployment feature into a Python 3.11+ virtual environment, and activate that environment. Both the `internal-app-updates` command and Python's `internal_app_updates` module must be available. Install and authenticate the ordinary AWS CLI using your existing setup.

The scripts assume your bucket and HTTPS delivery endpoint already exist, and that the destination prefix maps exactly to the HTTPS base URL. They do not create infrastructure, change access policies, or manage credentials. See the [deployment guide](../../Documents/deployment.md) for HTTPS, caching, installer access, and concurrency requirements.

## Configure once

Set these values in your project wrapper or CI environment:

```sh
export OUTPUT_DIRECTORY="$PWD/Artifacts/updates-site"
export BASE_URL="https://updates.example/your-app/"
export S3_DESTINATION="s3://your-updates-bucket/your-app"
```

`OUTPUT_DIRECTORY` stores the channel's local publication history. Preserve it between releases, including when using ephemeral CI runners. The default report is beside that directory, at `Artifacts/updates-site.publication.json`. Set `PUBLICATION_REPORT` to choose another path outside the served directory. If necessary, set `INTERNAL_APP_UPDATES_PYTHON` to the Python interpreter where the package is installed.

Use your CI system's concurrency control or deployment lock to run one complete workflow at a time per app/channel, including across machines. The scripts do not provide a distributed lock or compare against the remote feed to prevent an older upload from replacing a newer one.

## Publish a build

After exporting a signed IPA with a new build number:

```sh
sh Scripts/publish-s3.sh /path/to/App.ipa
```

The wrapper saves the report only after local publication succeeds. The uploader validates every local file, then calls `aws s3 cp` for each entry with the report's Content-Type and Cache-Control. It uploads the feed last, stops on a failed command, and runs `internal-app-updates verify`. Successful verification prints the installation link and feed URL.

The AWS CLI uses its normal credentials, profile, and region configuration. Set `AWS_PROFILE` or your CI's credential mechanism as usual. The script never prints or stores those credentials in the publication report. The [AWS CLI copy reference](https://docs.aws.amazon.com/cli/latest/reference/s3/cp.html) documents the upload flags.

## Withdraw a published offer

To remove the current offer while preserving the release files, create a withdrawal report outside `OUTPUT_DIRECTORY` and pass it to the same uploader:

```sh
internal-app-updates withdraw \
  --output "$OUTPUT_DIRECTORY" \
  --base-url "$BASE_URL" \
  --json > Artifacts/updates-site.withdrawal.json
python3 Scripts/upload-s3.py \
  Artifacts/updates-site.withdrawal.json \
  "$S3_DESTINATION"
```

The report uploads `index.html` before `feed.json`, with `no-cache` metadata, and verifies both through the HTTPS endpoint. On success the uploader prints `Withdrawal verified.` with the page and feed URLs. Keep the report and its local files unchanged while retrying. Do not delete S3 release objects during a withdrawal.

## Retry an upload

Keep the report and prepared directory unchanged after a failure. If no newer deployment has advanced the channel, retry without rebuilding or republishing:

```sh
python3 Scripts/upload-s3.py \
  Artifacts/updates-site.publication.json \
  s3://your-updates-bucket/your-app
```

Use the same Python environment as the wrapper. Failed verification may mean the files uploaded but the host still serves stale bytes or incorrect headers. Diagnose the reported file before retrying. Keep previous release directories available for downloads in progress; these scripts never delete remote objects.

For another host, keep the same report and sequencing contract and replace the `aws s3 cp` invocation with your existing upload tool. There is no package-specific hook registration or cloud SDK dependency.
