#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "usage: $0 OUTPUT_DIRECTORY" >&2
    exit 64
fi

output_directory=$1
case "$output_directory" in
    /*) ;;
    *) output_directory="$(pwd)/$output_directory" ;;
esac

if [ -e "$output_directory" ] && [ "$(find "$output_directory" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "output directory must not exist or must be empty: $output_directory" >&2
    exit 1
fi

repository_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
work_directory=$(mktemp -d "${TMPDIR:-/tmp}/internal-app-updates-documentation.XXXXXX")
trap 'rm -rf "$work_directory"' EXIT HUP INT TERM

mkdir -p "$output_directory"
derived_data="$work_directory/DerivedData"
cd "$repository_root"
xcodebuild -quiet \
    -scheme InternalAppUpdates \
    -destination 'generic/platform=iOS' \
    -derivedDataPath "$derived_data" \
    docbuild

archive=$(find "$derived_data/Build/Products" -type d -name 'InternalAppUpdates.doccarchive' -print -quit)
if [ -z "$archive" ]; then
    echo 'xcodebuild did not produce InternalAppUpdates.doccarchive.' >&2
    exit 1
fi

static_site="$work_directory/InternalAppUpdates-documentation"
xcrun docc process-archive transform-for-static-hosting "$archive" --output-path "$static_site"
ditto -c -k --sequesterRsrc --keepParent "$archive" "$output_directory/InternalAppUpdates.doccarchive.zip"
ditto -c -k --sequesterRsrc --keepParent "$static_site" "$output_directory/InternalAppUpdates-documentation.zip"

echo "built DocC archives in $output_directory"
