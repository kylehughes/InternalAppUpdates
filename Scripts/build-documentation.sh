#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

site_output=false
if [[ "${1:-}" == "--site" ]]; then
  site_output=true
  shift
fi

if [[ "$#" -ne 1 || -z "$1" ]]; then
  printf 'usage: %s [--site] OUTPUT_DIRECTORY\n' "${0##*/}" >&2
  exit 64
fi

output_path="$1"
for command in xcodebuild xcrun ditto; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'error: %s is required to build documentation\n' "$command" >&2
    exit 69
  fi
done

if [[ -e "$output_path" ]] && [[ -n "$(find "$output_path" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  printf 'error: documentation output target must not exist or must be empty: %q\n' "$output_path" >&2
  exit 73
fi

mkdir -p "$output_path"
work_directory="$(mktemp -d "${TMPDIR:-/tmp}/internal-app-updates-documentation.XXXXXX")"
trap 'rm -rf "$work_directory"' EXIT HUP INT TERM

derived_data="$work_directory/DerivedData"
xcodebuild -quiet docbuild \
  -scheme InternalAppUpdates \
  -destination 'generic/platform=iOS' \
  -derivedDataPath "$derived_data" \
  CODE_SIGNING_ALLOWED=NO \
  SWIFT_TREAT_WARNINGS_AS_ERRORS=YES \
  OTHER_DOCC_FLAGS='--warnings-as-errors'

archive_path="$derived_data/Build/Products/Debug-iphoneos/InternalAppUpdates.doccarchive"
if [[ ! -d "$archive_path" ]]; then
  printf '::error::DocC archive was not created at %q\n' "$archive_path" >&2
  exit 1
fi

if [[ "$site_output" == true ]]; then
  static_site="$output_path"
else
  static_site="$work_directory/InternalAppUpdates-documentation"
fi

xcrun docc process-archive transform-for-static-hosting \
  "$archive_path" \
  --hosting-base-path InternalAppUpdates \
  --output-path "$static_site"

printf '%s\n' \
  '<!doctype html>' \
  '<html lang="en">' \
  '<head>' \
  '<meta charset="utf-8">' \
  '<meta http-equiv="refresh" content="0; url=documentation/internalappupdates/">' \
  '<link rel="canonical" href="https://kylehughes.github.io/InternalAppUpdates/documentation/internalappupdates/">' \
  '<title>Internal App Updates documentation</title>' \
  '</head>' \
  '<body>' \
  '<p><a href="documentation/internalappupdates/">Open the Internal App Updates documentation.</a></p>' \
  '</body>' \
  '</html>' > "$static_site/index.html"

for path in \
  "$static_site/index.html" \
  "$static_site/documentation/internalappupdates/index.html" \
  "$static_site/data/documentation/internalappupdates.json"; do
  if [[ ! -f "$path" ]]; then
    printf '::error::Expected static documentation file was not created at %q\n' "$path" >&2
    exit 1
  fi
done

if [[ "$site_output" == true ]]; then
  printf 'built static DocC site in %s\n' "$output_path"
  exit 0
fi

ditto -c -k --sequesterRsrc --keepParent \
  "$archive_path" \
  "$output_path/InternalAppUpdates.doccarchive.zip"
ditto -c -k --sequesterRsrc --keepParent \
  "$static_site" \
  "$output_path/InternalAppUpdates-documentation.zip"

if [[ "$(find "$output_path" -maxdepth 1 -type f -print | wc -l | tr -d ' ')" -ne 2 ]]; then
  printf '::error::Expected exactly two documentation ZIP files in %q\n' "$output_path" >&2
  exit 1
fi

printf 'built DocC archives in %s\n' "$output_path"
